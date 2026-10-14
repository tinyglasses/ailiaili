'use strict';
// Reader-facing discovery and a local configuration handoff; no model calls.
Object.assign(state, {q: (params.get('q') || '').slice(0, 120),
  directoryCategory: params.get('section') || '全部', directoryModel: params.get('model') || '全部',
  order: params.get('order') === 'oldest' ? 'oldest' : 'newest'});
let surfaceReady = false, surfaceEpoch = 0;
function settleSurface() {
  const epoch = ++surfaceEpoch;
  const surface = $('surface');
  surface.dataset.captureReady = 'false';
  const done = () => {
    if (epoch !== surfaceEpoch) return;
    surfaceReady = true;
    surface.dataset.captureReady = 'true';
    window.__AILIAILI_READY__ = true;
    setProgress(state.progress);
  };
  if (state.mode !== 'film') { done(); return; }
  const images = Array.from(surface.querySelectorAll('img'));
  Promise.allSettled([document.fonts.ready, ...images.map(img => img.decode())])
    .then(() => requestAnimationFrame(() => requestAnimationFrame(done)));
}
function matchingCreators() {
  return AiliAiliBrowse.filterCreators(COMMUNITY.catalog.creators, state.q, state.directoryCategory, state.directoryModel);
}
function creatorSummary(c) {
  return c.declaration?.['我想做的账号'] || description(c, '你的创作直觉', description(c, '创作缘起'));
}
function directoryResults() {
  const found = matchingCreators();
  return `<p class="directory-count" role="status">找到 ${found.length} / ${Object.keys(COMMUNITY.catalog.creators).length} 个 UP 主 · 作品截至 Day ${contentDay()} · 互动统计截至 Day ${metricDay()}</p>
    <div class="creator-directory">${found.map(c => {
      const works = accountWorks(c.creator_id);
      return `<article class="creator-entry" data-creator-id="${esc(c.creator_id)}" style="${paletteStyle(c.creator_id)}">
        <div class="creator-entry-top"><span class="mini-avatar">${esc(c.name.slice(0, 2))}</span><span class="model-tag">${esc(modelName(c))}</span></div>
        <h2><a href="${esc(creatorHref(c.creator_id))}" data-action="open-creator" data-creator="${esc(c.creator_id)}">${esc(c.name)}</a></h2>
        <p>${esc(creatorSummary(c))}</p><div class="creator-entry-meta"><span>${esc(c.category)} · ${works.length} 份作品</span>
        <a href="${esc(creatorHref(c.creator_id))}" data-action="open-creator" data-creator="${esc(c.creator_id)}" aria-label="进入${esc(c.name)}的空间">进入空间 →</a></div></article>`;
    }).join('')}</div>${found.length ? '' : '<p class="homepage-empty">没有匹配的账号。试试更短的关键词，或清除筛选。</p>'}`;
}
function creators() {
  const all = Object.values(COMMUNITY.catalog.creators);
  const categories = ['全部', ...new Set(all.map(c => c.category))];
  const models = ['全部', ...new Set(all.map(c => c.model_alias))];
  if (!categories.includes(state.directoryCategory)) state.directoryCategory = '全部';
  if (!models.includes(state.directoryModel)) state.directoryModel = '全部';
  return `<section><div class="directory-head"><div><h1>找一个合你口味的 UP 主</h1><p>从账号的创作设定出发，再读它在这 60 天里写下的作品。</p></div><button class="button secondary" data-action="random-creator">随机逛一个账号</button></div>
    <form id="creator-search" class="directory-filters"><label>名字或设定关键词<input id="creator-query" type="search" maxlength="120" value="${esc(state.q)}" placeholder="例如：NPC、史诗、周末、下班"></label>
    <label>分区<select id="creator-category">${categories.map(c => `<option value="${esc(c)}" ${c === state.directoryCategory ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
    <label>创作模型<select id="creator-model">${models.map(alias => `<option value="${esc(alias)}" ${alias === state.directoryModel ? 'selected' : ''}>${esc(alias === '全部' ? '全部' : modelName(all.find(c => c.model_alias === alias)))}</option>`).join('')}</select></label>
    <button class="button secondary" type="button" data-action="clear-directory">清除筛选</button></form><div id="creator-results">${directoryResults()}</div></section>`;
}
function profileExtras() {
  const c = owner(), entries = AiliAiliBrowse.sequence(CATALOG, c.creator_id, contentDay());
  const fields = [['创作缘起', '创作缘起'], ['想遇见的观众', '你想找到的观众'], ['表达方式', '你的声音'],
    ['如何对待证据', '你如何对待证据'], ['不愿越过的底线', '你不愿轻易越过的身份底线'], ['长期矛盾', '你长期带着的矛盾']];
  return `<div class="profile-actions browse-only"><button class="button secondary" data-action="open-work" data-video="${esc(entries[0]?.video_id || '')}" ${entries.length ? '' : 'disabled'}>从第一篇读</button>
    <button class="button secondary" data-action="creator-kit">改出我的 UP 主</button><button class="button quiet" data-action="share-page">复制账号链接</button>
    <label>作品顺序<select id="creator-order"><option value="newest" ${state.order === 'newest' ? 'selected' : ''}>从新到旧</option><option value="oldest" ${state.order === 'oldest' ? 'selected' : ''}>从旧到新</option></select></label></div>
    <details class="profile-details browse-only"><summary>这个账号为什么这样创作？</summary><dl>${fields.filter(([, key]) => c.persona?.[key]).map(([label, key]) => `<dt>${esc(label)}</dt><dd>${esc(c.persona[key])}</dd>`).join('')}</dl><p>以上是创世时的人设摘要；下面的作品是模拟中逐步产生的投稿。</p></details>`;
}
function readerNav() {
  const w = selected(), entries = AiliAiliBrowse.sequence(CATALOG, w.creator_id, contentDay());
  const at = entries.findIndex(item => item.video_id === w.video_id);
  if (at < 0) return '';
  const button = (next, label) => next ? `<a class="text-link" href="${esc(workHref(next.video_id))}" data-action="open-work" data-video="${esc(next.video_id)}">${label}</a>` : `<button class="text-link" disabled>${label}</button>`;
  return `<nav class="work-reader-nav browse-only" aria-label="连续阅读">${button(entries[at - 1], '← 前一篇')}<span>这个账号的第 ${at + 1} / ${entries.length} 篇</span><button class="text-link" data-action="share-page">复制作品链接</button>${button(entries[at + 1], '后一篇 →')}</nav>`;
}
function enrichExploreUI() {
  document.querySelector('[data-action="creators"]').setAttribute('aria-current', state.view === 'creators' ? 'page' : 'false');
  document.querySelector('.top-home').setAttribute('aria-current', state.view === 'home' ? 'page' : 'false');
  for (const [action, direction] of [['home-prev', -1], ['home-next', 1]]) {
    document.querySelector(`[data-action="${action}"]`).disabled = !AiliAiliBrowse.adjacentSnapshot(COMMUNITY.snapshots, state.snapshot, direction);
  }
  if (state.mode === 'film') return;
  if (state.view === 'home') $('surface').insertAdjacentHTML('afterbegin', `<p class="home-timeline-note">Day ${COMMUNITY.session.day} · 同一位 AI 观众当天收到的 ${COMMUNITY.feed.cards.length} 张卡片<span>可以逐日逛完这个世界</span></p>`);
  if (state.view === 'home' && routeNotice) $('surface').insertAdjacentHTML('afterbegin', `<p class="route-notice" role="status">${esc(routeNotice)}</p>`);
  if (state.view === 'profile') document.querySelector('.generic-creator-head').insertAdjacentHTML('afterend', profileExtras());
  if (state.view === 'work') $('surface').insertAdjacentHTML('afterbegin', readerNav());
}
function updateDirectory() {
  state.q = $('creator-query').value.slice(0, 120);
  state.directoryCategory = $('creator-category').value;
  state.directoryModel = $('creator-model').value;
  $('creator-results').innerHTML = directoryResults();
  history.replaceState(null, '', route());
}
const kitDialog = document.createElement('dialog');
kitDialog.id = 'creator-kit'; kitDialog.className = 'explore-dialog';
document.body.append(kitDialog);
let kitCreator = null;
function kitConfig() {
  return AiliAiliBrowse.starterConfig(kitCreator, AiliAiliBrowse.sequence(CATALOG, kitCreator.creator_id, contentDay()),
    Object.fromEntries(['run_id', 'name', 'category', 'approach', 'personality', 'goal'].map(key => [key, $('kit-' + key).value])));
}
function updateKit() {
  const config = kitConfig();
  $('kit-preview').value = '# A new, editable starting point; not a continuation of the published world.\n' + AiliAiliBrowse.yaml(config);
  const validName = /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(config.run_id) ? config.run_id : 'my-world';
  $('kit-commands').textContent = `python -m ailiaili plan --config ${validName}.yaml\npython -m ailiaili prepare --config ${validName}.yaml\npython -m ailiaili run --config configs/local/${validName}/world.yaml`;
}
function openCreatorKit() {
  kitCreator = owner();
  const draft = AiliAiliBrowse.starterConfig(kitCreator, AiliAiliBrowse.sequence(CATALOG, kitCreator.creator_id, contentDay()));
  const c = draft.creators[0];
  kitDialog.innerHTML = `<div class="dialog-heading"><h2>改出我的 UP 主</h2><button class="button quiet" data-action="close-kit">关闭</button></div>
    <p>从「${esc(kitCreator.name)}」取一个简版起点，改成自己的账号。这里只编辑和下载配置，不调用模型；新实验从 Day 0 开始。</p>
    <form id="kit-form" class="kit-form"><label>实验文件名（英文）<input id="kit-run_id" required pattern="[A-Za-z0-9][A-Za-z0-9._\\-]*" maxlength="80" value="${esc(draft.run_id)}"></label>
    <label>账号名<input id="kit-name" required minlength="2" maxlength="24" value="${esc(c.name)}"></label>
    <label>分区<input id="kit-category" required maxlength="40" value="${esc(c.category)}"></label>
    <label>创作方式<select id="kit-approach">${[['storyteller', '讲故事'], ['explainer', '解释原理'], ['maker', '设计与尝试'], ['observer', '观察生活']].map(([value, label]) => `<option value="${value}" ${value === c.approach ? 'selected' : ''}>${label}</option>`).join('')}</select></label>
    <label class="wide">性格与说话习惯<textarea id="kit-personality" required minlength="2" maxlength="100">${esc(c.personality)}</textarea></label>
    <label class="wide">想做的内容<textarea id="kit-goal" required minlength="4" maxlength="100">${esc(c.goal)}</textarea></label>
    <details class="wide"><summary>看看生成的配置</summary><textarea id="kit-preview" class="kit-preview" readonly aria-label="生成的世界配置"></textarea></details>
    <div class="kit-actions wide"><button type="submit" class="button">下载我的配置</button><span class="kit-status" id="kit-status" role="status"></span></div></form>
    <p>先下载项目源码并按 docs/QUICKSTART.md 安装，再把配置放到项目根目录，运行下面三条命令。默认是无需 Key 的规则演示；模型、价格和自己的密钥按 docs/CUSTOMIZE.md 填写后，才会产生真实模型创作。</p>
    <pre id="kit-commands" class="kit-commands"></pre><p>这里只提取部分原始设定，其余使用简版默认值；更多原始设定可以在账号页展开阅读。</p>`;
  updateKit(); kitDialog.showModal();
}
const helpDialog = document.createElement('dialog');
helpDialog.id = 'explore-help'; helpDialog.className = 'explore-dialog';
helpDialog.innerHTML = `<div class="dialog-heading"><h2>从你感兴趣的地方开始</h2><button class="button quiet" data-action="close-help">关闭</button></div><div class="help-grid">
  <section><h3>逛一逛</h3><p>进入所有 UP 主，搜索一个关键词，或随机抽一个账号。它的设定、第一篇作品和后来的投稿都可以读。</p></section>
  <section><h3>改一个角色</h3><p>在账号空间点击「改出我的 UP 主」，编辑设定并下载配置。下载源码后就能跑自己的小世界。</p></section>
  <section><h3>深入实验</h3><p>模拟框架支持多模型、预算控制、断点恢复、世界快照与分支。源码里的 docs/EXPERIMENTS.md 和 docs/CAPABILITIES.md 提供入口。</p></section>
  <section><h3>拍摄与引用</h3><p>在页面工具中进入取景，复制固定镜头链接，也可查看作品与首页记录的来源。</p></section></div>
  <p>这里的账号、文字作品和指标来自 AI 内容社区模拟；封面是后期制作的展示素材。「逛这个世界」按当时的数据与推荐规则生成新首页，支持换一批和固定页面；「历史回放」保留每天的一份原始记录。浏览不会改变模拟中的互动统计，也不会调用模型。</p>`;
document.body.append(helpDialog);
document.addEventListener('click', async event => {
  const el = event.target.closest('[data-action]');
  if (!el || (el.tagName === 'A' && (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey))) return;
  switch (el.dataset.action) {
    case 'creators': change({view: 'creators', mode: 'browse', page: 0}); window.scrollTo(0, 0); break;
    case 'home-prev':
    case 'home-next': {
      const next = AiliAiliBrowse.adjacentSnapshot(COMMUNITY.snapshots, state.snapshot, el.dataset.action === 'home-prev' ? -1 : 1);
      if (next) change({view: 'home', snapshot: next, category: '全部', page: 0});
      window.scrollTo(0, 0); break;
    }
    case 'random-creator': {
      const choices = (state.view === 'creators' ? matchingCreators() : Object.values(COMMUNITY.catalog.creators)).filter(c => c.creator_id !== state.creator);
      if (!choices.length) { toast('当前筛选下没有其他账号，可以清除筛选再试。'); break; }
      const c = choices[Math.floor(Math.random() * choices.length)];
      change({view: 'profile', creator: c.creator_id, mode: 'browse', page: 0}); window.scrollTo(0, 0); break;
    }
    case 'clear-directory': change({q: '', directoryCategory: '全部', directoryModel: '全部'}); break;
    case 'explore-help': helpDialog.showModal(); break;
    case 'close-help': helpDialog.close(); break;
    case 'creator-kit': openCreatorKit(); break;
    case 'close-kit': kitDialog.close(); break;
    case 'share-page': {
      const path = state.view === 'profile' ? creatorHref(state.creator) : workHref(state.video);
      try { await navigator.clipboard.writeText(new URL(path, location.href).href); toast('链接已复制'); }
      catch { toast('可从地址栏复制当前页面链接'); }
      break;
    }
  }
});
document.addEventListener('input', event => {
  if (event.target.id === 'creator-query') updateDirectory();
  if (event.target.closest('#kit-form')) updateKit();
});
document.addEventListener('change', event => {
  if (['creator-category', 'creator-model'].includes(event.target.id)) updateDirectory();
  if (event.target.id === 'creator-order') change({order: event.target.value});
});
document.addEventListener('submit', event => {
  if (event.target.id === 'creator-search') { event.preventDefault(); updateDirectory(); }
  if (event.target.id !== 'kit-form') return;
  event.preventDefault();
  if (!event.target.reportValidity()) return;
  const config = kitConfig();
  if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/.test(config.run_id) || config.creators[0].name.length < 2
      || !config.creators[0].category || config.creators[0].personality.length < 2 || config.creators[0].goal.length < 4) {
    $('kit-status').textContent = '请填完整文件名、账号名和创作设定。'; return;
  }
  const url = URL.createObjectURL(new Blob([$('kit-preview').value], {type: 'application/yaml;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = config.run_id + '.yaml';
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  $('kit-status').textContent = '配置已下载。按下方说明在本机运行。';
});
// Capture errors never recurse: the failed image is removed and its text cover restored.
document.addEventListener('error', event => {
  const img = event.target;
  if (!(img instanceof HTMLImageElement)) return;
  const cover = img.closest('.community-cover');
  if (cover) { img.remove(); cover.classList.remove('photo', 'native-art'); cover.classList.add('label-cover'); return; }
  const card = img.closest('.lead-card');
  const w = card && AiliAiliBrowse.record(CATALOG, card.dataset.videoId) ? CATALOG[card.dataset.videoId] : null;
  if (w) img.replaceWith(Object.assign(document.createElement('span'), {className: 'lead-image-fallback', textContent: w.title}));
}, true);
// recommendation-ui.js initializes both browsing modes, then performs first render.
