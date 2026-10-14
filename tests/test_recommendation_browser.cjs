const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const api = require('../site/recommendation-core.js');
require('../site/recommendation-data.js');
const data = globalThis.AiliAiliRecommendationData;
const world = JSON.parse(fs.readFileSync(require('node:path').join(__dirname, '../data/world.json'), 'utf8'));

test('every visit respects catalog time, size, uniqueness and author cap', () => {
  for (let day = 1; day <= 60; day++) for (const phase of ['early', 'late']) {
    const result = api.visit(data, {day, phase, seed: day});
    assert.equal(result.cards.length, 40);
    assert.equal(result.stats_through_day, day - 1);
    assert.equal(result.catalog_through_day, day - Number(phase === 'early'));
    assert.equal(new Set(result.cards.map(c => c.video_id)).size, 40);
    const counts = new Map();
    for (const card of result.cards) {
      const w = world.community.catalog.works[card.video_id];
      assert(w.published_day <= result.catalog_through_day);
      counts.set(w.creator_id, (counts.get(w.creator_id) || 0) + 1);
    }
    assert(Math.max(...counts.values()) <= 2);
  }
});
test('same options replay; a new batch changes selection without mutating history', () => {
  const before = JSON.stringify(world.community.snapshots);
  const options = {day: 40, phase: 'early', seed: 2026092601};
  const first = api.visit(data, options), again = api.visit(data, options);
  assert.deepEqual(first, again);
  assert.equal(first.eligible_count, 1353);
  const next = api.visit(data, {...options, seed: 2026092602});
  assert.notDeepEqual(first.cards, next.cards);
  assert.equal(JSON.stringify(world.community.snapshots), before);
});
test('future metrics and feedback opportunities cannot leak into an earlier visit', () => {
  const before = api.visit(data, {day: 40, seed: 17});
  const changed = {...data, daily: Object.fromEntries(Object.entries(data.daily).map(([id, rows]) =>
    [id, rows.map(row => row[0] >= 40 ? [row[0], 99999999, 0, 99999999, 0, 99999999, 0] : row)]))};
  assert.deepEqual(api.visit(changed, {day: 40, seed: 17}), before);
});
test('interest is a thin lane, not a filter replacing the whole homepage', () => {
  const interest = data.catalog[Object.keys(data.catalog)[0]][1];
  const result = api.visit(data, {day: 40, phase: 'late', seed: 4, interest});
  const extra = result.cards.filter(c => c.source === 'interest');
  assert(extra.length > 0 && extra.length <= 4);
  assert(extra.every(c => data.catalog[c.video_id][1] === interest));
  assert(result.cards.some(c => data.catalog[c.video_id][1] !== interest));
});
test('frozen cards preserve exact order and bucket labels and reject invalid future cards', () => {
  const {cards, catalog_through_day} = api.visit(data, {day: 40, phase: 'early', seed: 8});
  const packed = api.freeze(cards);
  assert.deepEqual(api.unpack(packed, data, catalog_through_day), cards);
  assert.throws(() => api.unpack(packed.split(',').slice(0, 39).join(','), data, catalog_through_day));
  assert.throws(() => api.unpack(packed + ',' + packed.split(',')[0], data, catalog_through_day));
  const future = Object.keys(data.catalog).find(id => data.catalog[id][2] === 60);
  assert.throws(() => api.unpack(`${future}~e~g`, data, 39));
  assert.throws(() => api.unpack('__proto__~e~g', data, 60));
  assert.throws(() => api.unpack(packed.replace('~g', '~invalid'), data, 60));
});
test('days and seeds outside the supported world are rejected', () => {
  for (const day of [-1, 0, 61, 1.5, NaN]) assert.throws(() => api.visit(data, {day, seed: 1}));
  for (const seed of [-1, 4294967296, 1.5, NaN]) assert.throws(() => api.visit(data, {day: 40, seed}));
});
