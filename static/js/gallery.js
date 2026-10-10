/* ComfyUI-ImageGallery 前端：原生 ES module，零构建。
   结构：状态 -> 事件绑定 -> 虚拟滚动网格 -> 详情抽屉 -> 右键菜单 -> 模态框。 */

const API = (document.body.dataset.apiBase || '/api/gallery').replace(/\/$/, '');
const VIEWER_ID = 'viewer-' + Math.random().toString(36).slice(2);
const CARD_W = 168;
const CARD_H = 196;
const GAP = 10;
const PAGE = 120;

const state = {
  root: 'output',
  dir: '',
  q: '',
  models: [],
  modelMode: 'any',
  sort: 'mtime_desc',
  showDrafts: (function () { try { return localStorage.getItem('ig.showDrafts') === '1'; } catch (error) { return false; } })(),
  favoriteOnly: false,
  tags: [],
  tagMode: 'any',
  hiddenDrafts: 0,
  items: [],
  total: 0,
  cursor: '',
  hasMore: true,
  loading: false,
  selected: new Set(),
  selectMode: false,
  detailId: 0,
  detail: null,
  cols: 1,
  cardWidth: (function () { try { return Number(localStorage.getItem('ig.cardWidth')) || 168; } catch (error) { return 168; } })(),
  thumbH: 130,
  cardH: 196,
  gapX: 10,
  lastWrapWidth: 0,
  observerTimer: 0,
  geometrySignature: '',
  activeId: 0,
  mode: 'fts',
  lang: (function () { try { return localStorage.getItem('ig.lang') || 'zh'; } catch (error) { return 'zh'; } })(),
  searchHistory: (function () { try { return JSON.parse(localStorage.getItem('ig.searchHistory') || '[]'); } catch (error) { return []; } })(),
  nodes: new Map(),
  timer: 0,
};

const el = {};
['ig-badge', 'ig-status', 'ig-dir-list', 'ig-dir-filter', 'ig-search', 'ig-model-btn', 'ig-sort',
 'ig-count', 'ig-select-mode', 'ig-bulkbar', 'ig-bulk-text', 'ig-bulk-save', 'ig-bulk-delete',
 'ig-bulk-all', 'ig-bulk-clear', 'ig-grid', 'ig-grid-wrap', 'ig-empty', 'ig-loading',
 'ig-drawer', 'ig-drawer-title', 'ig-drawer-body', 'ig-drawer-close', 'ig-prev', 'ig-next',
 'ig-menu', 'ig-overlay', 'ig-modal-title', 'ig-modal-body', 'ig-modal-foot', 'ig-modal-close',
 'ig-toasts', 'ig-scan', 'ig-trash', 'ig-settings', 'ig-drafts', 'ig-fav', 'ig-tag-btn',
 'ig-lightbox', 'ig-lightbox-img', 'ig-lightbox-name', 'ig-lightbox-zoom', 'ig-lb-prev', 'ig-lb-next', 'ig-lb-close', 'ig-lb-detail',
 'ig-sidebar', 'ig-sidebar-resize', 'ig-sidebar-toggle', 'ig-thumb-size',
 'ig-lang', 'ig-regex', 'ig-bulk-compare', 'ig-bulk-rename', 'ig-bulk-move', 'ig-search-history',
 'ig-sidebar-toggle-top', 'ig-update', 'ig-update-reload'].forEach(function (id) {
  el[id] = document.getElementById(id);
});

/* ---------------- 基础工具 ---------------- */

function node(tag, attrs, children) {
  const element = document.createElement(tag);
  if (attrs) {
    Object.keys(attrs).forEach(function (key) {
      const value = attrs[key];
      if (value === null || value === undefined) return;
      if (key === 'class') element.className = value;
      else if (key === 'text') element.textContent = value;
      else if (key === 'html') element.innerHTML = value;
      else if (key.indexOf('on') === 0 && typeof value === 'function') element.addEventListener(key.slice(2), value);
      else element.setAttribute(key, value);
    });
  }
  (children || []).forEach(function (child) {
    if (child === null || child === undefined) return;
    element.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
  });
  return element;
}

function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '-';
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

function fmtDuration(seconds) {
  const total = Math.round(seconds || 0);
  if (!total) return '';
  return Math.floor(total / 60) + ':' + ('0' + (total % 60)).slice(-2);
}

function fmtTime(seconds) {
  if (!seconds) return '-';
  const date = new Date(seconds * 1000);
  const pad = function (value) { return value < 10 ? '0' + value : '' + value; };
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' +
    pad(date.getHours()) + ':' + pad(date.getMinutes());
}

function toast(message, kind) {
  const item = node('div', { class: 'ig-toast' + (kind ? ' is-' + kind : ''), text: message });
  el['ig-toasts'].appendChild(item);
  setTimeout(function () { item.remove(); }, kind === 'error' ? 6000 : 3200);
}

