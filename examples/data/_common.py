"""Small stdlib helpers for the public-data examples; not a simulation loader."""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORLD = ROOT / "data" / "world.json"
DEFAULT_METRICS = ROOT / "data" / "daily_metrics.csv"


def load_world(path):
    world = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    try:
        catalog = world["community"]["catalog"]
        creators, works = catalog["creators"], catalog["works"]
        if not isinstance(creators, dict) or not isinstance(works, dict):
            raise ValueError("catalog.creators and catalog.works must be dictionaries")
        if not isinstance(world["run_id"], str):
            raise ValueError("run_id must be a string")
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "Expected a public world projection with run_id and "
            "community.catalog.creators/works; a run snapshot is not this format."
        ) from exc
    return world


def find_creator(world, query):
    creators = world["community"]["catalog"]["creators"]
    if query in creators:
        return query, creators[query]
    matches = [(key, value) for key, value in creators.items() if value.get("name") == query]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ValueError(f"Unknown creator: {query!r}; use an ID or exact name.")
    raise ValueError(f"Name {query!r} is ambiguous; use creator_id.")


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def emit_json(value, output):
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if output:
        path = Path(output)
        data_root = (ROOT / "data").resolve()
        destination = path.resolve()
        if destination == data_root or data_root in destination.parents:
            raise ValueError("Choose an output path outside the bundled data/ directory.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(content)
