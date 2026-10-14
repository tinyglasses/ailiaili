'use strict';
// Navigation state is explicit in the link. Page rendering never consumes randomness.
const recommendationData = globalThis.AiliAiliRecommendationData;
const newVisitSeed = () => crypto.getRandomValues(new Uint32Array(1))[0];
const interestCategories = [...new Set(Object.values(CATALOG).map(w => w.category))];
Object.assign(state, {
  feed: params.get('feed') === 'dynamic' || (state.view === 'home' && !params.has('snapshot') && params.get('feed') !== 'history') ? 'dynamic' : 'history',
  phase: params.get('phase') === 'late' ? 'late' : 'early',
  seed: /^\d{1,10}$/.test(params.get('seed') || '') && Number(params.get('seed')) <= 4294967295 ? Number(params.get('seed')) : newVisitSeed(),
  interest: interestCategories.includes(params.get('interest')) ? params.get('interest') : '',
  frozen: params.get('cards') || '', rec: AiliAiliRecommend.version, data_rev: recommendationData.revision,
});
if (state.feed === 'dynamic') {
  state.day = Math.max(1, state.day);
  if ((params.has('rec') && params.get('rec') !== state.rec) || (params.has('data') && params.get('data') !== state.data_rev)) {
    state.feed = 'history'; state.frozen = '';
    routeNotice = '这个推荐链接使用了不同的数据或规则版本。请用保存链接时的项目版本打开；当前显示历史回放。';
  }
}
let activeVisit = null, activeVisitKey = '';
const recordedActivate = activateSnapshot;
activateSnapshot = function () {
  if (state.feed !== 'dynamic') { recordedActivate(); return; }
  state.day = Math.round(clamp(state.day, 1, recommendationData.max_day));
  state.snapshot = AiliAiliBrowse.resolveSnapshot(COMMUNITY.snapshots, null, state.day).snapshot;
  const key = JSON.stringify([state.day, state.phase, state.seed, state.interest, state.frozen]);
  if (key !== activeVisitKey) {
    activeVisit = AiliAiliRecommend.visit(recommendationData, state);
    if (state.frozen) {
      try { activeVisit.cards = AiliAiliRecommend.unpack(state.frozen, recommendationData, activeVisit.catalog_through_day); }
      catch {
        state.feed = 'history'; state.frozen = '';
        routeNotice = '固定页链接不完整或含有当时尚未发布的作品，已切回这一天的历史回放。';
        recordedActivate(); return;
      }
    }
    activeVisitKey = key;
  }
  COMMUNITY.session = {
    session_id: null, day: state.day, catalog_through_day: activeVisit.catalog_through_day,
    source_kind: 'browser_rule_visit', context: {delivery_phase: state.phase},
  };
  COMMUNITY.feed = {cards: activeVisit.cards, card_count: activeVisit.cards.length, order: 'browser rule visit, stable within this URL'};
};
const recordedRoute = route;
function addVisitParams(url) {
  url.searchParams.set('feed', state.feed);
  if (state.feed === 'dynamic') {
    url.searchParams.delete('snapshot');
    for (const [key, value] of Object.entries({phase: state.phase, seed: state.seed, rec: state.rec, data: state.data_rev})) url.searchParams.set(key, String(value));
    if (state.interest) url.searchParams.set('interest', state.interest); else url.searchParams.delete('interest');
    if (state.frozen) url.searchParams.set('cards', state.frozen); else url.searchParams.delete('cards');
  }
  return url.pathname + url.search;
}
route = () => addVisitParams(new URL(recordedRoute(), location.href));
const recordedCreatorHref = creatorHref, recordedWorkHref = workHref;
creatorHref = id => addVisitParams(new URL(recordedCreatorHref(id), location.href));
workHref = id => addVisitParams(new URL(recordedWorkHref(id), location.href));
const recordedChange = change;
change = function (input) {
  const patch = {...input};
  const visiting = (patch.feed || state.feed) === 'dynamic';
  if (visiting && patch.snapshot && AiliAiliBrowse.record(COMMUNITY.snapshots, patch.snapshot)) patch.day = COMMUNITY.snapshots[patch.snapshot].session.day;
  if (visiting && patch.day !== undefined) patch.day = Math.round(clamp(patch.day, 1, recommendationData.max_day));
  if (['day', 'phase', 'seed', 'interest', 'feed'].some(key => patch[key] !== undefined && patch[key] !== state[key])) patch.frozen = '';
  if (patch.phase !== undefined) patch.phase = patch.phase === 'late' ? 'late' : 'early';
  if (patch.interest !== undefined && !interestCategories.includes(patch.interest)) patch.interest = '';
  if (patch.feed !== undefined) patch.feed = patch.feed === 'dynamic' ? 'dynamic' : 'history';
  if (patch.seed !== undefined && (!Number.isInteger(patch.seed) || patch.seed < 0 || patch.seed > 4294967295)) patch.seed = newVisitSeed();
  // New visits are actual navigation; changing layout/progress remains replaceState.
  const navigated = ['seed', 'feed', 'phase', 'interest'].some(key => patch[key] !== undefined && patch[key] !== state[key]);
  const oldUrl = location.href, oldState = {...state};
  const result = recordedChange(patch);
  if (navigated && ['view', 'creator', 'video', 'snapshot', 'page'].every(key => oldState[key] === state[key])) {
    const next = route(); history.replaceState(null, '', oldUrl); history.pushState(null, '', next);
  }
  return result;
};
const recordedSources = fullSources;
fullSources = function () {
  const sources = recordedSources();
  if (state.feed !== 'dynamic') return sources;
  return {...sources,
    source_kind: 'browser_rule_visit', homepage_session_id: null, recorded_feed_day: null,
    catalog_through_day: contentDay(), statistics_through_day: metricDay(),
    recommendation: {
      rules_version: state.rec, data_revision: state.data_rev, seed: state.seed,
      phase: state.phase, interest_categories: state.interest ? [state.interest] : [],
      visitor_kind: 'new_visitor', followed_creator_ids: [], excluded_video_ids: [], shown_history: {},
      frozen: Boolean(state.frozen), eligible_count: activeVisit.eligible_count,
      cards: COMMUNITY.feed.cards, inputs: recommendationData.sources,
      random_stream: 'lcg32-v1; not the recorded Python session stream',
    },
    visual_note: '基于原模拟规则与当时数据生成的新访问；不是历史观众实录。固定页保存卡片及顺序，浏览不写回模拟。封面为后期呈现。',
  };
};
window.AiliAiliStage.setState = change;
window.AiliAiliStage.getSources = fullSources;
const recordedEnrich = enrichExploreUI;
enrichExploreUI = function () {
  recordedEnrich();
  if (state.mode === 'film' || state.view !== 'home') return;
  const dynamic = state.feed === 'dynamic';
  const note = document.querySelector('.home-timeline-note');
  if (dynamic) note.textContent = `Day ${state.day} · ${state.phase === 'early' ? '当天投稿前' : '当天投稿后'} · 从 ${activeVisit.eligible_count} 份已发布作品中推荐 · 互动统计截至 Day ${metricDay()}${state.frozen ? ' · 此页已固定' : ''}`;
  const toolbar = `<section class="visit-toolbar" aria-label="首页浏览方式">
    <div class="visit-top"><div class="visit-modes" role="group" aria-label="选择浏览方式"><button data-action="visit-dynamic" aria-pressed="${dynamic}">逛这个世界</button><button data-action="visit-history" aria-pressed="${!dynamic}">历史回放</button></div>
    ${dynamic ? `<div class="visit-actions"><button class="button secondary" data-action="visit-next">换一批 ${arrow}</button><button class="button quiet" data-action="visit-freeze">${state.frozen ? '复制固定页链接' : '固定这一页'}</button></div>` : ''}</div>
    ${dynamic ? `<div class="visit-options"><label>访问时段<select id="visit-phase"><option value="early" ${state.phase === 'early' ? 'selected' : ''}>当天投稿前</option><option value="late" ${state.phase === 'late' ? 'selected' : ''}>当天投稿后</option></select></label>
    <label>想多看一点<select id="visit-interest"><option value="">随便逛逛</option>${interestCategories.map(c => `<option value="${esc(c)}" ${state.interest === c ? 'selected' : ''}>${esc(c)}</option>`).join('')}</select></label>
    <span class="visit-description">按当时的推荐规则重新逛一遍。喜欢这页，就固定下来分享或拍摄。</span></div>` : '<p class="visit-description">同一位 AI 观众每天实际收到的首页，保留当时的 40 张卡片与顺序。</p>'}</section>`;
  $('surface').insertAdjacentHTML('afterbegin', toolbar);
};
document.addEventListener('click', async event => {
  const el = event.target.closest('[data-action]');
  if (!el) return;
  switch (el.dataset.action) {
    case 'visit-dynamic': change({feed: 'dynamic', page: 0, category: '全部'}); break;
    case 'visit-history': change({feed: 'history', page: 0, category: '全部'}); break;
    case 'visit-next': {
      let seed = newVisitSeed(); if (seed === state.seed) seed = (seed + 1) >>> 0;
      change({seed, page: 0, category: '全部'}); toast('已按这一天的规则换了一批'); break;
    }
    case 'visit-freeze': {
      change({frozen: AiliAiliRecommend.freeze(COMMUNITY.feed.cards)});
      try { await navigator.clipboard.writeText(location.href); toast('这页已固定，链接已复制；页面工具中可进入取景'); }
      catch { toast('这页已固定，可复制地址栏链接；页面工具中可进入取景'); }
      break;
    }
  }
});
document.addEventListener('change', event => {
  if (event.target.id === 'visit-phase') change({phase: event.target.value, category: '全部', page: 0});
  if (event.target.id === 'visit-interest') change({interest: event.target.value, category: '全部', page: 0});
});
render(true);
