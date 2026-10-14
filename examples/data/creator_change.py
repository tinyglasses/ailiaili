"""Compare a creator's cumulative metrics at two completed simulation days."""

import argparse
from pathlib import Path

from _common import DEFAULT_WORLD, emit_json, find_creator, load_world, ratio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creator", required=True)
    parser.add_argument("--from-day", type=int, required=True)
    parser.add_argument("--to-day", type=int, required=True)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if not 0 <= args.from_day < args.to_day:
            raise ValueError("Require 0 <= from-day < to-day")
        world = load_world(args.world)
        creator_id, creator = find_creator(world, args.creator)
        metrics = world["community"]["metrics"]
        columns = metrics["columns"]
        series = metrics["creators"][creator_id]
        if args.to_day >= len(series):
            raise ValueError(f"Available metric days are 0 through {len(series) - 1}")
        expected = {"impressions", "clicks", "new_follows", "triples", "videos_published"}
        if not expected.issubset(columns) or len(set(columns)) != len(columns):
            raise ValueError("Unexpected cumulative metric columns")
        before_row, after_row = series[args.from_day], series[args.to_day]
        if len(before_row) != len(columns) or len(after_row) != len(columns):
            raise ValueError("Metric row length differs from columns")
        before, after = dict(zip(columns, before_row)), dict(zip(columns, after_row))
        delta = {key: after[key] - before[key] for key in columns}
        if any(value < 0 for value in delta.values()):
            raise ValueError("Cumulative counts decreased; inspect your data before comparing")
        emit_json({
            "run_id": world["run_id"],
            "creator_id": creator_id,
            "creator_name": creator["name"],
            "from_day": args.from_day,
            "to_day": args.to_day,
            "interval": f"Day {args.from_day + 1} through Day {args.to_day}, inclusive",
            "before_cumulative": before,
            "after_cumulative": after,
            "change": delta,
            "interval_ctr": ratio(delta["clicks"], delta["impressions"]),
            "note": "Simulated actions, not unique people. Metric days do not imply saved homepage snapshots.",
        }, args.output)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
