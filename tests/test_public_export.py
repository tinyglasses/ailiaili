"""The public result export must exclude unfinished facts and private runtime state."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location(
    'public_run_export', Path(__file__).resolve().parents[1] / 'tools/export_run_data.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_export_excludes_unfinished_day_and_private_state(tmp_path):
    run = tmp_path / 'my run with spaces'
    run.mkdir()
    database = run / 'ailiaili.db'
    with sqlite3.connect(database) as connection:
        connection.executescript('''
        CREATE TABLE runs(run_id TEXT, last_completed_day INTEGER, config_json TEXT);
        CREATE TABLE days(run_id TEXT, simulation_day INTEGER, status TEXT);
        CREATE TABLE creators(run_id TEXT, creator_id TEXT, name TEXT, category TEXT, model_alias TEXT, persona_json TEXT, declaration_json TEXT, memory_json TEXT);
        CREATE TABLE videos(run_id TEXT, video_id TEXT, creator_id TEXT, published_day INTEGER, category TEXT, grounding_mode TEXT, title TEXT, cover_description TEXT, premise TEXT, outline_json TEXT, inspiration_json TEXT, source TEXT);
        CREATE TABLE daily_metrics(metric_id INTEGER, run_id TEXT, simulation_day INTEGER, entity_type TEXT, entity_id TEXT, impressions INTEGER, clicks INTEGER, completed_follow_opportunities INTEGER, new_follows INTEGER, completed_post_click_opportunities INTEGER, triples INTEGER, score REAL);
        INSERT INTO runs VALUES('demo',1,'private-configuration-sentinel');
        INSERT INTO days VALUES('demo',1,'completed'),('demo',2,'running');
        INSERT INTO creators VALUES('demo','creator_901','Example','游戏','stub','{}','{}','private-memory-sentinel');
        INSERT INTO videos VALUES('demo','initial','creator_901',0,'游戏','fiction','Initial','','','[]',NULL,'stub');
        INSERT INTO videos VALUES('demo','done','creator_901',1,'游戏','fiction','Completed','','','[]',NULL,'stub');
        INSERT INTO videos VALUES('demo','unfinished','creator_901',2,'游戏','fiction','unfinished-title-sentinel','','','[]',NULL,'stub');
        INSERT INTO daily_metrics VALUES(1,'demo',1,'creator','creator_901',10,2,2,1,2,0,NULL);
        INSERT INTO daily_metrics VALUES(2,'demo',2,'creator','creator_901',999,999,999,0,999,0,NULL);
        ''')
    before = database.read_bytes()
    destination = tmp_path / 'export'
    result = module.export(run, destination)
    world = json.loads((destination / 'world.json').read_text(encoding='utf-8'))
    assert result['works'] == 2 and result['metric_rows'] == 1
    assert set(world['community']['catalog']['works']) == {'initial', 'done'}
    assert world['community']['metrics']['creators']['creator_901'][-1] == [10, 2, 1, 0, 2]
    output = ''.join(p.read_text(encoding='utf-8') for p in destination.iterdir())
    assert 'private-configuration-sentinel' not in output
    assert 'private-memory-sentinel' not in output
    assert 'unfinished-title-sentinel' not in output
    assert database.read_bytes() == before
    with pytest.raises(ValueError, match='already exists'):
        module.export(run, destination)


def test_export_missing_database_does_not_create_it(tmp_path):
    run = tmp_path / 'uninitialized'
    run.mkdir()
    with pytest.raises(ValueError, match='must contain'):
        module.export(run, tmp_path / 'out')
    assert not (run / 'ailiaili.db').exists()