async function api(path, options) {
  const response = await fetch(API + path, Object.assign({
    headers: { 'content-type': 'application/json', 'accept': 'application/json' },
  }, options || {}));
  const text = await response.text();
  let data = {};
  try { data = text ? JSON.parse(text) : {}; } catch (error) { data = { raw: text }; }
  if (!response.ok) {
    const detail = data && (data.error || data.message || data.raw) || response.status;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function post(path, body) {
  return api(path, { method: 'POST', body: JSON.stringify(body || {}) });
}

/* ---------------- 模态框与菜单 ---------------- */

function openModal(title, bodyNode, buttons) {
  el['ig-modal-title'].textContent = title;
  el['ig-modal-body'].innerHTML = '';
  el['ig-modal-body'].appendChild(bodyNode);
  el['ig-modal-foot'].innerHTML = '';
  (buttons || []).forEach(function (spec) {
    el['ig-modal-foot'].appendChild(node('button', {
      class: 'ig-btn' + (spec.primary ? ' is-primary' : ''),
      text: spec.label,
      onclick: spec.onClick,
    }));
  });
  el['ig-overlay'].hidden = false;
}

function closeModal() { el['ig-overlay'].hidden = true; }

function openMenu(items, x, y) {
  const menu = el['ig-menu'];
  menu.innerHTML = '';
  items.forEach(function (item) {
    if (item === '-') { menu.appendChild(node('hr')); return; }
    menu.appendChild(node('button', { text: item.label, onclick: function () { closeMenu(); item.onClick(); } }));
  });
  menu.hidden = false;
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  menu.style.left = Math.min(x, window.innerWidth - width - 8) + 'px';
  menu.style.top = Math.min(y, window.innerHeight - height - 8) + 'px';
}

function closeMenu() { el['ig-menu'].hidden = true; }

/* ---------------- 数据加载 ---------------- */

function currentQueryBody(extra) {
  return Object.assign({
    root: state.root,
    dir: state.dir,
    dir_prefix: state.dir ? '' : '',
    q: state.q,
    mode: state.mode,
    lang: state.lang,
    lang: state.lang,
    models: state.models,
    model_mode: state.modelMode,
    tags: state.tags,
    tag_mode: state.tagMode,
    show_drafts: state.showDrafts,
    favorite_only: state.favoriteOnly,
    sort: state.sort,
    limit: PAGE,
  }, extra || {});
}

async function loadDirs() {
  try {
    const data = await api('/dirs?root=' + encodeURIComponent(state.root) + '&show_drafts=' + (state.showDrafts ? '1' : '0'));
    state.hiddenDrafts = data.hidden_drafts || 0;
    refreshFilterButtons();
    const list = el['ig-dir-list'];
    list.innerHTML = '';
    const filter = (el['ig-dir-filter'].value || '').toLowerCase();
    const all = node('div', {
      class: 'ig-dir' + (state.dir ? '' : ' is-active'),
      onclick: function () { state.dir = ''; loadDirs(); reload(); },
    }, [node('span', { text: '全部目录' }), node('small', { text: String(data.dirs.reduce(function (sum, item) { return sum + item.count; }, 0)) })]);
    list.appendChild(all);
    data.dirs.forEach(function (item) {
      if (filter && item.name.toLowerCase().indexOf(filter) < 0) return;
      const row = node('div', {
        class: 'ig-dir' + (state.dir === item.dir ? ' is-active' : ''),
        title: item.dir,
        onclick: function () { state.dir = item.dir; loadDirs(); reload(); },
      }, [node('span', { text: item.name, title: item.dir }), node('small', { text: String(item.count) })]);
      list.appendChild(row);
    });
  } catch (error) {
    toast('目录加载失败：' + error.message, 'error');
  }
}

async function reload() {
  state.items = [];
  state.cursor = '';
  state.hasMore = true;
  state.total = 0;
  state.nodes.forEach(function (entry) { entry.remove(); });
  state.nodes.clear();
  el['ig-grid'].style.height = '0px';
  await loadMore();
}

async function loadMore() {
  if (state.loading || !state.hasMore) return;
  state.loading = true;
  el['ig-loading'].hidden = false;
  try {
    const data = await api('/query', { method: 'POST', body: JSON.stringify(currentQueryBody({ cursor: state.cursor })) });
    state.total = data.total;
    state.items = state.items.concat(data.items);
    state.cursor = data.next_cursor || '';
    state.hasMore = Boolean(data.next_cursor);
    layout();
    updateCount();
    el['ig-empty'].hidden = state.items.length > 0;
    if (!state.items.length) el['ig-empty'].textContent = '当前筛选没有图片';
  } catch (error) {
    toast('加载失败：' + error.message, 'error');
  } finally {
    state.loading = false;
    el['ig-loading'].hidden = true;
  }
}

function updateCount() {
  el['ig-count'].textContent = t('count', { a: state.items.length, b: state.total }) +
    (state.dir ? '（' + state.dir.split('\\').pop() + '）' : '');
}

function updateBulkText() {
  if (el['ig-bulk-text']) el['ig-bulk-text'].textContent = t('selected', { n: state.selected.size });
}

/* ---------------- 虚拟滚动网格 ---------------- */

function computeLayout() {
  const wrap = el['ig-grid-wrap'];
  state.lastWrapWidth = wrap.clientWidth;
  const available = Math.max(240, wrap.clientWidth - 2 * GAP);
  const cardW = state.cardWidth;
  const thumbH = Math.round(cardW * 0.78);
  const cardH = thumbH + 66;
  const cols = Math.max(1, Math.floor((available + GAP) / (cardW + GAP)));
  // 不足一张图的余量均匀摊到列间隙里，避免留白全堆在右侧
  const gapX = cols > 1 ? Math.max(GAP, (available - cols * cardW) / (cols - 1)) : 0;
  state.cols = cols;
  state.thumbH = thumbH;
  state.cardH = cardH;
  state.gapX = gapX;
  const totalWidth = cols * cardW + (cols - 1) * gapX;
  const rows = Math.ceil(state.items.length / cols);
  const grid = el['ig-grid'];
  grid.style.position = 'absolute';
  grid.style.top = GAP + 'px';
  grid.style.left = GAP + 'px';
  grid.style.height = Math.max(0, rows * (cardH + GAP) - GAP) + 'px';
  grid.style.width = Math.round(totalWidth) + 'px';
}

/* 几何变化（列数/卡片尺寸/间距）后必须重建卡片：卡片的 left/top 是内联写死的，
   旧卡片不会自己挪位，否则会出现重叠。 */
function applyGeometryIfChanged() {
  const wrap = el['ig-grid-wrap'];
  if (state.lastWrapWidth !== wrap.clientWidth) computeLayout();
  const signature = [state.cols, state.cardWidth, Math.round(state.gapX), state.cardH].join(':');
  if (state.geometrySignature === signature) return false;
  state.geometrySignature = signature;
  state.nodes.forEach(function (element) { element.remove(); });
  state.nodes.clear();
  return true;
}

function layout() {
  computeLayout();
  applyGeometryIfChanged();
  renderGrid();
}

function diffChip(item) {
  const verdict = item.diff_verdict;
  if (!verdict) return null;
  const label = { start: '工作流起点', new: '换了工作流', weak: '结构差异较大', same: '与上一张同源' }[verdict] || verdict;
  return node('span', { class: 'ig-chip is-' + verdict, text: label, title: item.diff_summary || '' });
}

function cardNode(item) {
  const index = state.items.indexOf(item);
  const col = index % state.cols;
  const row = Math.floor(index / state.cols);
  const card = node('div', {
    class: 'ig-card' + (state.selected.has(item.id) ? ' is-selected' : '')
      + (state.activeId === item.id ? ' is-active' : ''),
    style: 'left:' + Math.round(col * (state.cardWidth + state.gapX)) + 'px'
      + ';top:' + Math.round(row * (state.cardH + GAP)) + 'px'
      + ';width:' + state.cardWidth + 'px;height:' + state.cardH + 'px',
    title: item.path || item.name,
  });
  const img = node('img', { class: 'ig-card-thumb', loading: 'lazy', alt: item.name, style: 'height:' + state.thumbH + 'px' });
  img.src = API + '/thumb?id=' + item.id + '&tier=256';
  card.appendChild(img);
  if (item.kind === 'video') {
    const badge = item.duration ? ('视频 ' + fmtDuration(item.duration)) : '视频';
    card.appendChild(node('span', { class: 'ig-chip', text: badge, style: 'position:absolute;right:6px;top:26px' }));
  }
  const star = node('span', {
    class: 'ig-card-star' + (item.favorite ? ' is-on' : ''),
    text: item.favorite ? '★' : '☆',
    title: '收藏',
  });
  star.addEventListener('click', function (event) {
    event.stopPropagation();
    toggleFavorite(item.id, !item.favorite);
  });
  card.appendChild(star);
  const body = node('div', { class: 'ig-card-body' }, [
    node('div', { class: 'ig-card-name', text: item.name }),
    node('div', { class: 'ig-card-meta' }, [
      node('span', { text: item.width && item.height ? item.width + '×' + item.height : (item.kind || '') }),
      node('span', { text: fmtSize(item.size) }),
    ]),
  ]);
  const chip = diffChip(item);
  if (chip) body.appendChild(chip);
  card.appendChild(body);
  if (state.selectMode) {
    const box = node('input', { type: 'checkbox', class: 'ig-card-check' });
    box.checked = state.selected.has(item.id);
    box.addEventListener('click', function (event) {
      event.stopPropagation();
      toggleSelect(item.id, box.checked);
    });
    card.appendChild(box);
  }
  card.dataset.diff = String(item.diff_verdict || '');
  card.addEventListener('click', function (event) {
    if (state.selectMode) { toggleSelect(item.id, !state.selected.has(item.id)); return; }
    if (event.ctrlKey || event.metaKey) { toggleSelect(item.id, true); return; }
    openDetail(item.id);
  });
  card.addEventListener('dblclick', function (event) {
    event.stopPropagation();
    openLightbox(item.id);
  });
  card.addEventListener('contextmenu', function (event) {
    event.preventDefault();
    openCardMenu(item, event.clientX, event.clientY);
  });
  return card;
}

function renderGrid() {
  const wrap = el['ig-grid-wrap'];
  applyGeometryIfChanged();
  const grid = el['ig-grid'];
  const rowH = state.cardH + GAP;
  const scrollTop = wrap.scrollTop;
  const viewH = wrap.clientHeight;
  const firstRow = Math.max(0, Math.floor(scrollTop / rowH) - 2);
  const lastRow = Math.ceil((scrollTop + viewH) / rowH) + 2;
  const wanted = [];
  for (let row = firstRow; row < lastRow; row += 1) {
    for (let col = 0; col < state.cols; col += 1) {
      const index = row * state.cols + col;
      if (index >= state.items.length) break;
      wanted.push(index);
    }
  }

  // 先收集再删除，避免在 forEach 里改 Map；内容签名变化（diff/收藏/解析状态）也触发重建
  const stale = [];
  state.nodes.forEach(function (element, index) {
    const item = state.items[index];
    if (wanted.indexOf(index) < 0 || !item || element.dataset.sig !== itemSignature(item)) stale.push(index);
  });
  stale.forEach(function (index) {
    const element = state.nodes.get(index);
    if (element) element.remove();
    state.nodes.delete(index);
  });

  const fragment = document.createDocumentFragment();
  wanted.forEach(function (index) {
    if (state.nodes.has(index)) return;
    const item = state.items[index];
    const element = cardNode(item);
    element.dataset.sig = itemSignature(item);
    state.nodes.set(index, element);
    fragment.appendChild(element);
  });
  if (fragment.childNodes.length) grid.appendChild(fragment);
}

function itemSignature(item) {
  return [item.id, item.diff_verdict || '', item.favorite ? 1 : 0, item.meta_state || '', state.activeId === item.id ? 1 : 0].join(':');
}

function setActive(fileId, scroll) {
  if (!fileId) return;
  const changed = state.activeId !== fileId;
  state.activeId = fileId;
  state.nodes.forEach(function (element, index) {
    const entry = state.items[index];
    if (entry) element.classList.toggle('is-active', entry.id === fileId);
  });
  if (scroll && changed) scrollActiveIntoView();
}

function scrollActiveIntoView() {
  const index = state.items.findIndex(function (entry) { return entry.id === state.activeId; });
  if (index < 0) return;
  const wrap = el['ig-grid-wrap'];
  const row = Math.floor(index / state.cols);
  const top = row * (state.cardH + GAP);
  if (top < wrap.scrollTop + 16 || top + state.cardH > wrap.scrollTop + wrap.clientHeight - 16) {
    wrap.scrollTo({ top: Math.max(0, top - wrap.clientHeight / 2 + state.cardH / 2), behavior: 'smooth' });
  }
}

function toggleSelect(id, value) {
  if (value) state.selected.add(id); else state.selected.delete(id);
  if (!state.selectMode && state.selected.size) state.selectMode = true;
  el['ig-select-mode'].classList.toggle('is-primary', state.selectMode);
  el['ig-bulkbar'].hidden = state.selected.size === 0;
  updateBulkText();
  // 就地更新可见卡片的选中态与复选框，避免整块重建
  state.nodes.forEach(function (element, index) {
    const entry = state.items[index];
    if (!entry) return;
    element.classList.toggle('is-selected', state.selected.has(entry.id));
    const box = element.querySelector('input[type=checkbox]');
    if (box) box.checked = state.selected.has(entry.id);
  });
  layout();
}

/* ---------------- 详情抽屉 ---------------- */

async function openDetail(fileId) {
  if (!fileId) return;
  state.detailId = fileId;
  try {
    const data = await api('/detail?id=' + fileId + '&lang=' + state.lang);
    state.detail = data;
    renderDetail(data);
    el['ig-drawer'].hidden = false;
    setActive(fileId, true);
  } catch (error) {
    toast('详情加载失败：' + error.message, 'error');
  }
}

function renderDetail(data) {
  const item = data.file;
  const body = el['ig-drawer-body'];
  el['ig-drawer-title'].textContent = item.name;
  body.innerHTML = '';
  const preview = node('img', { class: 'ig-preview', alt: item.name, title: '双击放大到页面内最大化' });
  preview.src = API + '/raw?id=' + item.id;
  preview.addEventListener('dblclick', function () { openLightbox(item.id); });
  body.appendChild(preview);
  body.appendChild(node('dl', { class: 'ig-kv' }, [
    node('dt', { text: '路径' }), node('dd', { text: data.path, title: data.path }),
    node('dt', { text: '尺寸' }), node('dd', { text: (item.width && item.height ? item.width + '×' + item.height : '-') }),
    node('dt', { text: '大小' }), node('dd', { text: fmtSize(item.size) }),
    node('dt', { text: '时间' }), node('dd', { text: fmtTime(item.mtime) }),
    node('dt', { text: '节点数' }), node('dd', { text: String(item.node_count || 0) }),
    node('dt', { text: '来源' }), node('dd', { text: data.meta.source || 'unknown' }),
    node('dt', { text: '家族' }), node('dd', { text: data.family ? ('#' + data.family.id + '（' + data.family.count + ' 张）') : '-' }),
  ]));

  if (data.meta.models && data.meta.models.length) {
    body.appendChild(node('div', { class: 'ig-section-title', text: '模型（点击筛选）' }));
    const row = node('div', { class: 'ig-chiprow' });
    data.meta.models.forEach(function (name) {
      row.appendChild(node('span', {
        class: 'ig-model-chip', text: name, title: '按该模型筛选',
        onclick: function () { state.models = [name]; state.modelMode = 'any'; openDetail(state.detailId); reload(); },
      }));
    });
    body.appendChild(row);
  }

  if (data.diff) {
    body.appendChild(node('div', { class: 'ig-section-title', text: '与上一张的差异' }));
    const box = node('div', { class: 'ig-diff' });
    box.appendChild(node('div', { text: data.diff.summary || '（无摘要）' }));
    box.appendChild(node('div', {
      class: 'ig-muted',
      text: '相似度 ' + (data.diff.score !== null && data.diff.score !== undefined ? data.diff.score.toFixed(3) : '-') +
        '（结构 ' + (data.diff.s1 || 0).toFixed(2) + ' / 连线 ' + (data.diff.s2 || 0).toFixed(2) +
        ' / 参数面 ' + (data.diff.s3 || 0).toFixed(2) + ' / 文本 ' + (data.diff.s4 || 0).toFixed(2) + '）',
    }));
    const detail = data.diff.detail || {};
    const list = node('ul');
    (detail.changed || []).slice(0, 12).forEach(function (change) {
      let text = change.node + '.' + change.key + ': ';
      if (change.kind === 'text') {
        text += '文本 ' + change.from_len + ' → ' + change.to_len + ' 字符';
        const fragments = change.fragments || [];
        if (fragments.length && fragments[0].added) text += '（新增：' + fragments[0].added.slice(0, 40) + '）';
      } else if (change.kind === 'number') {
        text += change.from + ' → ' + change.to + (change.percent !== undefined ? '（' + change.percent + '%）' : '');
      } else {
        text += String(change.from).slice(0, 24) + ' → ' + String(change.to).slice(0, 24);
      }
      list.appendChild(node('li', { text: text }));
    });
    if (list.childNodes.length) box.appendChild(list);
    body.appendChild(box);
  }

  if (data.older_id) {
    body.appendChild(node('div', { class: 'ig-section-title', text: '与上一张并排对比（更早的一张在右）' }));
    const row = node('div', { class: 'ig-compare' });
    const before = node('img', { alt: '上一张' });
    before.src = API + '/raw?id=' + data.older_id;
    before.title = '双击放大';
    before.addEventListener('dblclick', function () { openLightbox(data.older_id); });
    const current = node('img', { alt: '当前' });
    current.src = API + '/raw?id=' + item.id;
    current.title = '双击放大';
    current.addEventListener('dblclick', function () { openLightbox(item.id); });
    row.appendChild(node('figure', {}, [before, node('figcaption', { text: '上一张 #' + data.older_id })]));
    row.appendChild(node('figure', {}, [current, node('figcaption', { text: '当前 #' + item.id })]));
    body.appendChild(row);
  }

  body.appendChild(node('div', { class: 'ig-section-title', text: '标签（逗号分隔，回车保存）' }));
  const tagRow = node('div', { class: 'ig-chiprow' });
  (data.tags || []).forEach(function (name) {
    tagRow.appendChild(node('span', {
      class: 'ig-tag-chip', text: name, title: '点击按该标签筛选',
      onclick: function () { state.tags = [name]; state.tagMode = 'any'; refreshFilterButtons(); loadDirs(); reload(); },
    }));
  });
  const tagInput = node('input', { class: 'ig-mini-input', style: 'width:100%', placeholder: '标签1, 标签2', value: (data.tags || []).join(', ') });
  tagInput.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') saveTags(item.id, tagInput.value);
  });
  body.appendChild(tagInput);
  if (tagRow.childNodes.length) body.appendChild(tagRow);

  if (data.meta.search_text) {
    body.appendChild(node('div', { class: 'ig-section-title', text: '可检索文本（工作流输入合并）' }));
    body.appendChild(node('div', { class: 'ig-text-block', text: data.meta.search_text }));
  }

  const actions = node('div', { class: 'ig-actions' });
  actions.appendChild(node('button', {
    class: 'ig-btn' + (data.favorite ? ' is-primary' : ''),
    text: data.favorite ? '★ 已收藏' : '☆ 收藏',
    onclick: function () { toggleFavorite(item.id, !data.favorite); },
  }));
  actions.appendChild(node('button', {
    class: 'ig-btn is-primary', text: '发送到 ComfyUI',
    onclick: function () { sendToComfyUI(item.id); },
  }));
  actions.appendChild(node('button', {
    class: 'ig-btn', text: '另存为…',
    onclick: function () { saveAs(item); },
  }));
  actions.appendChild(node('button', {
    class: 'ig-btn', text: '复制路径',
    onclick: function () { copyText(data.path); },
  }));
  actions.appendChild(node('button', {
    class: 'ig-btn is-danger', text: '删除',
    onclick: function () { removeItems([item.id]); },
  }));
  body.appendChild(actions);
  el['ig-prev'].disabled = !data.newer_id;
  el['ig-next'].disabled = !data.older_id;
}

