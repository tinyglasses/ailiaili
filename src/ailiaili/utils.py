from __future__ import annotations

import hashlib
import json
import math
import random


def stable_seed(base_seed: int, *parts: object) -> int:
    payload = "|".join([str(base_seed), *(str(part) for part in parts)])
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def stable_provider_seed(base_seed: int, *parts: object) -> int:
    """Return a deterministic seed accepted by 32-bit provider adapters."""

    return stable_seed(base_seed, *parts) & ((1 << 31) - 1)


def stable_unit(base_seed: int, *parts: object) -> float:
    return stable_seed(base_seed, *parts) / float((1 << 63) - 1)


def metric_dimension_id(*parts: str) -> str:
    """Encode analytical dimensions without ambiguous delimiter rules."""

    return json.dumps(list(parts), ensure_ascii=True, separators=(",", ":"))


def parse_metric_dimension_id(value: str, *, size: int) -> tuple[str, ...]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or len(parsed) != size:
        raise ValueError(f"invalid {size}-part metric dimension ID")
    return tuple(str(item) for item in parsed)


def weighted_alias_assignments(
    *,
    aliases: list[str],
    weights: dict[str, float],
    count: int,
    seed: int,
) -> list[str]:
    """Deterministic largest-remainder quotas, shuffled away from row order."""

    if count <= 0:
        return []
    if not aliases:
        raise ValueError("at least one alias is required")
    if set(weights) != set(aliases):
        raise ValueError("weights must cover aliases exactly")
    if any(weight <= 0 for weight in weights.values()):
        raise ValueError("weights must be positive")
    total_weight = sum(weights.values())
    raw_counts = {
        alias: count * weights[alias] / total_weight for alias in aliases
    }
    counts = {alias: math.floor(raw_counts[alias]) for alias in aliases}
    remaining = count - sum(counts.values())
    remainder_order = sorted(
        aliases,
        key=lambda alias: (-(raw_counts[alias] - counts[alias]), alias),
    )
    for alias in remainder_order[:remaining]:
        counts[alias] += 1
    assignments = [
        alias for alias in aliases for _ in range(counts[alias])
    ]
    random.Random(seed).shuffle(assignments)
    return assignments
