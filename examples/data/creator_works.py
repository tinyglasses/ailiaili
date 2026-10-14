"""Find/export one creator's works from the public world projection (stdlib only)."""

import argparse
from pathlib import Path

from _common import DEFAULT_WORLD, emit_json, find_creator, load_world


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--creator", required=True, help="creator ID or exact nickname")
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD)
    parser.add_argument("--through-day", type=int, help="Include works published on/before this day")
    parser.add_argument("--query", default="", help="Substring in title, premise, or outline")
    parser.add_argument("--output", type=Path, help="Write JSON here instead of stdout")
    args = parser.parse_args()
    try:
        if args.through_day is not None and args.through_day < 0:
            raise ValueError("--through-day must be nonnegative")
        world = load_world(args.world)
        creator_id, creator = find_creator(world, args.creator)
        selected = []
        for work in world["community"]["catalog"]["works"].values():
            if work["creator_id"] != creator_id:
                continue
            if args.through_day is not None and work["published_day"] > args.through_day:
                continue
            searchable = "\n".join(
                [work.get("title", ""), work.get("premise", ""), *work.get("outline", [])]
            )
            if args.query.casefold() not in searchable.casefold():
                continue
            selected.append({key: work.get(key) for key in (
                "video_id", "published_day", "title", "premise", "outline", "cover_description"
            )})
        selected.sort(key=lambda work: (work["published_day"], work["video_id"]))
        emit_json({
            "run_id": world["run_id"],
            "creator_id": creator_id,
            "creator_name": creator["name"],
            "filters": {"through_day": args.through_day, "query": args.query},
            "work_count": len(selected),
            "works": selected,
        }, args.output)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
