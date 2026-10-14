from __future__ import annotations

from pathlib import Path

from ailiaili.config import SimulationConfig
from ailiaili.domain import VideoStats
from ailiaili.recommendation import Recommender


def _config(tmp_path: Path) -> SimulationConfig:
    return SimulationConfig(
        run_id="recommendation-contract",
        seed=11,
        world_anchor_date="2026-08-10",
        creator_count=20,
        viewer_count=1,
        homepage_size=10,
        cold_impression_target=12,
        exploit_ratio=0.8,
        cold_ratio=0.1,
        explore_ratio=0.1,
        max_videos_per_creator=2,
        output_root=tmp_path,
    )


def _video(
    index: int,
    *,
    day: int = 0,
    category: str = "科学与知识",
) -> dict[str, object]:
    video = {
        "video_id": f"v{index:02d}",
        "creator_id": f"c{index:02d}",
        "published_day": day,
        "category": category,
        "category": "科学与知识",
        "title": f"视频 {index}",
        "cover_description": "封面",
        "premise": "设定",
        "outline": ["一", "二", "三"],
    }
    video["category"] = category
    return video


def test_unseen_items_do_not_enter_normal_exploit_pool(tmp_path: Path) -> None:
    config = _config(tmp_path)
    videos = [_video(index) for index in range(20)]
    stats = {
        "v00": VideoStats(impressions=100, clicks=30),
        "v01": VideoStats(impressions=100, clicks=20),
        "v02": VideoStats(impressions=100, clicks=10),
        "v03": VideoStats(impressions=100, clicks=5),
        "v04": VideoStats(impressions=100, clicks=4),
        "v05": VideoStats(impressions=100, clicks=3),
        "v06": VideoStats(impressions=100, clicks=2),
        "v07": VideoStats(impressions=100, clicks=1),
    }

    result = Recommender(config).sample(
        videos=videos,
        stats_by_video=stats,
        current_day=10,
        excluded_video_ids=set(),
        seed=3,
    )

    assert len(result) == 10
    assert all(
        stats.get(item.video_id, VideoStats()).impressions
        >= config.cold_impression_target
        for item in result
        if item.bucket == "exploit"
    )


def test_all_cold_world_still_fills_homepage_and_labels_it_truthfully(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    result = Recommender(config).sample(
        videos=[_video(index) for index in range(20)],
        stats_by_video={},
        current_day=1,
        excluded_video_ids=set(),
        seed=3,
    )

    assert len(result) == 10
    assert len({item.video_id for item in result}) == 10
    assert {item.bucket for item in result} <= {"cold", "explore"}


def test_thin_personal_lanes_are_capped_and_shortages_return_to_global(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path).model_copy(
        update={
            "creator_count": 60,
            "interest_slots_max": 2,
            "following_slots_max": 2,
        }
    )
    videos = [_video(index, category="其他") for index in range(20)]
    videos.extend(
        _video(index, category="关注内容") for index in range(20, 40)
    )
    videos.extend(
        _video(index, category="目标兴趣") for index in range(40, 60)
    )

    result = Recommender(config).sample(
        videos=videos,
        stats_by_video={},
        current_day=1,
        excluded_video_ids=set(),
        followed_creator_ids={f"c{index:02d}" for index in range(20, 40)},
        interest_categories={"目标兴趣"},
        seed=19,
    )

    source_counts = {
        source: sum(item.source == source for item in result)
        for source in ("global", "interest", "following")
    }
    assert len(result) == 10
    assert source_counts == {"global": 6, "interest": 2, "following": 2}

    no_match = Recommender(config).sample(
        videos=videos[:20],
        stats_by_video={},
        current_day=1,
        excluded_video_ids=set(),
        followed_creator_ids=set(),
        interest_categories={"不存在"},
        seed=19,
    )
    assert len(no_match) == 10
    assert {item.source for item in no_match} == {"global"}


def test_reference_history_prefers_unseen_then_oldest_exposure(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path).model_copy(
        update={
            "creator_count": 10,
            "homepage_size": 4,
            "max_videos_per_creator": 1,
        }
    )
    videos = [_video(index) for index in range(10)]
    history = {
        "v00": 1,
        "v01": 1,
        "v02": 2,
        "v03": 2,
        "v04": 3,
        "v05": 3,
        "v06": 4,
        "v07": 4,
    }

    result = Recommender(config).sample(
        videos=videos,
        stats_by_video={},
        current_day=5,
        excluded_video_ids=set(),
        shown_history=history,
        seed=7,
    )

    assert {item.video_id for item in result} == {"v00", "v01", "v08", "v09"}
