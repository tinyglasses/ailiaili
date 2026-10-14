"""Aggregate daily creator impressions and CTR, without mixing entity types."""

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from _common import DEFAULT_METRICS, DEFAULT_WORLD, emit_json, load_world, ratio


COUNTS = ("impressions", "clicks", "new_follows", "triples")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=int, required=True, help="Single day, or inclusive end with --from-day")
    parser.add_argument("--from-day", type=int, help="Optional inclusive start day")
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD, help="Matching public projection for nicknames")
    parser.add_argument("--no-world", action="store_true", help="Do not join nicknames; suitable for your own CSV")
    parser.add_argument("--run-id", help="Required if the CSV contains multiple runs")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        first = args.day if args.from_day is None else args.from_day
        if first < 0 or first > args.day:
            raise ValueError("Require 0 <= from-day <= day")
        world = None if args.no_world else load_world(args.world)
        with args.metrics.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required = {"run_id", "simulation_day", "entity_type", "entity_id", *COUNTS}
            if not required.issubset(reader.fieldnames or []):
                raise ValueError("CSV is missing required daily_metrics columns")
            # These are creator totals. Mixing in other entity types would double count.
            rows = [row for row in reader if row["entity_type"] == "creator"]
        runs = {row["run_id"] for row in rows}
        if args.run_id:
            run_id = args.run_id
        elif len(runs) == 1:
            run_id = next(iter(runs))
        else:
            raise ValueError("CSV has zero or multiple creator runs; specify --run-id")
        rows = [row for row in rows if row["run_id"] == run_id]
        if not rows:
            raise ValueError(f"No creator metrics for run {run_id!r}")
        if world is not None and world["run_id"] != run_id:
            raise ValueError("CSV and world run_id differ; pass matching --world or --no-world")
        days = {int(row["simulation_day"]) for row in rows}
        missing_days = sorted(set(range(first, args.day + 1)) - days)
        if missing_days:
            raise ValueError(f"No creator rows for requested days: {missing_days}; missing is not zero")
        seen = set()
        totals = defaultdict(lambda: {key: 0 for key in COUNTS})
        contributors = defaultdict(set)
        for row in rows:
            day = int(row["simulation_day"])
            if not first <= day <= args.day:
                continue
            key = (day, row["entity_id"])
            if key in seen:
                raise ValueError(f"Duplicate creator/day row: {key}")
            seen.add(key)
            contributors[row["entity_id"]].add(day)
            for column in COUNTS:
                value = int(row[column])
                if value < 0:
                    raise ValueError(f"Negative count in {column}")
                totals[row["entity_id"]][column] += value
        creators = world["community"]["catalog"]["creators"] if world else {}
        result = []
        for creator_id, counts in sorted(totals.items()):
            result.append({
                "creator_id": creator_id,
                "creator_name": creators.get(creator_id, {}).get("name"),
                "days_with_rows": len(contributors[creator_id]),
                **counts,
                "ctr": ratio(counts["clicks"], counts["impressions"]),
            })
        overall = {column: sum(row[column] for row in result) for column in COUNTS}
        overall["ctr"] = ratio(overall["clicks"], overall["impressions"])
        emit_json({
            "run_id": run_id,
            "from_day_inclusive": first,
            "through_day_inclusive": args.day,
            "scope": "Simulated successful-session ledger, including any Reference sessions present; not unique people.",
            "ctr_formula": "sum(clicks) / sum(impressions); null when impressions = 0",
            "creator_count": len(result),
            "total": overall,
            "creators": result,
        }, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
