"""Rule parity with the Python engine under the same injected random stream.

The browser intentionally does not replay Python's old session RNG. We substitute
only RNG here, leaving the production Python ranking and lane code untouched.
"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from ailiaili.config import SimulationConfig
from ailiaili.domain import VideoStats
from ailiaili.recommendation import Recommender

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which('node')


class VisitRandom:
    def __init__(self, seed):
        self.value = seed & 0xffffffff

    def random(self):
        self.value = (1664525 * self.value + 1013904223) & 0xffffffff
        return self.value / 4294967296

    def randrange(self, n):
        return int(self.random() * n)

    def shuffle(self, items):
        for i in range(len(items) - 1, 0, -1):
            j = self.randrange(i + 1)
            items[i], items[j] = items[j], items[i]


@pytest.mark.skipif(not NODE, reason='Node.js is needed for cross-language rule parity')
@pytest.mark.parametrize('scenario', ['all_cold', 'all_mature', 'mixed', 'personal', 'seen', 'small', 'ties_even'])
def test_browser_matches_original_rules(monkeypatch, tmp_path, scenario):
    monkeypatch.setattr('ailiaili.recommendation.random.Random', VisitRandom)
    config = SimulationConfig(
        run_id='browser-parity', seed=1, world_anchor_date='2026-08-12',
        creator_count=40, viewer_count=1, homepage_size=40,
        cold_impression_target=100, exploit_ratio=.6, cold_ratio=.2, explore_ratio=.2,
        max_videos_per_creator=2, interest_slots_max=4, following_slots_max=4,
        age_half_life_days=14, output_root=tmp_path,
    )
    if scenario == 'ties_even':
        config = config.model_copy(update={'homepage_size': 33, 'cold_ratio': .1, 'explore_ratio': .1, 'exploit_ratio': .8})
    videos = [dict(video_id=f'v{i:03}', creator_id=f'c{i//3:02}', category=f'category{i%4}', published_day=i % 40) for i in range(120)]
    if scenario == 'small':
        videos = videos[:6]
    stats = {}
    for i, video in enumerate(videos):
        exposure = 0 if scenario == 'all_cold' else 1000 if scenario == 'all_mature' else i * 9
        stats[video['video_id']] = VideoStats(impressions=exposure, clicks=i * 3,
            completed_follow_opportunities=i * 2, new_follows=i % 5,
            completed_post_click_opportunities=i * 3, triples=i % 7)
    interest = {'category2'} if scenario == 'personal' else set()
    following = {'c15', 'c18', 'c21'} if scenario == 'personal' else set()
    excluded = {'v008', 'v009'} if scenario == 'personal' else set()
    shown = {v['video_id']: i % 3 for i, v in enumerate(videos)} if scenario == 'seen' else {}
    seed = 20260926
    expected = Recommender(config).sample(videos=videos, stats_by_video=stats, current_day=40,
        seed=seed, interest_categories=interest, followed_creator_ids=following,
        excluded_video_ids=excluded, shown_history=shown)
    payload = dict(videos=videos, stats={k: v.model_dump() for k, v in stats.items()}, day=40,
        seed=seed, rules=config.model_dump(mode='json'), interests=list(interest),
        following=list(following), excluded=list(excluded), shown=shown)
    result = subprocess.run([NODE, '-e',
        "const r=require('./site/recommendation-core.js'); const p=JSON.parse(require('fs').readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(r.sample(p)));"],
        cwd=ROOT, input=json.dumps(payload), text=True, encoding='utf-8', capture_output=True, check=True)
    assert json.loads(result.stdout) == [item.model_dump() for item in expected]


def test_exported_browser_counts_match_public_inputs():
    spec = importlib.util.spec_from_file_location('recommendation_export', ROOT / 'tools/build_recommendation_data.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    actual = (ROOT / 'site/recommendation-data.js').read_text(encoding='utf-8')
    actual = json.loads(actual.split('globalThis.AiliAiliRecommendationData=', 1)[1].strip().removesuffix(';'))
    assert actual == builder.build(ROOT)


def test_browser_revision_ignores_checkout_line_endings_but_tracks_content(tmp_path):
    spec = importlib.util.spec_from_file_location('recommendation_export', ROOT / 'tools/build_recommendation_data.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    paths = ['data/world.json', 'data/daily_metrics.csv', 'configs/reference/world.yaml']
    for relative in paths:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_text(encoding='utf-8').encode('utf-8'))
    lf = builder.build(tmp_path)
    for relative in paths:
        target = tmp_path / relative
        target.write_bytes(target.read_bytes().replace(b'\n', b'\r\n'))
    assert builder.build(tmp_path) == lf
    config = tmp_path / 'configs/reference/world.yaml'
    with config.open('ab') as stream:
        stream.write(b'\r\n# Changed source content\r\n')
    assert builder.build(tmp_path)['revision'] != lf['revision']
