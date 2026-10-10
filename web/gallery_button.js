/* 顶栏按钮 + 工作流注入通道 + 图标替换。ComfyUI 通过 WEB_DIRECTORY 自动加载本文件。 */
import { app } from "../../scripts/app.js";

const PAGE_PATH = "/gallery";
const TOOLTIP = "打开图片画廊（Shift+点击弹出独立窗口）";
const NEW_WINDOW_FEATURES = "width=1400,height=900,resizable=yes,scrollbars=yes,status=yes";
const MIN_VERSION_FOR_ACTION_BAR = [1, 33, 9];
const MAX_ATTACH_ATTEMPTS = 120;
const CHANNEL_NAME = "comfyui-gallery";
const PENDING_KEY = "ig.pendingWorkflow";
const PENDING_MAX_AGE = 120000;
const ICON_PATH = "/gallery_static/icon.svg";

/* 与 static/icon.svg 保持同一份图形：卡片 + 太阳 + 山，颜色用 ComfyUI 的橙金 */
const LOGO_SVG = [
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="20" height="20" style="display:block">',
  '<defs><linearGradient id="igBtnGrad" x1="0" y1="0" x2="1" y2="1">',
  '<stop offset="0" stop-color="#ff7a45"/><stop offset="1" stop-color="#f5b301"/>',
  '</linearGradient></defs>',
  '<rect x="3" y="13" width="44" height="44" rx="11" fill="#7a2f10" opacity="0.35"/>',
  '<rect x="13" y="5" width="48" height="48" rx="13" fill="url(#igBtnGrad)"/>',
  '<circle cx="28" cy="21" r="5.2" fill="#ffffff" opacity="0.95"/>',
  '<path d="M18 47 L32 30 L42 42 L47 36 L56 47 Z" fill="#ffffff" opacity="0.95"/>',
  '</svg>',
].join("");

function openGallery(event) {
  const url = window.location.origin + PAGE_PATH;
  if (event && event.shiftKey) {
    window.open(url, "_blank", NEW_WINDOW_FEATURES);
    return;
  }
  window.open(url, "_blank");
}

function parseVersion(value) {
  const parts = String(value || "0.0.0").replace(/^v/i, "").split("-")[0].split(".").map(function (n) {
    return parseInt(n, 10) || 0;
  });
  while (parts.length < 3) parts.push(0);
  return parts;
}

function compareVersions(a, b) {
  const va = parseVersion(a), vb = parseVersion(b);
  for (let i = 0; i < 3; i++) {
    if (va[i] > vb[i]) return 1;
    if (va[i] < vb[i]) return -1;
  }
  return 0;
}

async function frontendVersion() {
  try {
    if (window.__COMFYUI_FRONTEND_VERSION__) return window.__COMFYUI_FRONTEND_VERSION__;
  } catch (error) { /* ignore */ }
  try {
    const res = await fetch("/system_stats");
    const data = await res.json();
    const sys = (data && data.system) || {};
    return sys.comfyui_frontend_version || sys.required_frontend_version || "0.0.0";
  } catch (error) {
    return "0.0.0";
  }
}

function isGalleryButton(element) {
  const label = (element.getAttribute("aria-label") || "") + " " + (element.getAttribute("title") || "");
  return element.tagName === "BUTTON" && (label.indexOf("画廊") >= 0 || label.indexOf("Gallery") >= 0);
}

function paintIcon(button) {
  const holder = button.querySelector(".comfyui-button-icon, .icon, span") || button;
  const already = holder.querySelector("svg[data-ig-logo]");
  if (already) return;
  const wrapper = document.createElement("span");
  wrapper.innerHTML = LOGO_SVG;
  const svg = wrapper.firstChild;
  svg.setAttribute("data-ig-logo", "1");
  holder.innerHTML = "";
  holder.appendChild(svg);
}

/* 新前端的按钮由 Vue 渲染，重绘会把图标换回字体图标，所以轮询 + MutationObserver 保活 */
function keepLogoInActionBar() {
  let attempts = 0;
  const scan = function () {
    const bar = document.querySelector("[data-testid='action-bar-buttons']")
      || document.querySelector(".actionbar-container")
      || document.querySelector(".comfyui-menu")
      || document.body;
    const buttons = Array.prototype.slice.call(bar.querySelectorAll("button")).filter(isGalleryButton);
    if (buttons.length) {
      buttons.forEach(paintIcon);
      if (!window.__igLogoObserver) {
        const observer = new MutationObserver(function () {
          Array.prototype.slice.call(bar.querySelectorAll("button")).filter(isGalleryButton).forEach(function (element) {
            if (!element.querySelector("svg[data-ig-logo]")) paintIcon(element);
          });
        });
        observer.observe(bar, { childList: true, subtree: true });
        window.__igLogoObserver = observer;
      }
      return;
    }
    if (attempts++ < MAX_ATTACH_ATTEMPTS) requestAnimationFrame(scan);
  };
  requestAnimationFrame(scan);
}

