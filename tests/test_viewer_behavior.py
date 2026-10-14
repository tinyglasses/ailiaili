from __future__ import annotations

from ailiaili.viewer.behavior import activate_post_click


def _activate(*, channel_pull: str, episode_impact: str, identity: str = "p1"):
    return activate_post_click(
        base_seed=20260812,
        actor_identity=identity,
        video_id="v1",
        channel_pull=channel_pull,
        episode_impact=episode_impact,
        follow_open_to_more=0.0,
        follow_actively_seek=1.0,
        triple_notable=0.0,
        triple_exceptional=1.0,
    )


def test_low_qualitative_reaction_cannot_activate_buttons() -> None:
    result = _activate(channel_pull="none", episode_impact="ordinary")
    assert result.follow_intent is False
    assert result.triple is False
    assert result.follow_probability == 0
    assert result.triple_probability == 0


def test_high_reaction_uses_the_uniform_activation_policy() -> None:
    result = _activate(
        channel_pull="actively_seek", episode_impact="exceptional"
    )
    assert result.follow_intent is True
    assert result.triple is True


def test_reference_models_can_share_one_latent_draw() -> None:
    left = _activate(
        channel_pull="actively_seek",
        episode_impact="exceptional",
        identity="shared-panel",
    )
    right = _activate(
        channel_pull="actively_seek",
        episode_impact="exceptional",
        identity="shared-panel",
    )
    assert left == right
