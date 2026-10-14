from __future__ import annotations

from dataclasses import dataclass

from ailiaili.utils import stable_unit


@dataclass(frozen=True)
class PostClickActivation:
    follow_intent: bool
    triple: bool
    follow_probability: float
    triple_probability: float


def activate_post_click(
    *,
    base_seed: int,
    actor_identity: str,
    video_id: str,
    channel_pull: str,
    episode_impact: str,
    follow_open_to_more: float,
    follow_actively_seek: float,
    triple_notable: float,
    triple_exceptional: float,
) -> PostClickActivation:
    """Apply one model-agnostic, reproducible action-friction policy.

    Reference viewers pass their shared mirror-group identity. Consequently
    matched models receive the same latent draw for the same video: differences
    in actions can come from their qualitative judgment, not lucky random draws.
    """

    follow_probability = {
        "none": 0.0,
        "open_to_more": follow_open_to_more,
        "actively_seek": follow_actively_seek,
    }.get(channel_pull)
    if follow_probability is None:
        raise ValueError(f"unknown channel pull: {channel_pull}")
    triple_probability = {
        "ordinary": 0.0,
        "notable": triple_notable,
        "exceptional": triple_exceptional,
    }.get(episode_impact)
    if triple_probability is None:
        raise ValueError(f"unknown episode impact: {episode_impact}")

    follow_intent = (
        follow_probability > 0
        and stable_unit(
            base_seed,
            "post-click-follow-activation",
            actor_identity,
            video_id,
        )
        < follow_probability
    )
    triple = (
        triple_probability > 0
        and stable_unit(
            base_seed,
            "post-click-triple-activation",
            actor_identity,
            video_id,
        )
        < triple_probability
    )
    return PostClickActivation(
        follow_intent=follow_intent,
        triple=triple,
        follow_probability=follow_probability,
        triple_probability=triple_probability,
    )
