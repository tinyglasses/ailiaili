from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest
import yaml
from sqlalchemy import func, select

from ailiaili.config import SimulationConfig
from ailiaili.experiment_setup import TEMPLATES, initialize_world, plan_world
from ailiaili.persistence.models import CreatorRow, HomepageSessionRow, RunRow, VideoRow
from ailiaili.persona import CreatorPopulation
from ailiaili.simple_config import SimpleWorld, compile_world, prepare_world, yaml_text
from ailiaili.simulation.runner import SimulationRunner
from fake_world import FakeWorldProvider

ROOT = Path(__file__).resolve().parents[1]


def script(path, monkeypatch):
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('template', list(TEMPLATES))
def test_starters_are_complete_and_planning_is_offline(tmp_path, template):
    source = initialize_world('first-world', template, tmp_path / 'world.yaml')
    before = set(tmp_path.rglob('*'))
    plan = plan_world(source)
    assert plan['mode'] == 'stub'
    assert plan['logical_model_calls_for_full_schedule'] == {'min': 0, 'max': 0}
    assert set(tmp_path.rglob('*')) == before
    with pytest.raises(ValueError, match='already exists'):
        initialize_world('second-world', template, source)


def test_creation_approach_and_identity_overrides_enter_real_personas(tmp_path):
    source = initialize_world('identity', 'workshop', tmp_path / 'world.yaml')
    raw = yaml.safe_load(source.read_text(encoding='utf-8'))
    raw['creators'][0]['boundary'] = '我不愿为了方便传播而隐藏反例和结论的适用条件。'
    raw['creators'][0]['audience'] = '喜欢追问原因并愿意自己核对证据的读者。'
    raw['creators'][0]['tension'] = '我想解释清楚，又担心太多细节让真正好奇的人失去耐心。'
    documents = compile_world(SimpleWorld.model_validate(raw), tmp_path / 'prepared')
    people = CreatorPopulation.model_validate(documents['creators.yaml']).creators
    assert len({p.creative_mode for p in people}) == 3
    assert len({p.primary_drive for p in people}) == 3
    prompt = people[0].private_prompt_dict()
    assert prompt['你不愿轻易越过的身份底线'] == raw['creators'][0]['boundary']
    assert prompt['你想找到的观众'] == raw['creators'][0]['audience']
    assert prompt['你长期带着的矛盾'] == raw['creators'][0]['tension']


def test_workload_plan_bounds_actual_complete_model_flow(tmp_path):
    source = initialize_world('planned', 'workshop', tmp_path / 'world.yaml')
    raw = yaml.safe_load(source.read_text(encoding='utf-8'))
    raw.update(mode='llm', days=3, output_root=str(tmp_path / 'runs'))
    raw['models']['main'].update(model_id='test/model', input_per_million=0.001, output_per_million=0.001)
    source.write_text(yaml_text(raw), encoding='utf-8')
    plan = plan_world(source)
    prepare_world(source, tmp_path / 'prepared')
    config = SimulationConfig.from_yaml(tmp_path / 'prepared/world.yaml')
    provider = FakeWorldProvider()
    runner = SimulationRunner.with_llm(config, provider=provider)
    runner.run_through()
    with runner.database.session() as session:
        assert session.scalar(select(func.count()).select_from(VideoRow)) == plan['planned_works_total']
        assert session.scalar(select(func.count()).select_from(HomepageSessionRow)) == plan['planned_browse_sessions']
    bound = plan['logical_model_calls_for_full_schedule']
    assert bound['min'] <= len(provider.calls) <= bound['max']
    assert plan['model_routes'][0]['key_variable'] == 'AILIAILI_MODEL_API_KEY'


def test_share_bundle_excludes_private_state_and_keeps_complete_reading(tmp_path, monkeypatch):
    share = script(ROOT / 'tools/share_run.py', monkeypatch)
    source = initialize_world('shareable', 'stories', tmp_path / 'world.yaml')
    raw = yaml.safe_load(source.read_text(encoding='utf-8'))
    raw.update(days=2, output_root=str(tmp_path / 'runs'))
    source.write_text(yaml_text(raw), encoding='utf-8')
    prepare_world(source, tmp_path / 'prepared')
    config = SimulationConfig.from_yaml(tmp_path / 'prepared/world.yaml')
    runner = SimulationRunner(config)
    runner.run_through()
    with runner.database.session() as session, session.begin():
        run = session.get(RunRow, config.run_id)
        run.config_json = {**run.config_json, 'private_notes': 'never-publish-settings-sentinel'}
        actor = session.scalar(select(CreatorRow))
        actor.memory_json = {'private': 'never-publish-memory-sentinel'}
    (config.run_dir / '.env').write_text('never-publish-file-sentinel', encoding='utf-8')
    (config.run_dir / 'raw.log').write_text('never-publish-log-sentinel', encoding='utf-8')
    before = config.database_path.read_bytes()
    result = share.share_run(config.run_dir, tmp_path / 'share')
    with zipfile.ZipFile(result['archive']) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        assert 'ailiaili-world/reading/creators/creator_001.md' in names
        assert not any(n.endswith(('.db', '.log', '.yaml', '.env')) for n in names)
        assert not any(b'never-publish-' in archive.read(n) for n in names)
        world = json.loads(archive.read('ailiaili-world/data/world.json'))
        assert world['experiment_settings']['seed'] == raw['seed']
        assert 'private_notes' not in world['experiment_settings']
        assert len(world['community']['catalog']['works']) == result['works']
        reading = archive.read('ailiaili-world/reading/creators/creator_001.md').decode('utf-8')
        first = next(w for w in world['community']['catalog']['works'].values() if w['creator_id'] == 'creator_001')
        assert first['outline'][1] in reading
    assert config.database_path.read_bytes() == before
    with pytest.raises(ValueError, match='new output'):
        share.share_run(config.run_dir, tmp_path / 'share')


def comparison_fixture():
    return {'run_id': 'left', 'last_completed_day': 2, 'experiment_settings': {'seed': 42},
            'community': {'catalog': {'creators': {
                'a': {'name': 'A'}, 'b': {'name': 'B'}}, 'works': {}},
                'metrics': {'columns': ['impressions', 'clicks', 'new_follows', 'triples'],
                            'creators': {'a': [[0, 0, 0, 0], [100, 10, 0, 0], [200, 20, 0, 0]],
                                         'b': [[0, 0, 0, 0], [10, 5, 0, 0], [20, 10, 0, 0]]}}}}


def test_comparison_aligns_days_and_uses_weighted_totals(tmp_path, monkeypatch):
    compare = script(ROOT / 'examples/data/compare_worlds.py', monkeypatch)
    left = comparison_fixture()
    right = copy.deepcopy(left)
    right.update(run_id='right', last_completed_day=1)
    right['experiment_settings']['seed'] = 7
    result = compare.compare_worlds(left, right)
    assert result['comparison_day'] == 1
    assert result['left']['ctr'] == pytest.approx(15 / 110)
    assert result['left']['ctr'] != pytest.approx((0.1 + 0.5) / 2)
    assert result['public_settings_differ']['seed'] == {'left': 42, 'right': 7}
    assert compare.compare_worlds(left, right, 0)['left']['ctr'] is None
    with pytest.raises(ValueError, match='common metric day'):
        compare.compare_worlds(left, right, 2)
    right['community']['metrics']['creators'] = {}
    with pytest.raises(ValueError, match='complete cumulative'):
        compare.compare_worlds(left, right)
