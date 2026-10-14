from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from ailiaili.domain import (
    CreatorAccountOverview,
    CreatorMemory,
    CreatorObservationPack,
    ObservedVideo,
    PublicObservedVideo,
    VideoStats,
)
from ailiaili.persistence.database import Database
from ailiaili.persistence.models import (
    CreatorRow,
    DailyMetricRow,
    DayRow,
    RunRow,
    VideoRow,
)


class CreatorObservationBuilder:
    """Build the bounded facts one creator can see before publishing."""

    def __init__(self, database: Database, run_id: str):
        self.database = database
        self.run_id = run_id

    def build(
        self, *, creator_id: str, simulation_day: int
    ) -> CreatorObservationPack:
        with self.database.session() as session:
            run = session.get(RunRow, self.run_id)
            if run is None:
                raise ValueError(f"run does not exist: {self.run_id}")
            creator = session.get(CreatorRow, creator_id)
            if creator is None or creator.run_id != self.run_id:
                raise ValueError(f"creator does not exist in run: {creator_id}")
            completed_days = set(
                session.scalars(
                    select(DayRow.simulation_day).where(
                        DayRow.run_id == self.run_id,
                        DayRow.status == "completed",
                        DayRow.simulation_day < simulation_day,
                    )
                ).all()
            )
            if simulation_day - 1 not in completed_days:
                raise ValueError(
                    f"day {simulation_day - 1} must be completed before observation"
                )
            creators = {
                item.creator_id: item
                for item in session.scalars(
                    select(CreatorRow).where(CreatorRow.run_id == self.run_id)
                ).all()
            }
            videos = session.scalars(
                select(VideoRow).where(
                    VideoRow.run_id == self.run_id,
                    VideoRow.published_day < simulation_day,
                )
            ).all()
            metric_rows = session.scalars(
                select(DailyMetricRow).where(
                    DailyMetricRow.run_id == self.run_id,
                    DailyMetricRow.entity_type == "video",
                    DailyMetricRow.simulation_day.in_(completed_days),
                )
            ).all()
        all_stats = self._aggregate(metric_rows)
        recent_start = max(1, simulation_day - 7)
        recent_stats = self._aggregate(
            [row for row in metric_rows if row.simulation_day >= recent_start]
        )
        observed_all = {
            video.video_id: self._observed_video(
                video,
                creators[video.creator_id].name,
                all_stats.get(video.video_id, VideoStats()),
            )
            for video in videos
        }
        public_recent = {
            video.video_id: self._public_observed_video(
                video,
                creators[video.creator_id].name,
                recent_stats.get(video.video_id, VideoStats()),
            )
            for video in videos
        }

        own_videos = sorted(
            (video for video in videos if video.creator_id == creator_id),
            key=lambda video: (-video.published_day, video.video_id),
        )
        trending = sorted(
            videos,
            key=lambda video: self._trend_key(
                recent_stats.get(video.video_id, VideoStats()),
                video.published_day,
                video.video_id,
            ),
        )
        global_trending_rows = trending[:5]
        used_ids = {video.video_id for video in global_trending_rows}
        category_trending_rows = [
            video
            for video in trending
            if video.category == creator.category and video.video_id not in used_ids
        ][:5]
        used_ids.update(video.video_id for video in category_trending_rows)
        recent_new_rows = [
            video
            for video in sorted(
                videos,
                key=lambda video: (-video.published_day, video.video_id),
            )
            if video.video_id not in used_ids
        ][:3]

        account_stats = VideoStats()
        for video in own_videos:
            stats = all_stats.get(video.video_id, VideoStats())
            for field in (
                "impressions",
                "clicks",
                "completed_follow_opportunities",
                "new_follows",
                "completed_post_click_opportunities",
                "triples",
            ):
                setattr(
                    account_stats,
                    field,
                    getattr(account_stats, field) + getattr(stats, field),
                )

        return CreatorObservationPack(
            simulation_day=simulation_day,
            world_anchor_date=str(run.config_json["world_anchor_date"]),
            creator_id=creator.creator_id,
            creator_name=creator.name,
            primary_category=creator.category,
            persona=creator.persona_json,
            declaration=creator.declaration_json,
            current_memory=CreatorMemory.model_validate(creator.memory_json),
            account_overview=CreatorAccountOverview(
                video_count=len(own_videos),
                # FollowRow only exists for persistent fixed viewers. Random
                # visitors still create real aggregate follows, so the public
                # account count must come from completed behavior facts.
                cumulative_follows=account_stats.new_follows,
                **self._stats_fields(account_stats),
            ),
            own_recent_videos=[
                observed_all[video.video_id] for video in own_videos[:5]
            ],
            global_trending=[
                public_recent[video.video_id] for video in global_trending_rows
            ],
            category_trending=[
                public_recent[video.video_id]
                for video in category_trending_rows
            ],
            recent_new_videos=[
                public_recent[video.video_id] for video in recent_new_rows
            ],
        )

    @staticmethod
    def _aggregate(rows: list[DailyMetricRow]) -> dict[str, VideoStats]:
        result: dict[str, VideoStats] = defaultdict(VideoStats)
        for row in rows:
            stats = result[row.entity_id]
            for field in (
                "impressions",
                "clicks",
                "completed_follow_opportunities",
                "new_follows",
                "completed_post_click_opportunities",
                "triples",
            ):
                setattr(stats, field, getattr(stats, field) + getattr(row, field))
        return dict(result)

    @classmethod
    def _observed_video(
        cls, video: VideoRow, creator_name: str, stats: VideoStats
    ) -> ObservedVideo:
        return ObservedVideo(
            video_id=video.video_id,
            creator_id=video.creator_id,
            creator_name=creator_name,
            category=video.category,
            published_day=video.published_day,
            title=video.title,
            cover_description=video.cover_description,
            premise=video.premise,
            outline=video.outline_json,
            **cls._stats_fields(stats),
        )

    @staticmethod
    def _stats_fields(stats: VideoStats) -> dict[str, int | float]:
        return {
            "impressions": stats.impressions,
            "clicks": stats.clicks,
            "new_follows": stats.new_follows,
            "triples": stats.triples,
            "ctr": stats.ctr,
            "follow_rate": stats.follow_rate,
            "triple_rate": stats.triple_rate,
        }

    @staticmethod
    def _public_observed_video(
        video: VideoRow, creator_name: str, stats: VideoStats
    ) -> PublicObservedVideo:
        return PublicObservedVideo(
            video_id=video.video_id,
            creator_id=video.creator_id,
            creator_name=creator_name,
            category=video.category,
            published_day=video.published_day,
            title=video.title,
            cover_description=video.cover_description,
            premise=video.premise,
            outline=video.outline_json,
            clicks=stats.clicks,
            triples=stats.triples,
        )

    @staticmethod
    def _trend_key(
        stats: VideoStats, published_day: int, video_id: str
    ) -> tuple:
        return (
            -stats.clicks,
            -stats.triples,
            -published_day,
            video_id,
        )
