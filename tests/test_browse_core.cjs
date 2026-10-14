const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const api = require('../site/browse-core.js');
const world = JSON.parse(fs.readFileSync(path.join(__dirname, '../data/world.json'), 'utf8'));
const {creators, works} = world.community.catalog;

test('every published creator is discoverable regardless of homepage selection', () => {
  assert.equal(api.filterCreators(creators).length, 100);
  for (const c of Object.values(creators)) {
    assert(api.filterCreators(creators, c.name).some(found => found.creator_id === c.creator_id));
  }
  assert.equal(api.filterCreators(creators, 'ｎｐｃ')[0].creator_id, 'creator_038');
  assert.equal(api.filterCreators(creators, '没有这样的账号。xyz123').length, 0);
  assert(api.filterCreators(creators, '', '游戏').every(c => c.category === '游戏'));
  assert(api.filterCreators(creators, '', '全部', 'doubao_seed_2_1_turbo').every(c => c.model_alias === 'doubao_seed_2_1_turbo'));
});
test('untrusted URL keys never resolve inherited object properties', () => {
  for (const id of ['__proto__', 'constructor', 'toString', 'hasOwnProperty']) {
    assert.equal(api.record(creators, id), false);
    const result = api.resolveSnapshot(world.community.snapshots, id, 60);
    assert.equal(result.day, 60);
    assert(result.notice);
  }
});
test('unsupported homepage days are explicit and real snapshots keep their identity', () => {
  for (let day = 1; day <= 60; day++) {
    const result = api.resolveSnapshot(world.community.snapshots, null, String(day));
    assert.equal(result.day, day);
    assert.equal(result.notice, '');
    assert.equal(world.community.snapshots[result.snapshot].session.day, day);
  }
  for (const day of [0, 61, 20.5, -1, 'NaN']) {
    const result = api.resolveSnapshot(world.community.snapshots, null, day);
    assert.equal(result.day, 60);
    assert(result.notice);
  }
  const mismatch = api.resolveSnapshot(world.community.snapshots, 'home_d059_viewer_ref_005_m05', 20);
  assert.equal(mismatch.day, 59);
  assert(mismatch.notice.includes('不一致'));
});

test('all sixty recorded feeds preserve valid order, ownership and delivery cutoff', () => {
  const available = api.snapshots(world.community.snapshots);
  assert.deepEqual(available.map(s => s.session.day), Array.from({length: 60}, (_, i) => i + 1));
  for (const {session, feed} of available) {
    assert.equal(session.status, 'succeeded');
    assert.equal(session.viewer.viewer_id, 'viewer_ref_005_m05');
    assert.equal(feed.cards.length, 40);
    assert.equal(new Set(feed.cards.map(c => c.video_id)).size, 40);
    assert.equal(session.catalog_through_day, session.day - Number(session.context.delivery_phase === 'early'));
    for (const [index, card] of feed.cards.entries()) {
      assert.equal(card.position, index + 1);
      assert.equal(card.creator_id, works[card.video_id].creator_id);
      assert.equal(card.category, works[card.video_id].category);
      assert(works[card.video_id].published_day <= session.catalog_through_day);
    }
    assert(session.clicked_video_ids.every(id => feed.cards.some(c => c.video_id === id)));
  }
});

test('daily navigation reaches adjacent recorded days without wrapping at the ends', () => {
  const source = world.community.snapshots;
  const ids = api.snapshots(source).map(s => s.session.session_id);
  for (const [index, id] of ids.entries()) {
    assert.equal(api.adjacentSnapshot(source, id, -1), ids[index - 1] || null);
    assert.equal(api.adjacentSnapshot(source, id, 1), ids[index + 1] || null);
  }
  assert.equal(api.adjacentSnapshot(source, '__proto__', 1), null);
  assert.equal(api.adjacentSnapshot(source, ids[0], 0), null);
});
test('a story sequence respects account ownership, order and observation day', () => {
  const entries = api.sequence(works, 'creator_038', 20);
  assert(entries.length > 0);
  assert(entries.every(w => w.creator_id === 'creator_038' && w.published_day <= 20));
  assert(entries.every((w, i) => !i || w.published_day >= entries[i - 1].published_day));
  assert.equal(api.sequence(works, 'constructor', 60).length, 0);
});
test('role exports are new offline worlds, never a disguised continuation or model call', () => {
  const before = JSON.stringify(world);
  for (const c of Object.values(creators)) {
    const result = api.starterConfig(c, api.sequence(works, c.creator_id, 60));
    assert.equal(result.mode, 'stub');
    assert.equal(result.creators.length, 1);
    assert.equal(result.viewers.count, 6);
    assert.deepEqual(result.categories, [c.category]);
    assert.equal(result.models.main.model_id, 'REPLACE_WITH_MODEL_ID');
    assert.equal(result.models.main.input_per_million, null);
  }
  assert.equal(JSON.stringify(world), before);
});
test('edits and quotes remain data in the YAML export', () => {
  const output = api.starterConfig(creators.creator_038, [], {
    name: '有冒号：和引号"', personality: '第一行\n第二行', category: '我的分区',
    goal: '试着写一段自己的故事', run_id: 'my-safe-run', approach: 'observer'});
  assert.equal(output.creators[0].approach, 'observer');
  assert.equal(output.creators[0].personality, '第一行\n第二行');
  assert(api.yaml(output).includes('"第一行\\n第二行"'));
  assert.equal(output.run_id, 'my-safe-run');
});
