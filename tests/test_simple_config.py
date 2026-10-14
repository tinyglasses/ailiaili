from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from sqlalchemy import select

from ailiaili.config import SimulationConfig
from ailiaili.creator.service import genesis_observation_pack
from ailiaili.llm.config import ModelSpec
from ailiaili.llm.provider import OpenAICompatibleProvider, ProviderError, RoutingChatProvider
from ailiaili.persistence.models import CreatorRow, VideoRow, ViewerRow
from ailiaili.persona import CreatorPopulation, ViewerPopulationSpec
from ailiaili.simple_config import SimpleWorld, compile_world, prepare_world, yaml_text
from ailiaili.simulation.runner import SimulationRunner
from fake_world import FakeWorldProvider

ROOT = Path(__file__).resolve().parents[1]


def example(tmp_path, *, llm=False):
    raw = yaml.safe_load((ROOT / 'configs/custom-world.yaml').read_text(encoding='utf-8'))
    raw['output_root'] = str(tmp_path / 'runs')
    raw['days'] = 2
    if llm:
        raw['mode'] = 'llm'
        raw['models']['main'].update(model_id='test/first', input_per_million=0.001, output_per_million=0.001)
        raw['models']['other'] = {**raw['models']['main'], 'model_id': 'test/other'}
        raw['creators'][1]['model'] = 'other'
        raw['viewers']['model'] = 'other'
    source = tmp_path / 'small.yaml'
    source.write_text(yaml_text(raw), encoding='utf-8')
    return source, raw


def test_stub_custom_identity_categories_interest_and_resume(tmp_path):
    source, raw = example(tmp_path)
    destination = tmp_path / 'prepared'
    prepare_world(source, destination)
    config = SimulationConfig.from_yaml(destination / 'world.yaml')
    runner = SimulationRunner(config)
    assert len(runner.run_through(1)) == 1
    assert len(SimulationRunner(config).run_through(2)) == 1
    with runner.database.session() as session:
        creators = session.scalars(select(CreatorRow).order_by(CreatorRow.creator_id)).all()
        assert [c.name for c in creators] == [c['name'] for c in raw['creators']]
        assert creators[0].persona_json['style'] == raw['creators'][0]['personality']
        assert set(session.scalars(select(VideoRow.category))) == set(raw['categories'])
        profiles = [r.profile_json for r in session.scalars(select(ViewerRow))]
        assert all(set(p['interests']) == set(raw['categories']) for p in profiles)
    assert (config.run_dir / 'site/index.html').exists()
    # Existing destinations and changed frozen identities cannot silently replace a world.
    with pytest.raises(ValueError, match='already exists'):
        prepare_world(source, destination)
    changed = config.model_dump(mode='json')
    changed['stub_creators'][0]['style'] = 'a different personality'
    with pytest.raises(ValueError, match='config'):
        SimulationRunner(SimulationConfig.model_validate(changed)).initialize()


def test_llm_custom_prompts_routes_and_results_without_network(tmp_path):
    source, raw = example(tmp_path, llm=True)
    destination = tmp_path / 'prepared'
    prepare_world(source, destination)
    config = SimulationConfig.from_yaml(destination / 'world.yaml')
    population = CreatorPopulation.from_yaml(config.creator_persona_path)
    pack = genesis_observation_pack(population.creators[0], world_anchor_date=config.world_anchor_date)
    assert raw['creators'][0]['personality'] in pack.persona['你的声音']
    assert raw['creators'][0]['goal'] in pack.persona['你的创作直觉']
    audience = ViewerPopulationSpec.from_yaml(config.viewer_persona_path)
    assert set(audience.active_categories) == set(raw['categories'])
    assert raw['viewers']['personality'] in audience.constellations[0].interest_story
    provider = FakeWorldProvider()
    runner = SimulationRunner.with_llm(config, provider=provider)
    assert len(runner.run_through()) == 2
    with runner.database.session() as session:
        creators = session.scalars(select(CreatorRow).order_by(CreatorRow.creator_id)).all()
        assert [c.model_alias for c in creators] == ['main', 'other']
        assert set(session.scalars(select(VideoRow.category))) == set(raw['categories'])
        assert set(session.scalars(select(ViewerRow.model_alias))) == {'other'}
    assert provider.calls
    # The compiled personality files are frozen, too.
    config.creator_persona_path.write_text('changed', encoding='utf-8')
    with pytest.raises(RuntimeError, match='changed'):
        runner.initialize()