function openCardMenu(item, x, y) {
  openMenu([
    { label: '打开详情', onClick: function () { openDetail(item.id); } },
    { label: '发送到 ComfyUI', onClick: function () { sendToComfyUI(item.id); } },
    { label: '另存为…', onClick: function () { saveAs(item); } },
    '-',
    { label: '复制文件路径', onClick: function () { copyText(item.path); } },
    { label: '按该目录筛选', onClick: function () { state.dir = item.dir; loadDirs(); reload(); } },
    { label: '加入多选', onClick: function () { toggleSelect(item.id, true); } },
    '-',
    { label: '删除（移入回收站）', onClick: function () { removeItems([item.id]); } },
  ], x, y);
}

function copyText(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(function () { toast('已复制', 'ok'); },
      function () { toast('复制失败（浏览器拒绝）', 'error'); });
  } else {
    toast(text);
  }
}

/* ---------------- 发送到 ComfyUI ---------------- */

async function sendToComfyUI(fileId) {
  try {
    const data = await post('/workflow', { id: fileId });
    if (!data.ok || !data.workflow) {
      toast(data.error || '该文件没有工作流元数据', 'error');
      return;
    }
    const payload = { type: 'load-workflow', name: data.name, workflow: data.workflow, at: Date.now() };
    try { localStorage.setItem('ig.pendingWorkflow', JSON.stringify(payload)); } catch (error) { /* ignore */ }
    let delivered = false;
    try {
      const channel = new BroadcastChannel('comfyui-gallery');
      channel.postMessage(payload);
      channel.close();
      delivered = true;
    } catch (error) { delivered = false; }
    toast(delivered ? '已发送到 ComfyUI 标签页（若没有打开会提示手动加载）' : '已暂存工作流，请在 ComfyUI 页面确认加载', 'ok');
  } catch (error) {
    toast('发送失败：' + error.message, 'error');
  }
}

