"""Copy one reference persona into an editable population file; no model calls."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import yaml

from ailiaili.persona import CreatorPersona, CreatorPopulation


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=root / 'configs/reference/creators.yaml')
    parser.add_argument('--creator', required=True, help='Reference creator ID, e.g. creator_038')
    parser.add_argument('--new-id', help='Optional new ID, e.g. creator_901')
    parser.add_argument('--name', help='Optional new display name')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output exists; choose a new path to preserve your edits.')
    population = CreatorPopulation.from_yaml(args.source)
    original = next((c for c in population.creators if c.creator_id == args.creator), None)
    if original is None:
        parser.error('Creator ID not present in the selected population.')
    data = original.model_dump(mode='json')
    data.update(mirror_group_id=None, mirror_slot=None)
    if args.new_id:
        data['creator_id'] = args.new_id
    if args.name:
        data['display_name'] = args.name
    persona = CreatorPersona.model_validate(data)
    copied = CreatorPopulation.model_validate({
        'version': 'local-persona-v1', 'expected_count': 1,
        'category_targets': dict(Counter([persona.primary_category])),
        'creators': [persona.model_dump(mode='json')],
    })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(copied.model_dump(mode='json'),
                                         allow_unicode=True, sort_keys=False), encoding='utf-8')
    print(f'Created {args.output}: {persona.creator_id} / {persona.display_name}')
    print('Persona only: no runtime memory, no model calls. Edit this file before starting a new run.')


if __name__ == '__main__':
    main()
