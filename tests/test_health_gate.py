from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ailiaili.config import SimulationConfig
from ailiaili.simulation.runner import SimulationRunner


def _runner(tmp_path: Path) -> SimulationRunner:
    return SimulationRunner(
        SimulationConfig(
            run_id="health-gate",
            seed=7,
            world_anchor_date="2026-08-11",
            creator_count=6,
            viewer_count=1,
            homepage_size=10,
            max_videos_per_creator=2,
            output_root=tmp_path,
        )
    )


def test_one_failed_model_cannot_hide_inside_a_healthy_total(
    tmp_path: Path,
) -> None:
    records = [
        ("population", "cheap", True) for _ in range(45)
    ] + [("population", "expensive", False) for _ in range(5)]

    with pytest.raises(RuntimeError, match="expensive"):
        _runner(tmp_path)._assert_group_health(
            records=records,
            stage="viewer",
        )


def test_single_failed_creator_can_be_skipped_without_blocking_the_day(
    tmp_path: Path,
) -> None:
    records = [
        ("population", "cheap", True) for _ in range(9)
    ] + [("reference", "rare", False)]

    _runner(tmp_path)._assert_group_health(records=records, stage="viewer")


def test_two_failures_in_a_small_model_cohort_do_not_fake_an_outage(
    tmp_path: Path,
) -> None:
    records = [
        ("population", "ling", True) for _ in range(9)
    ] + [
        ("population", "qwen", True) for _ in range(5)
    ] + [
        ("population", "qwen", False) for _ in range(2)
    ]

    _runner(tmp_path)._assert_group_health(records=records, stage="creator")


def test_three_failures_in_a_small_model_cohort_still_stop_the_day(
    tmp_path: Path,
) -> None:
    records = [
        ("population", "ling", True) for _ in range(9)
    ] + [
        ("population", "qwen", True) for _ in range(4)
    ] + [
        ("population", "qwen", False) for _ in range(3)
    ]

    with pytest.raises(RuntimeError, match="qwen"):
        _runner(tmp_path)._assert_group_health(records=records, stage="creator")


def test_post_click_model_failure_cannot_masquerade_as_zero_strong_feedback(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    runner.config = runner.config.model_copy(update={"execution_mode": "llm"})
    sessions = [
        SimpleNamespace(
            cohort_role="population",
            model_alias="broken-postclick",
            clicked_video_ids_json=[f"v{index}"],
            post_click_decisions_json=[],
        )
        for index in range(5)
    ] + [
        SimpleNamespace(
            cohort_role="population",
            model_alias="healthy",
            clicked_video_ids_json=[f"ok{index}"],
            post_click_decisions_json=[{"video_id": f"ok{index}"}],
        )
        for index in range(20)
    ]

    with pytest.raises(RuntimeError, match="broken-postclick"):
        runner._assert_post_click_stage_health(sessions)


def test_two_isolated_post_click_failures_remain_skippable(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.config = runner.config.model_copy(update={"execution_mode": "llm"})
    sessions = [
        SimpleNamespace(
            cohort_role="reference",
            model_alias="rare",
            clicked_video_ids_json=["failed-a", "failed-b", "succeeded"],
            post_click_decisions_json=[{"video_id": "succeeded"}],
        )
    ] + [
        SimpleNamespace(
            cohort_role="population",
            model_alias="healthy",
            clicked_video_ids_json=[f"ok{index}"],
            post_click_decisions_json=[{"video_id": f"ok{index}"}],
        )
        for index in range(20)
    ]

    runner._assert_post_click_stage_health(sessions)