/* ---------------- 另存为 / 删除 / 回收站 ---------------- */

async function saveAs(item) {
  const url = API + '/raw?id=' + item.id;
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({ suggestedName: item.name });
      const writable = await handle.createWritable();
      const response = await fetch(url);
      await response.body.pipeTo(writable);
      toast('已另存到所选位置', 'ok');
      return;
    } catch (error) {
      if (error && error.name === 'AbortError') return;
    }
  }
  window.open(API + '/download?id=' + item.id, '_blank');
}

function removeItems(ids) {
  const body = node('div', {}, [
    node('p', { text: '将把 ' + ids.length + ' 个文件移入回收站（可还原），不会直接删除。' }),
    node('p', { class: 'ig-muted', text: '回收站目录：' + (state.stats ? state.stats.trash_dir : '（设置里可查）') }),
  ]);
  openModal('确认删除', body, [
    { label: '取消', onClick: closeModal },
    {
      label: '移入回收站', primary: true,
      onClick: async function () {
        closeModal();
        try {
          const result = await post('/delete', { ids: ids, permanent: false });
          toast('已移入回收站（批次 ' + result.batch + '），可在回收站页还原', 'ok');
          state.selected.clear();
          toggleSelect(0, false);
          await loadDirs();
          await reload();
          el['ig-drawer'].hidden = true;
        } catch (error) {
          toast('删除失败：' + error.message, 'error');
        }
      },
    },
  ]);
}

async function openTrash() {
  try {
    const data = await api('/trash');
    const body = node('div');
    if (!data.batches.length) {
      body.appendChild(node('p', { class: 'ig-muted', text: '回收站是空的。' }));
    }
    data.batches.forEach(function (batch) {
      const row = node('div', { class: 'ig-field' }, [
        node('label', { text: batch.batch }),
        node('span', { text: batch.count + ' 个文件 · ' + fmtSize(batch.bytes) }),
        node('span', { class: 'ig-spacer' }),
        node('button', {
          class: 'ig-btn', text: '查看 / 挑选还原',
          onclick: function () { closeModal(); openTrashBatch(batch.batch); },
        }),
        node('button', {
          class: 'ig-btn', text: '全部还原',
          onclick: async function () {
            try {
              const result = await post('/trash/restore', { batch: batch.batch });
              toast('已还原 ' + result.restored.length + ' 个文件' + (result.failed.length ? '，' + result.failed.length + ' 个失败' : ''), 'ok');
              closeModal();
              await reload();
            } catch (error) {
              toast('还原失败：' + error.message, 'error');
            }
          },
        }),
      ]);
      body.appendChild(row);
    });
    openModal('回收站', body, [{ label: '关闭', onClick: closeModal }]);
  } catch (error) {
    toast('回收站读取失败：' + error.message, 'error');
  }
}

async function openSettings() {
  try {
    const stats = await api('/stats');
    state.stats = stats;
    const sessionInput = node('input', { type: 'number', min: '0', max: '48', step: '0.5', value: String(stats.session_hours) });
    const archiveInput = node('input', { type: 'text', value: stats.archive_dir });
    const draftsInput = node('input', { type: 'text', value: (stats.draft_keywords || []).join(', ') });
    const trashDaysInput = node('input', { type: 'number', min: '0', max: '365', step: '1', value: '30' });
    const quotaInput = node('input', { type: 'number', min: '0.5', max: '200', step: '0.5',
      value: String(((stats.thumb_quota || 0) / (1024 * 1024 * 1024)).toFixed(1)) });
    const body = node('div', {}, [
      node('div', { class: 'ig-field' }, [node('label', { text: '会话窗口（小时）' }), sessionInput]),
      node('p', { class: 'ig-muted', text: '相邻两张图间隔超过该值即视为跨会话，不再做 diff。0 表示关闭该规则。' }),
      node('div', { class: 'ig-field' }, [node('label', { text: '存档目录' }), archiveInput]),
      node('p', { class: 'ig-muted', text: '批量另存为的落盘位置。' }),
      node('div', { class: 'ig-field' }, [node('label', { text: '草稿关键词' }), draftsInput]),
      node('p', { class: 'ig-muted', text: '目录路径包含这些词（逗号分隔）的图片默认隐藏，可用工具栏的草稿按钮临时显示。' }),
      node('div', { class: 'ig-field' }, [node('label', { text: '缩略图上限（GB）' }), quotaInput]),
      node('div', { class: 'ig-field' }, [node('label', { text: '回收站保留（天）' }), trashDaysInput]),
      node('p', { class: 'ig-muted', text: '超过保留天数的回收站批次会被后台线程自动永久清理；0 表示不自动清理。' }),
      node('div', { class: 'ig-field' }, [node('label', { text: '索引状态' }),
        node('span', { text: '文件 ' + stats.files + ' · 已解析 ' + stats.parsed + ' · 待解析 ' + stats.pending + ' · 族 ' + stats.families })]),
      node('div', { class: 'ig-field' }, [node('label', { text: '缩略图缓存' }),
        node('span', { text: fmtSize(stats.thumbs.bytes) + ' / ' + stats.thumbs.files + ' 个文件' })]),
      node('div', { class: 'ig-field' }, [node('label', { text: '后台线程' }),
        node('span', { text: (stats.worker.phase || 'idle') + (stats.worker.dir ? ' · ' + stats.worker.dir : '') + ' · 在线浏览 ' + stats.viewers })]),
    ]);
    openModal('设置', body, [
      { label: '取消', onClick: closeModal },
      {
        label: '清理缩略图', onClick: async function () {
          try {
            const result = await post('/thumbs/cleanup', {});
            toast(result.skipped ? '未超限，无需清理' : ('已回收 ' + result.removed + ' 个缩略图（' + fmtSize(result.removed_bytes) + '）'), 'ok');
            openSettings();
          } catch (error) {
            toast('清理失败：' + error.message, 'error');
          }
        },
      },
      {
        label: '保存', primary: true,
        onClick: async function () {
          try {
            await post('/settings', {
              session_hours: Number(sessionInput.value || 0),
              archive_dir: archiveInput.value.trim(),
              draft_keywords: draftsInput.value.trim(),
              thumb_quota_gb: Number(quotaInput.value || 5),
              trash_keep_days: Number(trashDaysInput.value || 30),
            });
            toast('设置已保存', 'ok');
            closeModal();
            refreshFilterButtons();
            await loadDirs();
            await reload();
          } catch (error) {
            toast('保存失败：' + error.message, 'error');
          }
        },
      },
    ]);
  } catch (error) {
    toast('设置读取失败：' + error.message, 'error');
  }
}

async function openModelFilter() {
  try {
    const data = await api('/models');
    const selected = new Set(state.models);
    const list = node('div', { class: 'ig-model-list' });
    data.models.forEach(function (entry) {
      const box = node('input', { type: 'checkbox' });
      box.checked = selected.has(entry.name);
      box.addEventListener('change', function () {
        if (box.checked) selected.add(entry.name); else selected.delete(entry.name);
      });
      list.appendChild(node('div', { class: 'ig-model-row' }, [box, node('span', { text: entry.name }), node('small', { text: '(' + entry.count + ')' })]));
    });
    const mode = node('select', {}, [
      node('option', { value: 'any', text: '包含任一' }),
      node('option', { value: 'all', text: '包含全部' }),
    ]);
    mode.value = state.modelMode;
    const body = node('div', {}, [node('div', { class: 'ig-field' }, [node('label', { text: '匹配方式' }), mode]), list]);
    openModal('模型筛选', body, [
      { label: '清空', onClick: function () { state.models = []; closeModal(); reload(); } },
      {
        label: '应用', primary: true,
        onClick: function () {
          state.models = Array.from(selected);
          state.modelMode = mode.value;
          closeModal();
          reload();
        },
      },
    ]);
  } catch (error) {
    toast('模型列表读取失败：' + error.message, 'error');
  }
}

