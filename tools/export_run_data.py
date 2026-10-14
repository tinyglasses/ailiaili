"""Export completed-day results from your own run, using read-only SQLite and stdlib."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import sqlite3


METRIC_COLUMNS = ['metric_id', 'run_id', 'simulation_day', 'entity_type', 'entity_id',
                  'impressions', 'clicks', 'completed_follow_opportunities', 'new_follows',
                  'completed_post_click_opportunities', 'triples', 'score']
CUMULATIVE_COLUMNS = ['impressions', 'clicks', 'new_follows', 'triples', 'videos_published']
# Only simple experimental settings; never copy the configuration object itself.
PUBLIC_SETTINGS = {'execution_mode', 'seed', 'days', 'creator_count', 'viewer_count',
                   'homepage_size', 'max_clicks', 'publish_interval_days',
                   'exploit_ratio', 'cold_ratio', 'explore_ratio',
                   'max_videos_per_creator', 'age_half_life_days'}


def decoded(value, default):
    return json.loads(value) if value else default


def export(run_dir: Path, output: Path, requested_run: str | None = None):
    database = run_dir.resolve() / 'ailiaili.db'
    if not database.is_file():
        raise ValueError('Run directory must contain ailiaili.db.')
    if output.exists():
        raise ValueError('Output already exists; choose a new directory.')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('BEGIN')
        runs = connection.execute('SELECT run_id, last_completed_day, config_json FROM runs').fetchall()
        selected = [r for r in runs if requested_run is None or r['run_id'] == requested_run]
        if len(selected) != 1:
            raise ValueError('Select exactly one existing run using --run-id.')
        run_id = selected[0]['run_id']
        last_day = selected[0]['last_completed_day']
        try:
            config = json.loads(selected[0]['config_json'])
        except (TypeError, ValueError):
            config = {}
        settings = {key: value for key, value in config.items()
                    if key in PUBLIC_SETTINGS and isinstance(value, (str, int, float, bool))} if isinstance(config, dict) else {}
        completed = {r[0] for r in connection.execute(
            "SELECT simulation_day FROM days WHERE run_id=? AND status='completed' AND simulation_day<=?",
            (run_id, last_day)) if r[0] > 0}
        if last_day < 1 or last_day not in completed:
            raise ValueError('Complete at least one simulation day before exporting.')
        creators = {}
        for row in connection.execute(
                'SELECT creator_id,name,category,model_alias,persona_json,declaration_json FROM creators WHERE run_id=? ORDER BY creator_id',
                (run_id,)):
            creators[row['creator_id']] = {
                'creator_id': row['creator_id'], 'name': row['name'], 'category': row['category'],
                'model_alias': row['model_alias'], 'persona': decoded(row['persona_json'], {}),
                'declaration': decoded(row['declaration_json'], {}),
            }
        works, by_creator = {}, {cid: [] for cid in creators}
        for row in connection.execute(
                'SELECT video_id,creator_id,published_day,category,grounding_mode,title,cover_description,premise,outline_json,inspiration_json,source FROM videos WHERE run_id=? AND published_day<=? ORDER BY published_day,video_id',
                (run_id, last_day)):
            if row['published_day'] != 0 and row['published_day'] not in completed:
                continue
            work = dict(row)
            work['outline'] = decoded(work.pop('outline_json'), [])
            work['inspiration'] = decoded(work.pop('inspiration_json'), None)
            works[work['video_id']] = work
            by_creator[work['creator_id']].append(work['video_id'])
        query = 'SELECT ' + ','.join(METRIC_COLUMNS) + ' FROM daily_metrics WHERE run_id=? AND simulation_day<=? ORDER BY simulation_day,entity_type,entity_id'
        metrics = [dict(r) for r in connection.execute(query, (run_id, last_day))
                   if r['simulation_day'] in completed or r['simulation_day'] == 0]

    full_history = completed == set(range(1, last_day + 1))
    cumulative = {}
    if full_history:
        daily = {(r['entity_id'], r['simulation_day']): r for r in metrics if r['entity_type'] == 'creator'}
        required = {(cid, day) for cid in creators for day in range(1, last_day + 1)}
        full_history = required.issubset(daily)
        if full_history:
            for cid in creators:
                total = [0] * len(CUMULATIVE_COLUMNS)
                sequence = []
                published = defaultdict(int)
                for vid in by_creator[cid]:
                    published[works[vid]['published_day']] += 1
                for day in range(last_day + 1):
                    row = daily.get((cid, day), {})
                    for index, key in enumerate(CUMULATIVE_COLUMNS[:-1]):
                        total[index] += int(row.get(key, 0))
                    total[-1] += published[day]
                    sequence.append(total.copy())
                cumulative[cid] = sequence

    world = {
        'schema_version': 'ailiaili-analysis-projection-v1', 'run_id': run_id,
        'last_completed_day': last_day,
        'experiment_settings': settings,
        'community': {
            'catalog': {'creators': creators, 'works': works, 'creator_work_ids': by_creator},
            'metrics': {'columns': CUMULATIVE_COLUMNS, 'creators': cumulative},
        },
        'export_scope': {
            'completed_days': sorted(completed), 'creator_cumulative_history_complete': full_history,
            'has_runtime_continuation_state': False, 'has_film_site_schema': False,
            'excluded': ['credentials', 'request/response logs', 'runtime memories', 'full viewer sessions', 'images'],
        },
    }
    output.mkdir(parents=True)
    (output / 'world.json').write_text(json.dumps(world, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (output / 'daily_metrics.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=METRIC_COLUMNS)
        writer.writeheader()
        writer.writerows(metrics)
    (output / 'README.md').write_text(
        '# Your run: analysis export\n\n'
        'This is a simulated-content result projection, not a continuation snapshot or the film-site schema. '
        'Use it with the data examples and inspiration exporter. To view this run, use its own generated report.\n\n'
        'Only completed-day works and metrics are exported. Credentials, model request logs and runtime memories are excluded. '
        'Role definitions and generated text are included; check your own content before sharing. '
        'Cumulative creator series are emitted only when all required daily records are present.\n', encoding='utf-8')
    return {'run_id': run_id, 'last_completed_day': last_day, 'creators': len(creators),
            'works': len(works), 'metric_rows': len(metrics), 'cumulative_history_complete': full_history}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id')
    args = parser.parse_args()
    try:
        print(json.dumps(export(args.run_dir, args.output, args.run_id), ensure_ascii=False))
    except (ValueError, sqlite3.DatabaseError) as error:
        parser.exit(2, f'{error}\n')


if __name__ == '__main__':
    main()
