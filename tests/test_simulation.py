from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select

from ailiaili.config import SimulationConfig
from ailiaili.persistence.models import (
    DailyMetricRow,
    DayRow,
    FollowRow,
    HomepageSessionRow,
    VideoRow,
)
from ailiaili.simulation.runner import InjectedFailure, SimulationRunner


def make_config(tmp_path: Path, run_id: str) -> SimulationConfig:
    return SimulationConfig(
        run_id=run_id,
        seed=20260810,
        world_anchor_date="2026-08-10",
        days=7,
        creator_count=6,
        viewer_count=20,
        homepage_size=10,
        max_clicks=3,
        publish_interval_days=3,
        initial_videos_per_creator=8,
        cold_impression_target=12,
        exploit_ratio=0.8,
        cold_ratio=0.1,
        explore_ratio=0.1,
        max_videos_per_creator=2,
        age_half_life_days=14,
        viewer_note_mode="brief",
        output_root=tmp_path,
    )


def test_seven_day_stub_world_reconciles(tmp_path: Path) -> None:
    config = make_config(tmp_path, "complete")
    runner = SimulationRunner(config)

    summaries = runner.run_through()

    assert len(summaries) == 7
    assert all(summary.status == "completed" for summary in summaries)
    with runner.database.session() as session:
        completed_days = session.scalar(
            select(func.count()).select_from(DayRow).where(
                DayRow.run_id == config.run_id,
                DayRow.simulation_day > 0,
                DayRow.status == "completed",
            )
        )
        videos = session.scalar(
            select(func.count()).select_from(VideoRow).where(
                VideoRow.run_id == config.run_id
            )
        )
        homepage_sessions = session.scalars(
            select(HomepageSessionRow)
            .where(HomepageSessionRow.run_id == config.run_id)
            .order_by(
                HomepageSessionRow.simulation_day,
                HomepageSessionRow.viewer_id,
            )
        ).all()
        follows = session.scalar(
            select(func.count()).select_from(FollowRow).where(
                FollowRow.run_id == config.run_id
            )
        )
        platform_metrics = session.scalars(
            select(DailyMetricRow).where(
                DailyMetricRow.run_id == config.run_id,
                DailyMetricRow.entity_type == "platform",
            )
        ).all()

    assert completed_days == 7
    assert videos == 62  # 48 Day-0 videos + 14 scheduled turns.
    assert len(homepage_sessions) == 140
    assert follows is not None and follows >= 0
    assert all(len(item.candidate_items_json) == 10 for item in homepage_sessions)
    assert all(
        len({entry["video_id"] for entry in item.candidate_items_json}) == 10
        for item in homepage_sessions
    )
    assert all(
        all(
            decision.get("click_note") and decision.get("post_click_note")
            for decision in item.post_click_decisions_json
        )
        for item in homepage_sessions
    )

    video_creator = {}
    with runner.database.session() as session:
        for video in session.scalars(
            select(VideoRow).where(VideoRow.run_id == config.run_id)
        ):
            video_creator[video.video_id] = video.creator_id
    for homepage in homepage_sessions:
        creator_counts = Counter(
            video_creator[item["video_id"]]
            for item in homepage.candidate_items_json
        )
        assert max(creator_counts.values()) <= 2

    raw_impressions = sum(
        len(homepage.candidate_items_json) for homepage in homepage_sessions
    )
    raw_clicks = sum(
        len(homepage.clicked_video_ids_json) for homepage in homepage_sessions
    )
    assert sum(metric.impressions for metric in platform_metrics) == raw_impressions
    assert sum(metric.clicks for metric in platform_metrics) == raw_clicks
    assert (config.run_dir / "site" / "index.html").exists()
    assert (config.run_dir / "site" / "summary.json").exists()
    summary = json.loads(
        (config.run_dir / "site" / "summary.json").read_text(encoding="utf-8")
    )
    assert "experience" not in summary
    timeline_path = config.run_dir / "site" / "world_timeline.json"
    assert timeline_path.exists()
    experience = json.loads(timeline_path.read_text(encoding="utf-8"))
    assert experience["schema_version"] == "world-experience-v0.3"
    assert len(experience["days"]) == 7
    assert experience["days"][-1]["cumulative"]["impressions"] == sum(
        metric.impressions for metric in platform_metrics
    )
    assert experience["days"][-1]["cumulative"]["clicks"] == sum(
        metric.clicks for metric in platform_metrics
    )
    for day in experience["days"]:
        assert "llm_cumulative" in day
        assert day["llm_cumulative"]["logical_calls"] == 0
        assert all(
            experience["video_catalog"][video_id]["published_day"] <= day["day"]
            for video_id in day["video_metrics"]
        )
        assert day["feeds"]
        assert all(len(feed["candidates"]) == 10 for feed in day["feeds"])
    html = (config.run_dir / "site" / "index.html").read_text(encoding="utf-8")
    assert "世界演化播放器" in html
    assert "首页推荐" in html
    assert "window.__AILIAILI_READY__=true" in html
    assert "模型调用与费用" in html