async function bulkSave() {
  const ids = Array.from(state.selected);
  if (!ids.length) return;
  try {
    const result = await post('/export', { ids: ids });
    toast('已复制 ' + result.copied.length + ' 个到 ' + result.archive + (result.failed.length ? '，失败 ' + result.failed.length : ''), 'ok');
  } catch (error) {
    toast('另存为失败：' + error.message, 'error');
  }
}

/* ---------------- 草稿过滤 / 收藏 / 标签 ---------------- */

function refreshFilterButtons() {
  if (!el['ig-drafts']) return;
  el['ig-drafts'].classList.toggle('is-primary', state.showDrafts);
  el['ig-fav'].classList.toggle('is-primary', state.favoriteOnly);
  el['ig-tag-btn'].classList.toggle('is-primary', state.tags.length > 0);
  el['ig-drafts'].title = state.showDrafts
    ? '正在显示草稿目录的图片，点击隐藏'
    : '已隐藏 ' + state.hiddenDrafts + ' 张草稿目录图片，点击显示';
}

function toggleDrafts() {
  state.showDrafts = !state.showDrafts;
  try { localStorage.setItem('ig.showDrafts', state.showDrafts ? '1' : '0'); } catch (error) { /* ignore */ }
  refreshFilterButtons();
  loadDirs();
  reload();
}

function toggleFavoriteOnly() {
  state.favoriteOnly = !state.favoriteOnly;
  refreshFilterButtons();
  reload();
}

async function toggleFavorite(fileId, value) {
  try {
    await post('/favorite', { id: fileId, value: value });
    state.items.forEach(function (entry, index) {
      if (entry.id !== fileId) return;
      entry.favorite = value;
      const element = state.nodes.get(index);
      if (!element) return;
      const star = element.querySelector('.ig-card-star');
      if (star) {
        star.textContent = value ? '★' : '☆';
        star.classList.toggle('is-on', value);
      }
    });
    if (state.detail && state.detail.file && state.detail.file.id === fileId) {
      state.detail.favorite = value;
      renderDetail(state.detail);
    }
    toast(value ? '已加入收藏' : '已取消收藏', 'ok');
    if (state.favoriteOnly) reload();
  } catch (error) {
    toast('收藏失败：' + error.message, 'error');
  }
}

async function saveTags(fileId, text) {
  const names = String(text || '').split(',').map(function (part) { return part.trim(); }).filter(Boolean);
  try {
    await post('/tags', { id: fileId, tags: names });
    toast('标签已保存（' + names.length + ' 个）', 'ok');
    if (state.detail && state.detail.file && state.detail.file.id === fileId) openDetail(fileId);
  } catch (error) {
    toast('标签保存失败：' + error.message, 'error');
  }
}

async function openTagFilter() {
  try {
    const data = await api('/tags');
    const selected = new Set(state.tags);
    const list = node('div', { class: 'ig-model-list' });
    if (!data.tags.length) {
      list.appendChild(node('p', { class: 'ig-muted', text: '还没有自定义标签：在图片详情抽屉里填写即可。' }));
    }
    data.tags.forEach(function (entry) {
      const box = node('input', { type: 'checkbox' });
      box.checked = selected.has(entry.name);
      box.addEventListener('change', function () {
        if (box.checked) selected.add(entry.name); else selected.delete(entry.name);
      });
      list.appendChild(node('div', { class: 'ig-model-row' }, [
        box, node('span', { text: entry.name }), node('small', { text: '(' + entry.count + ')' }),
      ]));
    });
    const mode = node('select', {}, [
      node('option', { value: 'any', text: '包含任一' }),
      node('option', { value: 'all', text: '包含全部' }),
    ]);
    mode.value = state.tagMode;
    openModal('标签筛选', node('div', {}, [
      node('div', { class: 'ig-field' }, [node('label', { text: '匹配方式' }), mode]),
      list,
    ]), [
      {
        label: '清空', onClick: function () {
          state.tags = []; state.tagMode = 'any'; closeModal(); refreshFilterButtons(); reload();
        },
      },
      {
        label: '应用', primary: true, onClick: function () {
          state.tags = Array.from(selected); state.tagMode = mode.value; closeModal(); refreshFilterButtons(); reload();
        },
      },
    ]);
  } catch (error) {
    toast('标签列表读取失败：' + error.message, 'error');
  }
}

/* ---------------- 版本自检：页面开着时后端更新了就提示刷新 ---------------- */

function watchVersion() {
  const known = state.version;
  el['ig-update-reload'].addEventListener('click', function () { window.location.reload(); });
  setInterval(async function () {
    try {
      const data = await api('/hello');
      if (known && data.version && data.version !== known) el['ig-update'].hidden = false;
    } catch (error) { /* 后端重启中，忽略 */ }
  }, 60000);
}

/* ---------------- 多语言（静态 chrome + diff 摘要） ---------------- */

const I18N = {
  zh: {
    scan: '刷新索引', trash: '回收站', settings: '设置', modelFilter: '模型筛选', drafts: '草稿',
    favorites: '收藏', tags: '标签', compare: '对比', saveAs: '另存为', rename: '重命名',
    moveTo: '移动到', remove: '删除', selectAll: '全选已加载', clearSel: '清除选择',
    viewDetail: '查看详情', sortNew: '时间倒序', sortOld: '时间正序', sortSize: '大小倒序',
    sortName: '名称', sortRelevance: '相关度（需搜索词）',
    count: '已加载 {a} / 共 {b}', selected: '已选 {n} 项', allDirs: '全部目录', dirTitle: '目录',
    sidebarHint: '筛选目录', searchHint: '搜索提示词、文件名、模型（FTS5，≥3 字符为子串匹配）',
    langButton: 'EN',
  },
  en: {
    scan: 'Rescan', trash: 'Trash', settings: 'Settings', modelFilter: 'Models', drafts: 'Drafts',
    favorites: 'Favorites', tags: 'Tags', compare: 'Compare', saveAs: 'Save as', rename: 'Rename',
    moveTo: 'Move to', remove: 'Delete', selectAll: 'Select loaded', clearSel: 'Clear',
    viewDetail: 'Details', sortNew: 'Newest first', sortOld: 'Oldest first', sortSize: 'Largest first',
    sortName: 'Name', sortRelevance: 'Relevance (needs query)',
    count: 'Loaded {a} of {b}', selected: '{n} selected', allDirs: 'All folders', dirTitle: 'Folders',
    sidebarHint: 'Filter folders', searchHint: 'Search prompts, filenames, models (FTS5, >=3 chars substring)',
    langButton: '中',
  },
};

function t(key, vars) {
  const table = I18N[state.lang] || I18N.zh;
  let text = table[key] || (I18N.zh[key] || key);
  Object.keys(vars || {}).forEach(function (name) {
    text = text.split('{' + name + '}').join(String(vars[name]));
  });
  return text;
}

function applyLanguage() {
  document.querySelectorAll('[data-i18n]').forEach(function (element) {
    element.textContent = t(element.dataset.i18n);
  });
  const search = el['ig-search'];
  if (search) search.placeholder = t('searchHint');
  const dirFilter = el['ig-dir-filter'];
  if (dirFilter) dirFilter.placeholder = t('sidebarHint');
  if (el['ig-lang']) el['ig-lang'].textContent = t('langButton');
  updateCount();
  updateBulkText();
}

function setLanguage(lang) {
  state.lang = lang === 'en' ? 'en' : 'zh';
  try { localStorage.setItem('ig.lang', state.lang); } catch (error) { /* ignore */ }
  applyLanguage();
  if (state.detailId) openDetail(state.detailId);
}

/* ---------------- 搜索历史 / 正则开关 ---------------- */

function rememberSearch(text) {
  const value = String(text || '').trim();
  if (value.length < 2) return;
  state.searchHistory = [value].concat(state.searchHistory.filter(function (item) { return item !== value; })).slice(0, 12);
  try { localStorage.setItem('ig.searchHistory', JSON.stringify(state.searchHistory)); } catch (error) { /* ignore */ }
  fillSearchHistory();
}

function fillSearchHistory() {
  const list = el['ig-search-history'];
  if (!list) return;
  list.innerHTML = '';
  state.searchHistory.forEach(function (item) {
    list.appendChild(node('option', { value: item }));
  });
}

