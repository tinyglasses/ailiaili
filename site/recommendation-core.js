/* Browser rule port of src/ailiaili/recommendation.py. No network or mutable world state.
 * New visits use a versioned LCG stream, not Python's historical random stream.
 * Retain v1 behavior for saved links; bump the version if sampling rules change. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AiliAiliRecommend = api;
})(globalThis, function () {
  'use strict';
  const version = 'v1';
  const fields = ['impressions', 'clicks', 'completed_follow_opportunities', 'new_follows', 'completed_post_click_opportunities', 'triples'];
  function random(seed) {
    let value = Number(seed) >>> 0;
    return () => { value = (Math.imul(1664525, value) + 1013904223) >>> 0; return value / 4294967296; };
  }
  function roundEven(n) { const low = Math.floor(n); return n - low === 0.5 ? low + low % 2 : Math.round(n); }
  function weight(video, stats, day, halfLife) {
    const s = stats[video.video_id] || {};
    const performance = 0.6 * (((s.clicks || 0) + 1) / ((s.impressions || 0) + 10))
      + 0.2 * (((s.new_follows || 0) + 1) / ((s.completed_follow_opportunities || 0) + 20))
      + 0.2 * (((s.triples || 0) + 0.6) / ((s.completed_post_click_opportunities || 0) + 20));
    return Math.max(performance * 0.5 ** (Math.max(0, day - video.published_day) / halfLife), 1e-9);
  }
  function sample({videos, stats = {}, day, rules: c, seed, interests = [], following = [], excluded = [], shown = {}, rng = random(seed)}) {
    const excludedIds = new Set(excluded), interestSet = new Set(interests), followingSet = new Set(following);
    const all = videos.filter(v => !excludedIds.has(v.video_id)).slice().sort((a, b) => a.video_id < b.video_id ? -1 : a.video_id > b.video_id ? 1 : 0);
    const fresh = all.filter(v => !Object.hasOwn(shown, v.video_id));
    const selected = [], ids = new Set(), counts = new Map();
    const isCold = v => (stats[v.video_id]?.impressions || 0) < c.cold_impression_target;
    const coldWeight = v => 1 / (1 + (stats[v.video_id]?.impressions || 0));
    const performance = v => weight(v, stats, day, c.age_half_life_days);
    const bucket = v => isCold(v) ? 'cold' : 'exploit';
    function pick(pool, count, weightFn, bucketFn, source = 'global', limit = c.homepage_size) {
      let available = pool.filter(v => !ids.has(v.video_id));
      for (let n = 0; n < count && selected.length < limit; n++) {
        const valid = available.filter(v => (counts.get(v.creator_id) || 0) < c.max_videos_per_creator);
        if (!valid.length) return;
        const weights = valid.map(v => Math.max(0, weightFn(v))), total = weights.reduce((a, b) => a + b, 0);
        let chosen = valid[valid.length - 1];
        if (total <= 0) chosen = valid[Math.floor(rng() * valid.length)];
        else {
          let cursor = rng() * total;
          for (let i = 0; i < valid.length; i++) { cursor -= weights[i]; if (cursor <= 0) { chosen = valid[i]; break; } }
        }
        ids.add(chosen.video_id); counts.set(chosen.creator_id, (counts.get(chosen.creator_id) || 0) + 1);
        selected.push({video_id: chosen.video_id, bucket: typeof bucketFn === 'function' ? bucketFn(chosen) : bucketFn, source});
        available = available.filter(v => v !== chosen);
      }
    }
    function fallback(pool, limit = c.homepage_size) {
      pick(pool.filter(isCold), limit - selected.length, coldWeight, 'cold', 'global', limit);
      pick(pool, limit - selected.length, performance, bucket, 'global', limit);
    }
    const target = c.homepage_size - c.interest_slots_max - c.following_slots_max;
    const cold = roundEven(target * c.cold_ratio), explore = roundEven(target * c.explore_ratio);
    pick(fresh.filter(isCold), cold, coldWeight, 'cold');
    pick(fresh, explore, () => 1, 'explore');
    pick(fresh.filter(v => !isCold(v)), target - cold - explore, performance, 'exploit', 'global', target);
    fallback(fresh, target);
    pick(fresh.filter(v => followingSet.has(v.creator_id)), c.following_slots_max, performance, bucket, 'following');
    pick(fresh.filter(v => interestSet.has(v.category)), c.interest_slots_max, performance, bucket, 'interest');
    fallback(fresh);
    const lastShown = [...new Set(all.filter(v => Object.hasOwn(shown, v.video_id) && !ids.has(v.video_id)).map(v => shown[v.video_id]))].sort((a, b) => a - b);
    for (const at of lastShown) pick(all.filter(v => shown[v.video_id] === at), c.homepage_size - selected.length, performance, bucket);
    for (let i = selected.length - 1; i > 0; i--) { const j = Math.floor(rng() * (i + 1)); [selected[i], selected[j]] = [selected[j], selected[i]]; }
    return selected;
  }
  function cumulative(data, throughDay) {
    const stats = Object.create(null);
    for (const [id, rows] of Object.entries(data.daily)) {
      const values = [0, 0, 0, 0, 0, 0];
      for (const row of rows) { if (row[0] > throughDay) break; for (let i = 0; i < 6; i++) values[i] += row[i + 1]; }
      stats[id] = Object.fromEntries(fields.map((field, i) => [field, values[i]]));
    }
    return stats;
  }
  function visit(data, {day, phase = 'early', seed, interest = ''}) {
    if (!Number.isInteger(day) || day < 1 || day > data.max_day || !['early', 'late'].includes(phase)) throw new Error('Invalid visit date or phase');
    if (!Number.isInteger(seed) || seed < 0 || seed > 4294967295) throw new Error('Invalid seed');
    const catalogThrough = day - Number(phase === 'early');
    const videos = Object.entries(data.catalog).map(([video_id, [creator_id, category, published_day]]) => ({video_id, creator_id, category, published_day})).filter(v => v.published_day <= catalogThrough);
    const cards = sample({videos, stats: cumulative(data, day - 1), day, rules: data.rules, seed, interests: interest ? [interest] : []});
    return {cards: cards.map((card, i) => ({...card, position: i + 1})), catalog_through_day: catalogThrough, stats_through_day: day - 1, eligible_count: videos.length};
  }
  function freeze(cards) {
    const bucketCode = {cold: 'c', exploit: 'e', explore: 'x'};
    return cards.map(c => [c.video_id, bucketCode[c.bucket], c.source[0]].join('~')).join(',');
  }
  function unpack(value, data, cutoff) {
    if (typeof value !== 'string' || !value || value.length > 8000) throw new Error('Invalid frozen feed');
    const cards = value.split(',').map((item, i) => {
      const parts = item.split('~'), [video_id, b, s] = parts;
      const bucket = {c: 'cold', e: 'exploit', x: 'explore'}[b], source = {g: 'global', i: 'interest', f: 'following'}[s];
      if (parts.length !== 3 || !Object.hasOwn(data.catalog, video_id) || data.catalog[video_id][2] > cutoff || !['c', 'e', 'x'].includes(b) || !['g', 'i', 'f'].includes(s)) throw new Error('Invalid frozen card');
      return {video_id, bucket, source, position: i + 1};
    });
    const available = new Map();
    for (const [creator, , day] of Object.values(data.catalog)) if (day <= cutoff) available.set(creator, (available.get(creator) || 0) + 1);
    const expected = Math.min(data.rules.homepage_size, [...available.values()].reduce((n, count) => n + Math.min(count, data.rules.max_videos_per_creator), 0));
    if (cards.length !== expected || new Set(cards.map(c => c.video_id)).size !== cards.length) throw new Error('Invalid frozen count');
    const counts = new Map();
    for (const c of cards) { const id = data.catalog[c.video_id][0]; counts.set(id, (counts.get(id) || 0) + 1); }
    if ([...counts.values()].some(n => n > data.rules.max_videos_per_creator)) throw new Error('Invalid frozen creator count');
    return cards;
  }
  return {version, random, weight, sample, cumulative, visit, freeze, unpack};
});