function notice(message, ok) {
  try {
    if (app.extensionManager && app.extensionManager.toast && app.extensionManager.toast.add) {
      app.extensionManager.toast.add({
        severity: ok ? "success" : "warn",
        summary: "Image Gallery",
        detail: message,
        life: 6000,
      });
      return;
    }
  } catch (error) { /* fall through */ }
  try {
    if (app.ui && app.ui.dialog && app.ui.dialog.show) { app.ui.dialog.show(message); return; }
  } catch (error) { /* fall through */ }
  console.log("[ImageGallery]", message);
}

async function loadWorkflowPayload(payload) {
  if (!payload || !payload.workflow) return;
  let graph = null;
  try {
    graph = JSON.parse(payload.workflow);
  } catch (error) {
    notice("工作流 JSON 解析失败：" + error.message, false);
    return;
  }
  const hasGraph = app.graph && app.graph._nodes && app.graph._nodes.length > 0;
  const apply = function () {
    try {
      app.loadGraphData(graph);
      const missing = (app.graph && app.graph._nodes ? app.graph._nodes : []).filter(function (item) {
        return item.has_errors || item.type === "MissingNode";
      }).length;
      notice("已载入 " + (payload.name || "图片工作流") + (missing ? "，有 " + missing + " 个节点缺失（需安装对应插件）" : ""), true);
    } catch (error) {
      notice("载入失败：" + error.message, false);
    }
  };
  if (!hasGraph) { apply(); return; }
  try {
    const confirmed = await app.ui.dialog.confirm("用《" + (payload.name || "该图片") + "》的工作流覆盖当前画布？");
    if (confirmed) apply();
  } catch (error) {
    apply();
  }
}

function consumePending() {
  let payload = null;
  try {
    const raw = localStorage.getItem(PENDING_KEY);
    if (raw) {
      payload = JSON.parse(raw);
      localStorage.removeItem(PENDING_KEY);
    }
  } catch (error) { return; }
  if (!payload || !payload.at || Date.now() - payload.at > PENDING_MAX_AGE) return;
  setTimeout(function () { loadWorkflowPayload(payload); }, 800);
}

function listenChannel() {
  try {
    const channel = new BroadcastChannel(CHANNEL_NAME);
    channel.onmessage = function (event) {
      const payload = event && event.data;
      if (!payload || payload.type !== "load-workflow") return;
      try { localStorage.removeItem(PENDING_KEY); } catch (error) { /* ignore */ }
      loadWorkflowPayload(payload);
    };
  } catch (error) {
    console.warn("[ImageGallery] BroadcastChannel unavailable:", error);
  }
}

function attachLegacyButton() {
  import("../../scripts/ui/components/button.js").then(function (mod) {
    const ComfyButton = mod.ComfyButton;
    const button = new ComfyButton({
      icon: "pi pi-images",
      tooltip: TOOLTIP,
      app: app,
      enabled: true,
      classList: "comfyui-button comfyui-menu-mobile-collapse primary",
    });
    button.element.setAttribute("aria-label", TOOLTIP);
    button.element.id = "image-gallery-top-button";
    button.element.addEventListener("click", openGallery);

    let attempts = 0;
    const attach = function () {
      const host = document.querySelector(".comfyui-menu")
        || document.querySelector("[data-testid='action-bar-buttons']")
        || document.querySelector(".actionbar-container");
      if (host) {
        host.appendChild(button.element);
        keepLogoInActionBar();
        return;
      }
      if (attempts++ < MAX_ATTACH_ATTEMPTS) requestAnimationFrame(attach);
    };
    attach();
  }).catch(function (error) {
    console.warn("[ImageGallery] legacy button unavailable:", error);
  });
}

(async function () {
  const version = await frontendVersion();
  const useActionBar = compareVersions(version, MIN_VERSION_FOR_ACTION_BAR) >= 0;
  const extension = {
    name: "ImageGallery.TopMenu",
    async setup() {
      listenChannel();
      consumePending();
      if (!useActionBar) attachLegacyButton();
      keepLogoInActionBar();
    },
  };
  if (useActionBar) {
    extension.actionBarButtons = [
      { icon: "icon-[lucide--images] size-4", tooltip: TOOLTIP, onClick: openGallery },
    ];
  }
  app.registerExtension(extension);
  console.info("[ImageGallery] ready (actionBar=" + useActionBar + ", frontend=" + version + ", icon=" + ICON_PATH + ")");
})();