function toggleRegex() {
  state.mode = state.mode === 'regex' ? 'fts' : 'regex';
  el['ig-regex'].classList.toggle('is-primary', state.mode === 'regex');
  el['ig-regex'].title = state.mode === 'regex' ? '正则模式：对合并文本做 re.search（点击切回普通搜索）' : '正则搜索（对合并文本做 re.search）';
  if (state.q) reload();
}

/* ---------------- 灯箱：页面内最大化（不是浏览器全屏） ---------------- */

const ZOOM_MAX = 8;
const lightbox = { id: 0, newer: null, older: null, zoom: 1, panX: 0, panY: 0, dragged: false };

async function openLightbox(fileId) {
  if (!fileId) return;
  lightbox.id = fileId;
  lightbox.newer = null;
  lightbox.older = null;
  resetLightboxZoom();
  el['ig-lightbox-img'].src = API + '/raw?id=' + fileId;
  el['ig-lightbox-name'].textContent = '#' + fileId;
  el['ig-lightbox'].hidden = false;
  setActive(fileId, true);
  el['ig-lb-prev'].disabled = true;
  el['ig-lb-next'].disabled = true;
  try {
    const data = await api('/detail?id=' + fileId + '&lang=' + state.lang);
    if (lightbox.id !== fileId) return;
    lightbox.newer = data.newer_id || null;
    lightbox.older = data.older_id || null;
    const item = data.file;
    const dims = item.width && item.height ? item.width + '×' + item.height + ' · ' : '';
    el['ig-lightbox-name'].textContent = item.name + ' · ' + dims + fmtSize(item.size);
    el['ig-lb-prev'].disabled = !lightbox.newer;
    el['ig-lb-next'].disabled = !lightbox.older;
  } catch (error) {
    /* 导航不可用不影响看图 */
  }
}

function closeLightbox() {
  el['ig-lightbox'].hidden = true;
  el['ig-lightbox-img'].src = '';
  resetLightboxZoom();
}

function applyLightboxZoom() {
  const image = el['ig-lightbox-img'];
  const fit = lightbox.zoom <= 1.001;  // 浮点乘法会留下 1.0000000000000004 这类残差
  image.style.transform = fit ? '' : 'translate(' + lightbox.panX.toFixed(1) + 'px,' +
    lightbox.panY.toFixed(1) + 'px) scale(' + lightbox.zoom.toFixed(3) + ')';
  image.classList.toggle('is-zoomed', !fit);
  if (el['ig-lightbox-zoom']) el['ig-lightbox-zoom'].textContent = Math.round((fit ? 1 : lightbox.zoom) * 100) + '%';
}

function resetLightboxZoom() {
  lightbox.zoom = 1;
  lightbox.panX = 0;
  lightbox.panY = 0;
  lightbox.dragged = false;
  applyLightboxZoom();
}

function lightboxStep(direction) {
  const target = direction < 0 ? lightbox.newer : lightbox.older;
  if (target) openLightbox(target);
}

function bindLightbox() {
  el['ig-lb-close'].addEventListener('click', closeLightbox);
  el['ig-lb-prev'].addEventListener('click', function () { lightboxStep(-1); });
  el['ig-lb-next'].addEventListener('click', function () { lightboxStep(1); });
  el['ig-lb-detail'].addEventListener('click', function () {
    const id = lightbox.id;
    closeLightbox();
    openDetail(id);
  });
  el['ig-lightbox'].addEventListener('click', function (event) {
    if (event.target === el['ig-lightbox']) closeLightbox();
  });

  // 单击左半边看更新的、右半边看更早的；双击退出放大（延迟 220ms 区分单双击）
  const image = el['ig-lightbox-img'];
  let clickTimer = 0;
  image.addEventListener('click', function (event) {
    clearTimeout(clickTimer);
    const dragged = lightbox.dragged;
    lightbox.dragged = false;
    if (lightbox.zoom > 1 || dragged) return;  // 放大状态只做平移，翻页交给左右箭头
    const rect = image.getBoundingClientRect();
    const goNewer = (event.clientX - rect.left) < rect.width / 2;
    clickTimer = setTimeout(function () {
      if (goNewer) lightboxStep(-1); else lightboxStep(1);
    }, 220);
  });
  image.addEventListener('dblclick', function () {
    clearTimeout(clickTimer);
    closeLightbox();
  });

  // 滚轮缩放：以光标为锚点，1x 表示适配窗口，最大 8x
  image.addEventListener('wheel', function (event) {
    event.preventDefault();
    const step = lightbox.zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15);
    const next = Math.min(ZOOM_MAX, step <= 1.001 ? 1 : step);
    if (Math.abs(next - lightbox.zoom) < 1e-4) return;
    const rect = image.getBoundingClientRect();
    const cx = event.clientX - (rect.left + rect.width / 2);
    const cy = event.clientY - (rect.top + rect.height / 2);
    const ratio = next / lightbox.zoom;
    lightbox.panX -= cx * (ratio - 1);
    lightbox.panY -= cy * (ratio - 1);
    lightbox.zoom = next;
    if (next === 1) { lightbox.panX = 0; lightbox.panY = 0; }
    applyLightboxZoom();
  }, { passive: false });

  // 放大后按住拖动平移
  let panning = false, panStartX = 0, panStartY = 0, panBaseX = 0, panBaseY = 0;
  image.addEventListener('pointerdown', function (event) {
    lightbox.dragged = false;
    if (lightbox.zoom <= 1) return;
    panning = true;
    panStartX = event.clientX;
    panStartY = event.clientY;
    panBaseX = lightbox.panX;
    panBaseY = lightbox.panY;
    image.classList.add('is-panning');
    try { image.setPointerCapture(event.pointerId); } catch (error) { /* ignore */ }
  });
  image.addEventListener('pointermove', function (event) {
    if (!panning) return;
    const dx = event.clientX - panStartX;
    const dy = event.clientY - panStartY;
    if (Math.abs(dx) + Math.abs(dy) > 6) lightbox.dragged = true;
    lightbox.panX = panBaseX + dx;
    lightbox.panY = panBaseY + dy;
    applyLightboxZoom();
  });
  const endPan = function (event) {
    if (!panning) return;
    panning = false;
    image.classList.remove('is-panning');
    try { image.releasePointerCapture(event.pointerId); } catch (error) { /* ignore */ }
  };
  image.addEventListener('pointerup', endPan);
  image.addEventListener('pointercancel', endPan);
}

/* ---------------- 侧栏：可调宽度 + 可折叠 ---------------- */

const SIDEBAR_MIN = 160;
const SIDEBAR_MAX = 560;
const SIDEBAR_DEFAULT = 232;

function applySidebar() {
  let width = SIDEBAR_DEFAULT;
  try { width = Number(localStorage.getItem('ig.sidebarWidth') || SIDEBAR_DEFAULT) || SIDEBAR_DEFAULT; } catch (error) { /* ignore */ }
  width = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, width));
  let collapsed = false;
  try { collapsed = localStorage.getItem('ig.sidebarCollapsed') === '1'; } catch (error) { /* ignore */ }
  el['ig-sidebar'].style.width = collapsed ? '' : width + 'px';
  el['ig-sidebar'].style.flexBasis = collapsed ? '' : width + 'px';
  el['ig-sidebar'].classList.toggle('is-collapsed', collapsed);
  el['ig-sidebar-toggle'].textContent = collapsed ? '⟩' : '⟨';
  if (el['ig-sidebar-toggle-top']) {
    el['ig-sidebar-toggle-top'].classList.toggle('is-primary', collapsed);
    el['ig-sidebar-toggle-top'].title = collapsed
      ? '显示左侧目录栏（快捷键 [ ）'
      : '隐藏左侧目录栏（快捷键 [ ）';
  }
  layout();
}

function toggleSidebar() {
  const collapsed = !el['ig-sidebar'].classList.contains('is-collapsed');
  try { localStorage.setItem('ig.sidebarCollapsed', collapsed ? '1' : '0'); } catch (error) { /* ignore */ }
  applySidebar();
  // 过渡动画结束后再量一次宽度，否则列数按旧宽度算，留白会堆在右侧
  setTimeout(layout, 240);
}

