/* 顶栏按钮 + 工作流注入通道 + 图标替换。ComfyUI 通过 WEB_DIRECTORY 自动加载本文件。 */
import { app } from "../../scripts/app.js";

const PAGE_PATH = "/gallery";
const TOOLTIP = "打开图片画廊（Shift+点击弹出独立窗口）";
const NEW_WINDOW_FEATURES = "width=1400,height=900,resizable=yes,scrollbars=yes,status=yes";
const MAX_ATTACH_ATTEMPTS = 120;
const ACTION_BAR_SELECTORS = "[data-testid='action-bar-buttons'], .actionbar-container";
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

function actionBarHost() {
  return document.querySelector(ACTION_BAR_SELECTORS);
}

/* 前端不暴露版本号（/system_stats 与 index.html 都没有），所以按 DOM 判断：
   有 action bar 就用声明式按钮，若干帧后仍没有才回退老按钮。 */
function scheduleLegacyFallback() {
  let attempts = 0;
  const probe = function () {
    if (actionBarHost()) return;
    if (attempts++ < MAX_ATTACH_ATTEMPTS) { requestAnimationFrame(probe); return; }
    attachLegacyButton();
  };
  requestAnimationFrame(probe);
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
    const bar = actionBarHost();
    if (!bar) {
      if (attempts++ < MAX_ATTACH_ATTEMPTS) requestAnimationFrame(scan);
      return;
    }
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
  // 第 4 个参数（工作流名）必须给：老前端（1.45.x）的 afterLoadNewGraph 在"没名字"时
  // 会额外建一个名为 Unsaved Workflow 的空工作流标签页（实测每次 +1），给了名字则只开
  // 一个以图片名命名的标签页。
  const tabName = String(payload.name || "图片工作流").replace(/\.[^.]+$/, "");
  const apply = function () {
    try {
      app.loadGraphData(graph, true, true, tabName);
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
      const host = document.querySelector(".comfyui-menu");
      if (host) {
        host.appendChild(button.element);
        paintIcon(button.element);
        return;
      }
      if (attempts++ < MAX_ATTACH_ATTEMPTS) requestAnimationFrame(attach);
    };
    attach();
  }).catch(function (error) {
    console.warn("[ImageGallery] legacy button unavailable:", error);
  });
}

app.registerExtension({
  name: "ImageGallery.TopMenu",
  actionBarButtons: [
    { icon: "icon-[lucide--images] size-4", tooltip: TOOLTIP, onClick: openGallery },
  ],
  async setup() {
    listenChannel();
    consumePending();
    keepLogoInActionBar();
    scheduleLegacyFallback();
  },
});