def test_mirror_viewers_receive_identical_feeds(tmp_path: Path) -> None:
    config = make_config(tmp_path, "mirrors")
    runner = SimulationRunner(config)
    runner.run_through(3)

    with runner.database.session() as session:
        sessions = session.scalars(
            select(HomepageSessionRow)
            .where(
                HomepageSessionRow.run_id == config.run_id,
                HomepageSessionRow.mirror_group_id.is_not(None),
            )
            .order_by(
                HomepageSessionRow.simulation_day,
                HomepageSessionRow.mirror_group_id,
                HomepageSessionRow.viewer_id,
            )
        ).all()

    grouped: dict[tuple[int, str], list[HomepageSessionRow]] = {}
    for homepage in sessions:
        grouped.setdefault(
            (homepage.simulation_day, homepage.mirror_group_id), []
        ).append(homepage)
    assert grouped
    for group_sessions in grouped.values():
        assert len(group_sessions) == 2
        assert (
            group_sessions[0].candidate_items_json
            == group_sessions[1].candidate_items_json
        )


def test_interrupted_day_resumes_without_duplicate_work(tmp_path: Path) -> None:
    recovered_config = make_config(tmp_path / "recovered", "recovered")
    recovered = SimulationRunner(recovered_config)

    with pytest.raises(InjectedFailure):
        recovered.run_day(1, fail_after_new_sessions=7)

    with recovered.database.session() as session:
        day = session.get(DayRow, (recovered_config.run_id, 1))
        partial_count = session.scalar(
            select(func.count()).select_from(HomepageSessionRow).where(
                HomepageSessionRow.run_id == recovered_config.run_id,
                HomepageSessionRow.simulation_day == 1,
            )
        )
    assert day is not None and day.status == "running"
    assert partial_count == 7

    recovered.run_through(1)
    with recovered.database.session() as session:
        recovered_sessions = session.scalars(
            select(HomepageSessionRow)
            .where(
                HomepageSessionRow.run_id == recovered_config.run_id,
                HomepageSessionRow.simulation_day == 1,
            )
            .order_by(HomepageSessionRow.viewer_id)
        ).all()
    assert len(recovered_sessions) == 20

    control_config = make_config(tmp_path / "control", "control")
    control = SimulationRunner(control_config)
    control.run_through(1)
    with control.database.session() as session:
        control_sessions = session.scalars(
            select(HomepageSessionRow)
            .where(
                HomepageSessionRow.run_id == control_config.run_id,
                HomepageSessionRow.simulation_day == 1,
            )
            .order_by(HomepageSessionRow.viewer_id)
        ).all()

    recovered_facts = [
        (
            row.viewer_id,
            row.candidate_items_json,
            row.clicked_video_ids_json,
            row.post_click_decisions_json,
        )
        for row in recovered_sessions
    ]
    control_facts = [
        (
            row.viewer_id,
            row.candidate_items_json,
            row.clicked_video_ids_json,
            row.post_click_decisions_json,
        )
        for row in control_sessions
    ]
    assert recovered_facts == control_facts