function bindSidebar() {
  el['ig-sidebar'].addEventListener('transitionend', function (event) {
    if (event.propertyName === 'width' || event.propertyName === 'flex-basis') layout();
  });
  const handle = el['ig-sidebar-resize'];
  let dragging = false;
  handle.addEventListener('pointerdown', function (event) {
    dragging = true;
    handle.classList.add('is-active');
    try { handle.setPointerCapture(event.pointerId); } catch (error) { /* ignore */ }
    event.preventDefault();
  });
  handle.addEventListener('pointermove', function (event) {
    if (!dragging) return;
    const left = el['ig-sidebar'].getBoundingClientRect().left;
    const width = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, event.clientX - left));
    el['ig-sidebar'].style.width = width + 'px';
    el['ig-sidebar'].style.flexBasis = width + 'px';
    try { localStorage.setItem('ig.sidebarWidth', String(Math.round(width))); } catch (error) { /* ignore */ }
  });
  const stop = function (event) {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('is-active');
    try { handle.releasePointerCapture(event.pointerId); } catch (error) { /* ignore */ }
    layout();
  };
  handle.addEventListener('pointerup', stop);
  handle.addEventListener('pointercancel', stop);
  handle.addEventListener('dblclick', function () {
    try { localStorage.setItem('ig.sidebarWidth', String(SIDEBAR_DEFAULT)); } catch (error) { /* ignore */ }
    applySidebar();
  });
  el['ig-sidebar-toggle'].addEventListener('click', toggleSidebar);
  el['ig-sidebar-toggle-top'].addEventListener('click', toggleSidebar);
}

/* ---------------- 批量重命名 / 移动到 / 对比视图 ---------------- */

function openRename() {
  const ids = Array.from(state.selected);
  if (!ids.length) return;
  const first = state.items.find(function (item) { return item.id === ids[0]; }) || { name: 'name.png' };
  const stem0 = first.name.replace(/\.[^.]+$/, '');
  const ext0 = first.name.slice(stem0.length);
  const templateInput = node('input', { type: 'text', value: '{name}' });
  const padInput = node('input', { type: 'number', min: '0', max: '6', step: '1', value: '0' });
  const preview = node('div', { class: 'ig-muted' });
  const refresh = function () {
    const pad = Math.max(0, Number(padInput.value || 0));
    const number = pad ? String(1).padStart(pad, '0') : '1';
    const name = templateInput.value.split('{name}').join(stem0).split('{n}').join(number).split('{ext}').join(ext0.replace('.', ''));
    preview.textContent = '预览：' + name + '（共 ' + ids.length + ' 个，序号按当前顺序递增）';
  };
  templateInput.addEventListener('input', refresh);
  padInput.addEventListener('input', refresh);
  refresh();
  openModal('批量重命名', node('div', {}, [
    node('div', { class: 'ig-field' }, [node('label', { text: '命名模板' }), templateInput]),
    node('p', { class: 'ig-muted', text: '占位符：{name} 原名、{n} 序号、{ext} 扩展名。例如 {name}_{n}' }),
    node('div', { class: 'ig-field' }, [node('label', { text: '序号补零位数' }), padInput]),
    preview,
  ]), [
    { label: '取消', onClick: closeModal },
    {
      label: '执行', primary: true,
      onClick: async function () {
        closeModal();
        try {
          const result = await post('/rename', { ids: ids, template: templateInput.value, pad: Number(padInput.value || 0) });
          const bad = result.results.filter(function (item) { return item.error; });
          toast('重命名完成：成功 ' + (result.results.length - bad.length) + ' 个' +
            (bad.length ? '，失败 ' + bad.length + '（' + bad[0].error + '）' : ''), bad.length ? 'error' : 'ok');
          state.selected.clear(); updateBulkText(); el['ig-bulkbar'].hidden = true;
          await loadDirs(); await reload();
        } catch (error) {
          toast('重命名失败：' + error.message, 'error');
        }
      },
    },
  ]);
}

function openMove() {
  const ids = Array.from(state.selected);
  if (!ids.length) return;
  const targetInput = node('input', { type: 'text', value: '' });
  const hint = node('p', { class: 'ig-muted',
    text: '相对路径以当前根目录为基准（例如 2026-10-10/精选），也可以填绝对路径；目标必须位于图库根目录内。' });
  openModal('移动到目录', node('div', {}, [
    node('div', { class: 'ig-field' }, [node('label', { text: '目标目录' }), targetInput]),
    hint,
  ]), [
    { label: '取消', onClick: closeModal },
    {
      label: '移动', primary: true,
      onClick: async function () {
        const target = targetInput.value.trim();
        if (!target) { toast('请填写目标目录', 'error'); return; }
        closeModal();
        try {
          const result = await post('/move', { ids: ids, target: target, root: state.root });
          const bad = result.results.filter(function (item) { return item.error; });
          toast('已移动 ' + (result.results.length - bad.length) + ' 个到 ' + result.target +
            (bad.length ? '，失败 ' + bad.length : ''), bad.length ? 'error' : 'ok');
          state.selected.clear(); updateBulkText(); el['ig-bulkbar'].hidden = true;
          await loadDirs(); await reload();
        } catch (error) {
          toast('移动失败：' + error.message, 'error');
        }
      },
    },
  ]);
}

function openCompare() {
  const picked = state.items.filter(function (item) { return state.selected.has(item.id); }).slice(0, 4);
  if (picked.length < 2) { toast('至少选择 2 张才能对比', 'error'); return; }
  const grid = node('div', { class: 'ig-compare-grid' });
  picked.forEach(function (item) {
    const image = node('img', { class: 'ig-compare-img', alt: item.name, title: '双击放大' });
    image.src = API + '/raw?id=' + item.id;
    image.addEventListener('dblclick', function () { closeModal(); openLightbox(item.id); });
    grid.appendChild(node('figure', {}, [
      image,
      node('figcaption', { text: item.name + ' · ' + (item.width && item.height ? item.width + '×' + item.height : '') }),
    ]));
  });
  openModal('对比（' + picked.length + ' 张）', grid, [{ label: '关闭', onClick: closeModal }]);
}

async function openTrashBatch(batch) {
  try {
    const data = await api('/trash/detail?batch=' + encodeURIComponent(batch));
    const list = node('div', { class: 'ig-model-list' });
    const boxes = [];
    data.entries.forEach(function (entry) {
      const box = node('input', { type: 'checkbox' });
      box.checked = !entry.restored;
      box.disabled = entry.restored;
      boxes.push({ box: box, id: entry.id });
      list.appendChild(node('div', { class: 'ig-model-row' }, [
        box,
        node('span', { text: entry.name, title: entry.original }),
        node('small', { text: fmtSize(entry.size) + (entry.restored ? ' · 已还原' : '') }),
      ]));
    });
    openModal('回收站批次 ' + batch + '（' + data.entries.length + ' 个）', list, [
      {
        label: '清空该批次', onClick: async function () {
          closeModal();
          try {
            const result = await post('/trash/purge', { batch: batch });
            toast('已永久删除 ' + result.removed + ' 个文件', 'ok');
            await reload();
          } catch (error) { toast('清空失败：' + error.message, 'error'); }
        },
      },
      {
        label: '还原选中', primary: true, onClick: async function () {
          const ids = boxes.filter(function (item) { return item.box.checked && !item.box.disabled; })
            .map(function (item) { return item.id; });
          if (!ids.length) { toast('没有勾选可还原的文件', 'error'); return; }
          closeModal();
          try {
            const result = await post('/trash/restore', { batch: batch, ids: ids });
            toast('已还原 ' + result.restored.length + ' 个' + (result.failed.length ? '，失败 ' + result.failed.length : ''), 'ok');
            await reload();
          } catch (error) { toast('还原失败：' + error.message, 'error'); }
        },
      },
    ]);
  } catch (error) {
    toast('批次读取失败：' + error.message, 'error');
  }
}

/* ---------------- 快捷键帮助 ---------------- */

function openHelp() {
  const rows = [
    ['双击卡片 / 详情大图', '在页面内最大化查看（灯箱，非浏览器全屏）'],
    ['双击灯箱里的图片', '退出放大（详情页保持打开）'],
    ['点灯箱图片左 / 右半边', '翻页：左＝更新的一张，右＝更早的一张'],
    ['缩略图滑块双击', '复位到默认大小'],
    ['← / →', '按网格顺序翻页：左＝更新的一张，右＝更早的一张'],
    ['空格', '打开当前详情图的灯箱'],
    ['Esc', '关闭灯箱 / 详情 / 弹窗'],
    ['[ 或顶栏左上角的 ☰', '隐藏或显示左侧目录栏'],
    ['/', '聚焦搜索框'],
    ['?', '打开本帮助'],
    ['Delete', '把当前详情图移入回收站'],
  ];
  const table = node('div', { class: 'ig-kv' });
  rows.forEach(function (row) {
    table.appendChild(node('dt', { text: row[0] }));
    table.appendChild(node('dd', { text: row[1] }));
  });
  openModal('快捷键', table, [{ label: '关闭', onClick: closeModal }]);
}

/* ---------------- 状态栏 ---------------- */

