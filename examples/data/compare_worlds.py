"""Describe two exported worlds at the same completed day; no model ranking."""
from __future__ import annotations

import argparse
from collections import Counter
import html
import json
from pathlib import Path

from _common import ROOT, load_world, ratio

COUNTS = ('impressions', 'clicks', 'new_follows', 'triples')


def available_day(world):
    creators = world['community']['catalog']['creators']
    series = world['community']['metrics']['creators']
    if not creators or any(cid not in series or not series[cid] for cid in creators):
        raise ValueError('Both worlds need complete cumulative creator histories; an incomplete branch export cannot be compared this way.')
    day = min(len(series[cid]) - 1 for cid in creators)
    return min(day, world.get('last_completed_day', day))


def summarize(world, day):
    community = world['community']
    creators = community['catalog']['creators']
    metrics = community['metrics']
    columns = metrics['columns']
    if len(set(columns)) != len(columns) or not set(COUNTS).issubset(columns):
        raise ValueError('Unexpected cumulative metric columns')
    totals = Counter()
    exposures = []
    for cid in creators:
        values = metrics['creators'][cid][day]
        if len(values) != len(columns):
            raise ValueError('Metric row length differs from column definitions')
        row = dict(zip(columns, values))
        if any(type(row[key]) is not int or row[key] < 0 for key in COUNTS):
            raise ValueError('Metric counts must be nonnegative integers')
        totals.update({key: row[key] for key in COUNTS})
        exposures.append(row['impressions'])
    return {'run_id': world['run_id'], 'day': day, 'creators': len(creators),
            'works': sum(w['published_day'] <= day for w in community['catalog']['works'].values()),
            **{key: totals[key] for key in COUNTS},
            'ctr': ratio(totals['clicks'], totals['impressions']),
            'most_exposed_creator_share': ratio(max(exposures), totals['impressions']),
            'creators_with_zero_exposure': exposures.count(0)}


def compare_worlds(left, right, day=None):
    last_left, last_right = available_day(left), available_day(right)
    through = min(last_left, last_right) if day is None else day
    if not 0 <= through <= min(last_left, last_right):
        raise ValueError(f'Choose a common metric day from 0 through {min(last_left, last_right)}')
    before, after = summarize(left, through), summarize(right, through)
    settings_l = left.get('experiment_settings', {})
    settings_r = right.get('experiment_settings', {})
    differences = {key: {'left': settings_l.get(key), 'right': settings_r.get(key)}
                   for key in sorted(set(settings_l) | set(settings_r)) if settings_l.get(key) != settings_r.get(key)}
    def public_creators(world):
        return {cid: {key: creator.get(key) for key in ('name', 'category', 'model_alias', 'persona', 'declaration')}
                for cid, creator in world['community']['catalog']['creators'].items()}
    return {
        'comparison_day': through, 'available_days': {'left': last_left, 'right': last_right},
        'left': before, 'right': after,
        'right_minus_left': {key: after[key] - before[key] for key in (*COUNTS, 'works', 'creators_with_zero_exposure')},
        'public_settings_differ': differences,
        'public_creator_definitions_equal': public_creators(left) == public_creators(right),
        'settings_available_for_both': bool(settings_l) and bool(settings_r),
        'interpretation': 'Descriptive simulation comparison, not causal evidence or model ranking. It does not verify provider model IDs, code versions, full viewer definitions, service failures or token budgets. Repeat controlled runs before drawing conclusions.',
    }


def cell(value):
    return html.escape(str(value), quote=False).replace('|', '\\|').replace('\n', ' ').replace('`', '\\`').replace('[', '\\[')


def render_report(result):
    left, right = result['left'], result['right']
    lines = ['# 两个模拟世界的对照', '',
             f"统一比较到 **Day {result['comparison_day']}**，不是各取最新一天。", '',
             f"| 指标 | {cell(left['run_id'])} | {cell(right['run_id'])} |", '| --- | ---: | ---: |']
    labels = {'creators': '作者数', 'works': '累计作品', 'impressions': '累计曝光', 'clicks': '累计点开',
              'new_follows': '累计新增关注', 'triples': '累计三连', 'ctr': '总点开 / 总曝光',
              'most_exposed_creator_share': '曝光最多作者的曝光占比', 'creators_with_zero_exposure': '累计零曝光作者数'}
    for key, label in labels.items():
        def formatted(row):
            value = row[key]
            if value is None: return '无分母'
            return f'{value:.2%}' if key in ('ctr', 'most_exposed_creator_share') else str(value)
        lines.append(f'| {label} | {formatted(left)} | {formatted(right)} |')
    lines += ['', '## 已知设置差异', '']
    if result['public_settings_differ']:
        lines += [f"- {cell(key)}：{cell(row['left'])} → {cell(row['right'])}" for key, row in result['public_settings_differ'].items()]
    else:
        lines.append('公开的配置字段没有检出差异。' if result['settings_available_for_both'] else '至少一个输入缺少公开配置摘要，不能核对设置是否一致。')
    lines += ['', '公开作者定义' + ('相同。' if result['public_creator_definitions_equal'] else '不同，不能直接当成同一批作者的单变量对照。'), '',
              '这里只描述模拟结果，不自动给模型排位，也不能把一次差异当成因果结论。未核对真实模型ID、代码版本、完整观众人设、服务失败与费用预算。应结合配置、原文和多次重复运行理解。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--left', type=Path, required=True)
    parser.add_argument('--right', type=Path, required=True)
    parser.add_argument('--day', type=int)
    parser.add_argument('--format', choices=['markdown', 'json'], default='markdown')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = compare_worlds(load_world(args.left), load_world(args.right), args.day)
        content = json.dumps(result, ensure_ascii=False, indent=2)+'\n' if args.format == 'json' else render_report(result)
        if args.output:
            if (ROOT / 'data').resolve() in args.output.resolve().parents:
                raise ValueError('Choose an output outside the bundled data/ directory')
            if args.output.exists(): raise ValueError('Output already exists; choose a new report filename')
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding='utf-8')
        else:
            import sys
            if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
            print(content)
    except (ValueError, OSError, KeyError, IndexError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
