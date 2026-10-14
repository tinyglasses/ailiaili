/* Pure browsing helpers. The simulation and recorded homepage order are unchanged. */
(function (root) {
  'use strict';
  const own = (object, key) => typeof key === 'string' && object != null && Object.hasOwn(object, key);
  const record = (object, key) => own(object, key) && object[key] !== null && typeof object[key] === 'object' && !Array.isArray(object[key]);
  function snapshots(source) {
    return Object.entries(source || {}).filter(([id, value]) => record(source, id)
      && value.session?.session_id === id && Number.isInteger(value.session.day)
      && value.session.day >= 0 && Array.isArray(value.feed?.cards))
      .map(([, value]) => value).sort((a, b) => a.session.day - b.session.day);
  }
  function resolveSnapshot(source, requestedId, requestedDay) {
    const valid = snapshots(source);
    if (!valid.length) throw new Error('No recorded homepage snapshots are available.');
    const byId = requestedId && valid.find(s => s.session.session_id === requestedId);
    const hasDay = requestedDay !== null && requestedDay !== undefined && requestedDay !== '';
    const day = hasDay ? Number(requestedDay) : null;
    const byDay = hasDay && valid.find(s => s.session.day === day);
    const chosen = byId || byDay || valid[valid.length - 1];
    let notice = '';
    if (requestedId && !byId) notice = `这个首页快照链接不可用，已显示 Day ${chosen.session.day} 的记录。`;
    else if (byId && hasDay && day !== chosen.session.day) {
      notice = `链接里的日期与首页快照不一致，已按快照显示 Day ${chosen.session.day}。`;
    } else if (hasDay && (!Number.isInteger(day) || day !== chosen.session.day)) {
      notice = `所选日期没有对应的首页快照，已显示 Day ${chosen.session.day} 的记录。作者和作品仍可按观察日浏览。`;
    }
    return {snapshot: chosen.session.session_id, day: chosen.session.day, notice};
  }
  function adjacentSnapshot(source, currentId, direction) {
    if (direction !== -1 && direction !== 1) return null;
    const available = snapshots(source);
    const index = available.findIndex(s => s.session.session_id === currentId);
    return index < 0 ? null : (available[index + direction]?.session.session_id || null);
  }
  const normalize = value => String(value || '').normalize('NFKC').trim().toLocaleLowerCase('zh-CN');
  function filterCreators(creators, query = '', category = '全部', model = '全部') {
    const needle = normalize(query);
    return Object.values(creators).filter(c => record(creators, c.creator_id)
      && (category === '全部' || c.category === category)
      && (model === '全部' || c.model_alias === model)
      && (!needle || normalize([c.creator_id, c.name, c.category, c.model_alias,
        ...Object.values(c.persona || {}), ...Object.values(c.declaration || {})].join(' ')).includes(needle)))
      .sort((a, b) => a.creator_id.localeCompare(b.creator_id));
  }
  function sequence(catalog, creatorId, day) {
    return Object.values(catalog).filter(w => w.creator_id === creatorId && w.published_day <= day)
      .sort((a, b) => a.published_day - b.published_day || a.video_id.localeCompare(b.video_id));
  }
  function starterConfig(creator, works, edits = {}) {
    const persona = creator.persona || {}, declaration = creator.declaration || {};
    const shorten = (value, max) => Array.from(String(value)).slice(0, max).join('');
    const primary = persona['最重要的创作欲望'];
    const approach = {'求真与掌握': 'explainer', '服务与效用': 'maker', '同类与关系': 'observer'}[primary] || 'storyteller';
    const c = {
      name: shorten(creator.name + '·新篇', 24), category: creator.category,
      approach,
      personality: shorten(persona['你的声音'] || '认真具体，保留自己的表达习惯', 100),
      goal: shorten(declaration['我想做的账号'] || persona['你的创作直觉'] || '从一件具体的小事展开自己的创作', 100),
      topics: works.slice(0, 2).map(w => shorten(w.title, 80)),
    };
    if (!c.topics.length) c.topics = ['从一件普通小事开始'];
    for (const [key, source] of [['audience', '你想找到的观众'], ['boundary', '你不愿轻易越过的身份底线'], ['tension', '你长期带着的矛盾']]) {
      const limits = {audience: [8, 140], boundary: [8, 160], tension: [10, 200]};
      const text = shorten(persona[source] || '', limits[key][1]);
      if (text.length >= limits[key][0]) c[key] = text;
    }
    for (const key of ['name', 'category', 'personality', 'goal', 'approach']) {
      if (Object.hasOwn(edits, key)) c[key] = String(edits[key]).trim();
    }
    return {run_id: edits.run_id || 'my-' + creator.creator_id.replaceAll('_', '-'),
      mode: 'stub', days: 5, seed: 42, categories: [c.category], creators: [c],
      viewers: {count: 6, interests: [c.category], personality: '喜欢具体的作品，也愿意试读陌生作者。'},
      publish_every: 2, model: 'main', models: {main: {provider: 'openrouter',
        model_id: 'REPLACE_WITH_MODEL_ID', api_key_env: 'AILIAILI_MODEL_API_KEY',
        input_per_million: null, output_per_million: null}},
      budget: {hard_limit: 0.1, max_cost_per_call: 0.02}};
  }
  // Only the JSON-compatible values created above are serialized; strings stay quoted.
  function yaml(value, indent = 0) {
    const pad = ' '.repeat(indent);
    if (Array.isArray(value)) return value.map(v => typeof v === 'object' && v !== null
      ? pad + '-\n' + yaml(v, indent + 2) : pad + '- ' + JSON.stringify(v) + '\n').join('');
    return Object.entries(value).map(([key, v]) => pad + key + ':' +
      (typeof v === 'object' && v !== null ? '\n' + yaml(v, indent + 2) : ' ' + JSON.stringify(v) + '\n')).join('');
  }
  const api = Object.freeze({own, record, snapshots, resolveSnapshot, adjacentSnapshot, filterCreators, sequence, starterConfig, yaml});
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.AiliAiliBrowse = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