async function pollStatus() {
  try {
    const stats = await api('/stats');
    state.stats = stats;
    el['ig-badge'].textContent = state.milestone || '';
    const worker = stats.worker || {};
    const parts = [];
    if (worker.phase === 'index') parts.push('索引中 ' + (worker.dir || '').split('\\').pop());
    else if (worker.phase === 'scan') parts.push('遍历目录中');
    else parts.push('空闲');
    parts.push('待解析 ' + stats.pending);
    parts.push('在线 ' + stats.viewers);
    if (stats.jobs && stats.jobs[0] && stats.jobs[0].message) parts.push(stats.jobs[0].message);
    el['ig-status'].textContent = parts.join(' · ');
    maybeAutoRefresh(stats);
  } catch (error) {
    el['ig-status'].textContent = '后端未就绪：' + error.message;
  }
}

/* 索引一变多就自己刷新：数据来自既有的 3 秒状态轮询，不额外发请求。
   只在"用户正看着最新一批"时静默重载；已选中、开着详情或灯箱、已经翻到下面时都不打断。 */
function maybeAutoRefresh(stats) {
  const seen = state.indexSeen;
  state.indexSeen = stats.files;
  if (seen === null || seen === undefined || stats.files <= seen) return;
  if (state.selected.size || !el['ig-drawer'].hidden || !el['ig-lightbox'].hidden) return;
  const wrap = el['ig-grid-wrap'];
  if (wrap.scrollTop > 400) return;
  const scrollTop = wrap.scrollTop;
  reload().then(function () {
    requestAnimationFrame(function () { wrap.scrollTop = scrollTop; });
  }).catch(function () { /* 下一轮轮询再试 */ });
}

/* ---------------- 事件绑定 ---------------- */

function bind() {
  document.querySelectorAll('.ig-root').forEach(function (button) {
    button.addEventListener('click', function () {
      document.querySelectorAll('.ig-root').forEach(function (other) { other.classList.toggle('is-active', other === button); });
      state.root = button.dataset.root;
      state.dir = '';
      loadDirs();
      reload();
    });
  });

  el['ig-drawer-close'].addEventListener('click', function () { el['ig-drawer'].hidden = true; });
  el['ig-prev'].addEventListener('click', function () { if (state.detail && state.detail.newer_id) openDetail(state.detail.newer_id); });
  el['ig-next'].addEventListener('click', function () { if (state.detail && state.detail.older_id) openDetail(state.detail.older_id); });
  el['ig-modal-close'].addEventListener('click', closeModal);
  el['ig-overlay'].addEventListener('click', function (event) { if (event.target === el['ig-overlay']) closeModal(); });
  el['ig-model-btn'].addEventListener('click', openModelFilter);
  el['ig-drafts'].addEventListener('click', toggleDrafts);
  el['ig-fav'].addEventListener('click', toggleFavoriteOnly);
  el['ig-tag-btn'].addEventListener('click', openTagFilter);
  el['ig-lang'].addEventListener('click', function () { setLanguage(state.lang === 'zh' ? 'en' : 'zh'); });
  el['ig-regex'].addEventListener('click', toggleRegex);
  el['ig-bulk-compare'].addEventListener('click', openCompare);
  el['ig-bulk-rename'].addEventListener('click', openRename);
  el['ig-bulk-move'].addEventListener('click', openMove);
  el['ig-search'].addEventListener('change', function () { rememberSearch(el['ig-search'].value); });
  el['ig-trash'].addEventListener('click', openTrash);
  el['ig-settings'].addEventListener('click', openSettings);
  el['ig-scan'].addEventListener('click', async function () {
    await post('/scan', {});
    toast('已请求重新遍历，稍后刷新', 'ok');
    setTimeout(pollStatus, 1500);
  });
  el['ig-select-mode'].addEventListener('click', function () {
    state.selectMode = !state.selectMode;
    if (!state.selectMode) { state.selected.clear(); }
    el['ig-select-mode'].classList.toggle('is-primary', state.selectMode);
    el['ig-bulkbar'].hidden = state.selected.size === 0;
    layout();
  });
  el['ig-bulk-clear'].addEventListener('click', function () {
    state.selected.clear();
    el['ig-bulkbar'].hidden = true;
    layout();
  });
  el['ig-bulk-all'].addEventListener('click', function () {
    state.items.forEach(function (item) { state.selected.add(item.id); });
    updateBulkText();
    el['ig-bulkbar'].hidden = false;
    layout();
  });
  el['ig-bulk-save'].addEventListener('click', bulkSave);
  el['ig-bulk-delete'].addEventListener('click', function () { removeItems(Array.from(state.selected)); });
  el['ig-sort'].addEventListener('change', function () { state.sort = el['ig-sort'].value; reload(); });
  el['ig-dir-filter'].addEventListener('input', function () { loadDirs(); });

  let searchTimer = 0;
  el['ig-search'].addEventListener('input', function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      state.q = el['ig-search'].value.trim();
      reload();
    }, 320);
  });

  el['ig-grid-wrap'].addEventListener('scroll', function () {
    renderGrid();
    const wrap = el['ig-grid-wrap'];
    if (wrap.scrollTop + wrap.clientHeight > wrap.scrollHeight - 800) loadMore();
  });
  let resizeTimer = 0;
  window.addEventListener('resize', function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(layout, 80);
  });

  // 网格容器宽度变化（侧栏折叠/拖拽、窗口缩放、滚动条出现）统一触发重排
  if (typeof ResizeObserver !== 'undefined') {
    const observer = new ResizeObserver(function () {
      layout();
      clearTimeout(state.observerTimer);
      state.observerTimer = setTimeout(layout, 300);
    });
    observer.observe(el['ig-grid-wrap']);
  }

  const sizeSlider = el['ig-thumb-size'];
  if (sizeSlider) {
    sizeSlider.value = String(state.cardWidth);
    const applySize = function (value) {
      state.cardWidth = value;
      sizeSlider.value = String(value);
      try { localStorage.setItem('ig.cardWidth', String(value)); } catch (error) { /* ignore */ }
      state.nodes.forEach(function (element) { element.remove(); });
      state.nodes.clear();
      layout();
    };
    sizeSlider.addEventListener('input', function () { applySize(Number(sizeSlider.value) || 168); });
    sizeSlider.addEventListener('dblclick', function () { applySize(168); });
  }
  document.addEventListener('click', function (event) {
    if (!el['ig-menu'].hidden && !el['ig-menu'].contains(event.target)) closeMenu();
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      // 逐层关闭：灯箱 -> 菜单 -> 弹窗 -> 抽屉；关灯箱时详情保持打开
      if (!el['ig-lightbox'].hidden) { closeLightbox(); return; }
      if (!el['ig-menu'].hidden) { closeMenu(); return; }
      if (!el['ig-overlay'].hidden) { closeModal(); return; }
      if (!el['ig-drawer'].hidden) { el['ig-drawer'].hidden = true; }
      return;
    }
    if (!el['ig-lightbox'].hidden) {
      if (event.key === 'ArrowLeft') lightboxStep(-1);
      if (event.key === 'ArrowRight') lightboxStep(1);
      return;
    }
    if (!el['ig-drawer'].hidden && state.detail) {
      if (event.key === 'ArrowLeft' && state.detail.newer_id) openDetail(state.detail.newer_id);
      if (event.key === 'ArrowRight' && state.detail.older_id) openDetail(state.detail.older_id);
    }
    if (event.key === '[') toggleSidebar();
    if (event.key === '?') openHelp();
    const typing = /^(input|textarea|select)$/i.test((event.target && event.target.tagName) || '');
    if (event.key === '/' && !typing) { event.preventDefault(); el['ig-search'].focus(); }
    if (event.key === ' ' && !typing && state.detailId && el['ig-lightbox'].hidden) {
      event.preventDefault();
      openLightbox(state.detailId);
    }
    if (event.key === 'Delete' && !typing && state.detailId && !el['ig-drawer'].hidden) removeItems([state.detailId]);
  });
}

async function boot() {
  bind();
  bindLightbox();
  bindSidebar();
  applySidebar();
  applyLanguage();
  fillSearchHistory();
  watchVersion();
  try {
    const hello = await api('/hello');
    state.milestone = hello.milestone || '';
    el['ig-badge'].textContent = state.milestone;
    el['ig-status'].textContent = hello.plugin + ' v' + hello.version;
  } catch (error) {
    el['ig-status'].textContent = '后端未就绪：' + error.message;
  }
  await loadDirs();
  await reload();
  pollStatus();
  setInterval(pollStatus, 3000);
  setInterval(function () { post('/heartbeat', { viewer: VIEWER_ID }).catch(function () {}); }, 10000);
  post('/heartbeat', { viewer: VIEWER_ID }).catch(function () {});
}

boot();