def test_one_custom_category_and_small_viewer_grammar(tmp_path):
    _, raw = example(tmp_path)
    raw['categories'] = ['桌面种菜']
    raw['creators'] = [{**raw['creators'][0], 'category': '桌面种菜'}]
    raw['viewers']['interests'] = ['桌面种菜']
    documents = compile_world(SimpleWorld.model_validate(raw), tmp_path / 'one')
    viewers = ViewerPopulationSpec.model_validate(documents['viewers.yaml'])
    assert viewers.active_categories == ('桌面种菜',)
    assert viewers.constellations[0].medium_categories == []
    assert documents['world.yaml']['homepage_size'] == 1


@pytest.mark.parametrize('edit', ['typo', 'category', 'model', 'duplicate', 'price', 'api_key'])
def test_invalid_simple_input_fails_before_writing(tmp_path, edit):
    source, raw = example(tmp_path, llm=True)
    if edit == 'typo': raw['creators'][0]['personailty'] = 'misspelled'
    elif edit == 'category': raw['creators'][0]['category'] = 'not registered'
    elif edit == 'model': raw['creators'][0]['model'] = 'missing'
    elif edit == 'duplicate': raw['creators'][1]['name'] = raw['creators'][0]['name']
    elif edit == 'price': raw['models']['main']['input_per_million'] = None
    elif edit == 'api_key': raw['models']['main']['api_key'] = 'do-not-store-key'
    source.write_text(yaml_text(raw), encoding='utf-8')
    output = tmp_path / 'invalid'
    with pytest.raises(ValueError) as caught: prepare_world(source, output)
    assert 'do-not-store-key' not in str(caught.value)
    assert not output.exists()
    assert not (tmp_path / 'runs').exists()


def test_check_only_never_writes_and_does_not_need_credentials(tmp_path, monkeypatch):
    source, _ = example(tmp_path, llm=True)
    monkeypatch.delenv('AILIAILI_MODEL_API_KEY', raising=False)
    output = tmp_path / 'checked'
    prepare_world(source, output, check_only=True)
    assert not output.exists() and not (tmp_path / 'runs').exists()


@pytest.mark.parametrize('with_key', [True, False])
def test_generic_chat_route_request_usage_and_credentials(monkeypatch, with_key):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'id': 'sample', 'model': 'local-test',
            'choices': [{'message': {'content': '{"ok":true}'}}],
            'usage': {'prompt_tokens': 15, 'completion_tokens': 4, 'cost': 123}})
    monkeypatch.setenv('MY_CUSTOM_MODEL_KEY', 'temporary-test-only')
    spec = ModelSpec(provider='openai_compatible', model_id='local-test',
        endpoint='http://127.0.0.1:8080/v1/chat/completions',
        api_key_env='MY_CUSTOM_MODEL_KEY' if with_key else None,
        supports_temperature=False, token_limit_parameter='max_completion_tokens',
        pricing={'input_per_million': 0, 'output_per_million': 0})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        generic = OpenAICompatibleProvider(client=client)
        # Use injected clients for all routes; no real service is used.
        routing = RoutingChatProvider(openrouter=generic, volcengine_ark=generic, openai_compatible=generic)
        response = routing.complete(spec=spec, messages=[{'role': 'user', 'content': 'JSON please'}],
            max_output_tokens=128, temperature=0.3, seed=42, reasoning_enabled=True)
    payload = json.loads(seen[0].content)
    assert payload['max_completion_tokens'] == 128
    assert not {'temperature', 'seed', 'reasoning', 'thinking', 'provider', 'max_tokens'} & payload.keys()
    assert payload['response_format'] == {'type': 'json_object'}
    assert ('Authorization' in seen[0].headers) == with_key
    assert 'X-OpenRouter-Title' not in seen[0].headers
    assert response.usage.input_tokens == 15
    assert response.usage.reported_cost is None  # Generic provider cost fields aren't authoritative.


def test_generic_rate_limit_preserves_cooldown():
    spec = ModelSpec(provider='openai_compatible', model_id='local-test',
        api_key_env=None, endpoint='http://127.0.0.1:8080/v1/chat/completions',
        pricing={'input_per_million': 0, 'output_per_million': 0})
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(429, headers={'Retry-After': '12'}))) as client:
        with pytest.raises(ProviderError) as caught:
            OpenAICompatibleProvider(client=client).complete(spec=spec, messages=[],
                max_output_tokens=32, temperature=0, seed=None, reasoning_enabled=False)
    assert caught.value.code == 'http_429'
    assert caught.value.retry_after_seconds == 12
