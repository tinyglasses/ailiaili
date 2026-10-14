from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ailiaili.config import SimulationConfig
from ailiaili.domain import VideoStats
from ailiaili.persistence.database import Database
from ailiaili.persistence.models import (
    CreatorRow,
    CreatorTurnRow,
    DailyMetricRow,
    DayRow,
    HomepageSessionRow,
    LLMCallRow,
    LLMAttemptRow,
    VideoRow,
    ViewerRow,
)
from ailiaili.utils import parse_metric_dimension_id


class ReportExporter:
    def __init__(self, config: SimulationConfig, database: Database):
        self.config = config
        self.database = database
        self.environment = Environment(
            loader=PackageLoader("ailiaili.reports", "templates"),
            autoescape=select_autoescape(["html"]),
        )

    def export(self) -> Path:
        site_dir = self.config.run_dir / "site"
        site_dir.mkdir(parents=True, exist_ok=True)
        data = self._build_data()
        template = self.environment.get_template("index.html")
        (site_dir / "index.html").write_text(
            template.render(**data),
            encoding="utf-8",
        )
        # Keep the compact audit summary backward compatible.  The richer UI
        # projection has its own versioned file so presentation work does not
        # invalidate a frozen simulation-evidence hash.
        summary = {key: value for key, value in data.items() if key != "experience"}
        (site_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (site_dir / "model_ecology.json").write_text(
            json.dumps(data["model_ecology"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (site_dir / "world_timeline.json").write_text(
            json.dumps(data["experience"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return site_dir / "index.html"

    def _build_data(self) -> dict[str, Any]:
        with self.database.session() as session:
            days = session.scalars(
                select(DayRow)
                .where(
                    DayRow.run_id == self.config.run_id,
                    DayRow.status == "completed",
                    DayRow.simulation_day > 0,
                )
                .order_by(DayRow.simulation_day)
            ).all()
            latest_day = days[-1].simulation_day if days else 0
            creators = session.scalars(
                select(CreatorRow)
                .where(CreatorRow.run_id == self.config.run_id)
                .order_by(CreatorRow.creator_id)
            ).all()
            videos = session.scalars(
                select(VideoRow)
                .where(
                    VideoRow.run_id == self.config.run_id,
                    VideoRow.published_day <= latest_day,
                )
                .order_by(VideoRow.published_day.desc(), VideoRow.video_id)
            ).all()
            metrics = session.scalars(
                select(DailyMetricRow).where(
                    DailyMetricRow.run_id == self.config.run_id,
                    DailyMetricRow.simulation_day <= latest_day,
                )
            ).all()
            viewers = {
                viewer.viewer_id: viewer
                for viewer in session.scalars(
                    select(ViewerRow).where(ViewerRow.run_id == self.config.run_id)
                ).all()
            }
            creator_turns = session.scalars(
                select(CreatorTurnRow)
                .where(
                    CreatorTurnRow.run_id == self.config.run_id,
                    CreatorTurnRow.simulation_day > 0,
                    CreatorTurnRow.simulation_day <= latest_day,
                    CreatorTurnRow.status == "succeeded",
                )
                .order_by(
                    CreatorTurnRow.simulation_day,
                    CreatorTurnRow.creator_id,
                )
            ).all()
            latest_sessions = session.scalars(
                select(HomepageSessionRow)
                .where(
                    HomepageSessionRow.run_id == self.config.run_id,
                    HomepageSessionRow.simulation_day == latest_day,
                    HomepageSessionRow.status == "succeeded",
                )
                .order_by(HomepageSessionRow.viewer_id)
                .limit(1)
            ).all()
            viewer_note_sessions = session.scalars(
                select(HomepageSessionRow)
                .where(
                    HomepageSessionRow.run_id == self.config.run_id,
                    HomepageSessionRow.simulation_day <= latest_day,
                    HomepageSessionRow.status == "succeeded",
                )
                .order_by(
                    HomepageSessionRow.simulation_day.desc(),
                    HomepageSessionRow.viewer_id,
                )
                .limit(1000)
            ).all()
            ranked_sessions = (
                select(
                    HomepageSessionRow.homepage_session_id.label("session_id"),
                    func.row_number()
                    .over(
                        partition_by=(
                            HomepageSessionRow.simulation_day,
                            HomepageSessionRow.viewer_kind,
                            HomepageSessionRow.cohort_role,
                            HomepageSessionRow.model_alias,
                        ),
                        order_by=HomepageSessionRow.viewer_id,
                    )
                    .label("sample_rank"),
                )
                .where(
                    HomepageSessionRow.run_id == self.config.run_id,
                    HomepageSessionRow.simulation_day <= latest_day,
                    HomepageSessionRow.status == "succeeded",
                )
                .subquery()
            )
            experience_sessions = session.scalars(
                select(HomepageSessionRow)
                .join(
                    ranked_sessions,
                    ranked_sessions.c.session_id
                    == HomepageSessionRow.homepage_session_id,
                )
                .where(ranked_sessions.c.sample_rank == 1)
                .order_by(
                    HomepageSessionRow.simulation_day.desc(),
                    HomepageSessionRow.viewer_id,
                )
            ).all()
            has_compact_ecology = any(
                metric.entity_type == "viewer_model" for metric in metrics
            )
            ecology_sessions = session.scalars(
                select(HomepageSessionRow)
                .where(
                    HomepageSessionRow.run_id == self.config.run_id,
                    HomepageSessionRow.simulation_day <= latest_day,
                    HomepageSessionRow.status == "succeeded",
                    *(
                        (HomepageSessionRow.cohort_role == "reference",)
                        if has_compact_ecology
                        else ()
                    ),
                )
                .order_by(
                    HomepageSessionRow.simulation_day,
                    HomepageSessionRow.viewer_id,
                )
            ).all()
            llm_daily, llm_model_rows = self._llm_sql_rollups(
                session=session,
                run_id=self.config.run_id,
                latest_day=latest_day,
            )

        creator_by_id = {creator.creator_id: creator for creator in creators}
        video_by_id = {video.video_id: video for video in videos}
        cumulative = self._cumulative_metrics(metrics)
        platform_days = {
            metric.simulation_day: metric
            for metric in metrics
            if metric.entity_type == "platform"
        }

        timeline = []
        for day in days:
            metric = platform_days.get(day.simulation_day)
            timeline.append(
                {
                    "day": day.simulation_day,
                    "videos": sum(
                        1 for video in videos if video.published_day == day.simulation_day
                    ),
                    "impressions": metric.impressions if metric else 0,
                    "clicks": metric.clicks if metric else 0,
                    "new_follows": metric.new_follows if metric else 0,
                    "triples": metric.triples if metric else 0,
                    "ctr": self._rate(metric.clicks, metric.impressions) if metric else 0,
                }
            )

        top_videos = []
        for video in videos:
            stats = cumulative.get(("video", video.video_id), VideoStats())
            top_videos.append(
                {
                    "video_id": video.video_id,
                    "title": video.title,
                    "category": video.category,
                    "creator": creator_by_id[video.creator_id].name,
                    "published_day": video.published_day,
                    "cover_description": video.cover_description,
                    **self._stats_dict(stats),
                }
            )
        top_videos.sort(
            key=lambda item: (
                -item["clicks"],
                -item["triples"],
                item["video_id"],
            )
        )

        creator_cards = []
        for creator in creators:
            stats = cumulative.get(("creator", creator.creator_id), VideoStats())
            creator_cards.append(
                {
                    "creator_id": creator.creator_id,
                    "name": creator.name,
                    "category": creator.category,
                    "model_alias": creator.model_alias,
                    "mirror_group_id": creator.mirror_group_id,
                    "goal": creator.persona_json.get(
                        "goal",
                        creator.declaration_json.get(
                            "我想做的账号", "按自己的方式持续创作"
                        ),
                    ),
                    "video_count": sum(
                        1 for video in videos if video.creator_id == creator.creator_id
                    ),
                    **self._stats_dict(stats),
                }
            )
        creator_cards.sort(
            key=lambda item: (-item["clicks"], -item["new_follows"], item["name"])
        )

        sample_feed: list[dict[str, Any]] = []
        if latest_sessions:
            for item in latest_sessions[0].candidate_items_json:
                video = video_by_id[item["video_id"]]
                sample_feed.append(
                    {
                        "video_id": video.video_id,
                        "title": video.title,
                        "cover_description": video.cover_description,
                        "category": video.category,
                        "creator": creator_by_id[video.creator_id].name,
                        "bucket": item["bucket"],
                        "source": item.get("source", "global"),
                    }
                )

        viewer_notes: list[dict[str, Any]] = []
        for homepage in viewer_note_sessions:
            viewer = viewers.get(homepage.viewer_id)
            viewer_name = (
                viewer.name
                if viewer is not None
                else "随机访客 · "
                + str(
                    homepage.viewer_snapshot_json.get(
                        "main_need", homepage.viewer_id
                    )
                )
            )
            model_alias = (
                viewer.model_alias
                if viewer is not None
                else homepage.model_alias or "unknown"
            )
            for decision in homepage.post_click_decisions_json:
                if not decision.get("click_note") and not decision.get("post_click_note"):
                    continue
                video = video_by_id[decision["video_id"]]
                viewer_notes.append(
                    {
                        "day": homepage.simulation_day,
                        "viewer": viewer_name,
                        "model_alias": model_alias,
                        "video_title": video.title,
                        "click_note": decision.get("click_note"),
                        "post_click_note": decision.get("post_click_note"),
                        "channel_pull": decision.get("channel_pull"),
                        "episode_impact": decision.get("episode_impact"),
                        "follow_activation_probability": decision.get(
                            "follow_activation_probability"
                        ),
                        "triple_activation_probability": decision.get(
                            "triple_activation_probability"
                        ),
                        "follow": decision["new_follow"],
                        "triple": decision["triple"],
                    }
                )
                if len(viewer_notes) >= 30:
                    break
            if len(viewer_notes) >= 30:
                break

        platform_total = cumulative.get(("platform", "platform"), VideoStats())
        llm_cost = self._merge_llm_rollups(llm_daily.values())
        llm_cost["models"] = llm_model_rows
        model_ecology = self._model_ecology_summary(
            creators=creators,
            videos=videos,
            sessions=ecology_sessions,
            metrics=metrics,
            cumulative=cumulative,
        )
        experience = self._build_experience(
            days=days,
            creators=creators,
            videos=videos,
            metrics=metrics,
            sessions=experience_sessions,
            creator_turns=creator_turns,
            viewers=viewers,
            llm_daily=llm_daily,
        )
        return {
            "run_id": self.config.run_id,
            "parent_run_id": self.config.parent_run_id,
            "parent_day": self.config.parent_day,
            "canonical": self.config.canonical,
            "latest_day": latest_day,
            "status": "completed" if latest_day >= self.config.days else "running",
            "totals": {
                "creators": len(creators),
                "videos": len(videos),
                "viewer_sessions": sum(day.succeeded_viewer_sessions for day in days),
                **self._stats_dict(platform_total),
            },
            "timeline": timeline,
            "sample_feed": sample_feed,
            "top_videos": top_videos[:12],
            "creators": creator_cards,
            "viewer_notes": viewer_notes,
            "llm_cost": llm_cost,
            "model_ecology": model_ecology,
            "experience": experience,
        }

    def _build_experience(
        self,
        *,
        days: list[DayRow],
        creators: list[CreatorRow],
        videos: list[VideoRow],
        metrics: list[DailyMetricRow],
        sessions: list[HomepageSessionRow],
        creator_turns: list[CreatorTurnRow],
        viewers: dict[str, ViewerRow],
        llm_daily: dict[int, dict[str, Any]],
    ) -> dict[str, Any]:
        """Build compact completed-day slices for the public world and Lab.

        This is a presentation projection, not a second source of truth.  Every
        number and homepage card is derived from persisted completed-day facts.
        """

        creator_by_id = {creator.creator_id: creator for creator in creators}
        video_by_id = {video.video_id: video for video in videos}
        videos_ascending = sorted(
            videos, key=lambda video: (video.published_day, video.video_id)
        )
        metrics_by_day: dict[int, list[DailyMetricRow]] = defaultdict(list)
        metric_lookup: dict[tuple[int, str, str], DailyMetricRow] = {}
        for metric in metrics:
            metrics_by_day[metric.simulation_day].append(metric)
            metric_lookup[
                (metric.simulation_day, metric.entity_type, metric.entity_id)
            ] = metric

        sessions_by_day: dict[int, list[HomepageSessionRow]] = defaultdict(list)
        for homepage in sessions:
            if homepage.status == "succeeded":
                sessions_by_day[homepage.simulation_day].append(homepage)
        turns_by_day: dict[int, list[CreatorTurnRow]] = defaultdict(list)
        turn_by_video: dict[str, CreatorTurnRow] = {}
        for turn in creator_turns:
            turns_by_day[turn.simulation_day].append(turn)
            if turn.video_id:
                turn_by_video[turn.video_id] = turn

        catalog = {}
        for video in videos_ascending:
            turn = turn_by_video.get(video.video_id)
            catalog[video.video_id] = {
                "video_id": video.video_id,
                "creator_id": video.creator_id,
                "creator": creator_by_id[video.creator_id].name,
                "published_day": video.published_day,
                "category": video.category,
                "grounding_mode": video.grounding_mode,
                "title": video.title,
                "cover_description": video.cover_description,
                "premise": video.premise,
                "outline": video.outline_json,
                "cover_seed": self._stable_cover_seed(video.video_id),
                "expected_reception": (
                    turn.expected_reception if turn is not None else None
                ),
                "prediction_reason": (
                    turn.prediction_reason if turn is not None else None
                ),
            }
        creator_catalog = {
            creator.creator_id: {
                "creator_id": creator.creator_id,
                "name": creator.name,
                "category": creator.category,
                "goal": creator.persona_json.get(
                    "goal",
                    creator.declaration_json.get(
                        "我想做的账号", "按自己的方式持续创作"
                    ),
                ),
                "model_alias": creator.model_alias,
                "mirror_group_id": creator.mirror_group_id,
                "day0_manifesto": creator.declaration_json,
            }
            for creator in creators
        }

        anchor = date.fromisoformat(self.config.world_anchor_date)
        cumulative: dict[tuple[str, str], VideoStats] = defaultdict(VideoStats)
        cumulative_sessions = 0
        previous_video_ranks: dict[str, int] = {}
        previous_daily_ctr: float | None = None
        slices: list[dict[str, Any]] = []
        cumulative_llm_days: list[dict[str, Any]] = []
        if 0 in llm_daily:
            cumulative_llm_days.append(llm_daily[0])

        for day_row in days:
            simulation_day = day_row.simulation_day
            if simulation_day in llm_daily:
                cumulative_llm_days.append(llm_daily[simulation_day])
            for metric in metrics_by_day.get(simulation_day, []):
                self._add_metric(cumulative[(metric.entity_type, metric.entity_id)], metric)

            daily_platform = self._stats_from_metric(
                metric_lookup.get((simulation_day, "platform", "platform"))
            )
            cumulative_platform = cumulative[("platform", "platform")]
            cumulative_sessions += day_row.succeeded_viewer_sessions
            eligible_videos = [
                video
                for video in videos_ascending
                if video.published_day <= simulation_day
            ]
            new_videos = [
                video
                for video in eligible_videos
                if video.published_day == simulation_day
            ]

            video_metrics: dict[str, dict[str, Any]] = {}
            ranked_videos: list[dict[str, Any]] = []
            for video in eligible_videos:
                stats = cumulative[("video", video.video_id)]
                daily_stats = self._stats_from_metric(
                    metric_lookup.get((simulation_day, "video", video.video_id))
                )
                row = {
                    "video_id": video.video_id,
                    **self._stats_dict(stats),
                    "daily_clicks": daily_stats.clicks,
                    "daily_impressions": daily_stats.impressions,
                    "daily_new_follows": daily_stats.new_follows,
                    "daily_triples": daily_stats.triples,
                }
                video_metrics[video.video_id] = row
                ranked_videos.append(row)
            ranked_videos.sort(
                key=lambda item: (
                    -item["clicks"],
                    -item["triples"],
                    -item["new_follows"],
                    -item["impressions"],
                    item["video_id"],
                )
            )
            current_video_ranks = {
                item["video_id"]: index + 1
                for index, item in enumerate(ranked_videos)
            }
            rising_videos = sorted(
                (
                    {
                        "video_id": item["video_id"],
                        "rank": current_video_ranks[item["video_id"]],
                        "rank_delta": previous_video_ranks[item["video_id"]]
                        - current_video_ranks[item["video_id"]],
                    }
                    for item in ranked_videos
                    if item["video_id"] in previous_video_ranks
                    and previous_video_ranks[item["video_id"]]
                    > current_video_ranks[item["video_id"]]
                ),
                key=lambda item: (-item["rank_delta"], item["rank"]),
            )[:6]

            creator_metrics: dict[str, dict[str, Any]] = {}
            ranked_creators: list[dict[str, Any]] = []
            for creator in creators:
                stats = cumulative[("creator", creator.creator_id)]
                daily_stats = self._stats_from_metric(
                    metric_lookup.get(
                        (simulation_day, "creator", creator.creator_id)
                    )
                )
                row = {
                    "creator_id": creator.creator_id,
                    "video_count": sum(
                        video.creator_id == creator.creator_id
                        for video in eligible_videos
                    ),
                    **self._stats_dict(stats),
                    "daily_clicks": daily_stats.clicks,
                    "daily_new_follows": daily_stats.new_follows,
                    "daily_triples": daily_stats.triples,
                }
                creator_metrics[creator.creator_id] = row
                ranked_creators.append(row)
            ranked_creators.sort(
                key=lambda item: (
                    -item["clicks"],
                    -item["new_follows"],
                    -item["triples"],
                    item["creator_id"],
                )
            )
            categories = self._category_slices(
                simulation_day=simulation_day,
                eligible_videos=eligible_videos,
                cumulative=cumulative,
                metric_lookup=metric_lookup,
            )
            models = self._model_slices(
                creators=creators,
                eligible_videos=eligible_videos,
                cumulative=cumulative,
            )
            feeds = self._representative_feeds(
                sessions=sessions_by_day.get(simulation_day, []),
                viewers=viewers,
                video_by_id=video_by_id,
            )
            strategy_notes = []
            for turn in turns_by_day.get(simulation_day, []):
                creator = creator_by_id.get(turn.creator_id)
                video = video_by_id.get(turn.video_id or "")
                if creator is None or not turn.creator_note:
                    continue
                strategy_notes.append(
                    {
                        "creator_id": creator.creator_id,
                        "creator": creator.name,
                        "category": creator.category,
                        "model_alias": creator.model_alias,
                        "note": turn.creator_note,
                        "video_id": turn.video_id,
                        "video_title": video.title if video is not None else None,
                        "expected_reception": turn.expected_reception,
                        "prediction_reason": turn.prediction_reason,
                    }
                )

            moments = self._day_moments(
                simulation_day=simulation_day,
                daily=daily_platform,
                cumulative=cumulative_platform,
                ranked_videos=ranked_videos,
                rising_videos=rising_videos,
                catalog=catalog,
                new_video_count=len(new_videos),
                previous_daily_ctr=previous_daily_ctr,
            )
            llm_cumulative = self._merge_llm_rollups(
                cumulative_llm_days
            )
            slices.append(
                {
                    "day": simulation_day,
                    "date": (anchor + timedelta(days=simulation_day - 1)).isoformat(),
                    "daily": {
                        "videos": len(new_videos),
                        "viewer_sessions": day_row.succeeded_viewer_sessions,
                        **self._stats_dict(daily_platform),
                    },
                    "cumulative": {
                        "videos": len(eligible_videos),
                        "viewer_sessions": cumulative_sessions,
                        **self._stats_dict(cumulative_platform),
                    },
                    "new_video_ids": [video.video_id for video in new_videos],
                    "top_video_ids": [
                        item["video_id"] for item in ranked_videos[:12]
                    ],
                    "rising_videos": rising_videos,
                    "video_metrics": video_metrics,
                    "top_creator_ids": [
                        item["creator_id"] for item in ranked_creators[:10]
                    ],
                    "creator_metrics": creator_metrics,
                    "categories": categories,
                    "creator_models": models,
                    "feeds": feeds,
                    "strategy_notes": strategy_notes,
                    "moments": moments,
                    "llm_cumulative": {
                        key: value
                        for key, value in llm_cumulative.items()
                        if key != "models"
                    },
                }
            )
            previous_video_ranks = current_video_ranks
            previous_daily_ctr = daily_platform.ctr

        return {
            "schema_version": "world-experience-v0.3",
            "run_id": self.config.run_id,
            "parent_run_id": self.config.parent_run_id,
            "parent_day": self.config.parent_day,
            "canonical": self.config.canonical,
            "world_anchor_date": self.config.world_anchor_date,
            "latest_day": days[-1].simulation_day if days else 0,
            "day_count": len(days),
            "creator_count": len(creators),
            "video_catalog": catalog,
            "creator_catalog": creator_catalog,
            "days": slices,
            "display_contract": {
                "source": "completed-day database facts",
                "homepage": "recorded representative sessions",
                "moments": "deterministic clues, not authored plot",
                "model_labels_public": False,
                "llm_accounting_scope": (
                    "all run days"
                    if self.config.canonical
                    else "branch-created calls only; parent calls remain in parent archive"
                ),
            },
        }

    @classmethod
    def _category_slices(
        cls,
        *,
        simulation_day: int,
        eligible_videos: list[VideoRow],
        cumulative: dict[tuple[str, str], VideoStats],
        metric_lookup: dict[tuple[int, str, str], DailyMetricRow],
    ) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for video in eligible_videos:
            row = result.setdefault(
                video.category,
                {
                    "category": video.category,
                    "videos": 0,
                    "impressions": 0,
                    "clicks": 0,
                    "new_follows": 0,
                    "triples": 0,
                    "daily_impressions": 0,
                    "daily_clicks": 0,
                },
            )
            row["videos"] += 1
            stats = cumulative[("video", video.video_id)]
            row["impressions"] += stats.impressions
            row["clicks"] += stats.clicks
            row["new_follows"] += stats.new_follows
            row["triples"] += stats.triples
            daily = cls._stats_from_metric(
                metric_lookup.get((simulation_day, "video", video.video_id))
            )
            row["daily_impressions"] += daily.impressions
            row["daily_clicks"] += daily.clicks
        for row in result.values():
            row["ctr"] = cls._rate(row["clicks"], row["impressions"])
            row["daily_ctr"] = cls._rate(
                row["daily_clicks"], row["daily_impressions"]
            )
        return sorted(
            result.values(),
            key=lambda item: (-item["daily_clicks"], -item["clicks"], item["category"]),
        )

    @classmethod
    def _model_slices(
        cls,
        *,
        creators: list[CreatorRow],
        eligible_videos: list[VideoRow],
        cumulative: dict[tuple[str, str], VideoStats],
    ) -> list[dict[str, Any]]:
        rows: dict[str, dict[str, Any]] = {}
        for creator in creators:
            row = rows.setdefault(
                creator.model_alias,
                {
                    "model_alias": creator.model_alias,
                    "creators": 0,
                    "videos": 0,
                    "impressions": 0,
                    "clicks": 0,
                    "new_follows": 0,
                    "triples": 0,
                },
            )
            row["creators"] += 1
            row["videos"] += sum(
                video.creator_id == creator.creator_id
                for video in eligible_videos
            )
            stats = cumulative[("creator", creator.creator_id)]
            row["impressions"] += stats.impressions
            row["clicks"] += stats.clicks
            row["new_follows"] += stats.new_follows
            row["triples"] += stats.triples
        for row in rows.values():
            row["ctr"] = cls._rate(row["clicks"], row["impressions"])
        return sorted(rows.values(), key=lambda item: item["model_alias"])

    @classmethod
    def _representative_feeds(
        cls,
        *,
        sessions: list[HomepageSessionRow],
        viewers: dict[str, ViewerRow],
        video_by_id: dict[str, VideoRow],
    ) -> list[dict[str, Any]]:
        priority = {"random": 0, "fixed": 1}
        ordered = sorted(
            sessions,
            key=lambda row: (
                priority.get(row.viewer_kind, 9),
                0 if row.cohort_role == "population" else 1,
                row.model_alias or "",
                row.viewer_id,
            ),
        )
        selected: list[HomepageSessionRow] = []
        seen_strata: set[tuple[str, str, str]] = set()
        for homepage in ordered:
            stratum = (
                homepage.viewer_kind,
                homepage.cohort_role,
                homepage.model_alias or "unknown",
            )
            if stratum in seen_strata:
                continue
            seen_strata.add(stratum)
            selected.append(homepage)
            # A small cross-stratum gallery is enough to let a human observer
            # revisit different real homepages without bloating the static
            # 100-day playback artifact with every session.
            if len(selected) >= 12:
                break

        feeds: list[dict[str, Any]] = []
        for homepage in selected:
            viewer = viewers.get(homepage.viewer_id)
            profile = homepage.viewer_snapshot_json
            if viewer is not None:
                viewer_name = viewer.name
            else:
                viewer_name = "随机访客"
            actions = {
                str(item["video_id"]): {
                    "follow": bool(item.get("new_follow")),
                    "triple": bool(item.get("triple")),
                    "channel_pull": item.get("channel_pull"),
                    "episode_impact": item.get("episode_impact"),
                    "follow_activation_probability": item.get(
                        "follow_activation_probability"
                    ),
                    "triple_activation_probability": item.get(
                        "triple_activation_probability"
                    ),
                }
                for item in homepage.post_click_decisions_json
            }
            candidates = []
            clicked = set(homepage.clicked_video_ids_json)
            for item in homepage.candidate_items_json:
                video_id = str(item["video_id"])
                if video_id not in video_by_id:
                    continue
                candidates.append(
                    {
                        "video_id": video_id,
                        "bucket": item["bucket"],
                        "source": item.get("source", "global"),
                        "clicked": video_id in clicked,
                        **actions.get(
                            video_id,
                            {
                                "follow": False,
                                "triple": False,
                                "channel_pull": None,
                                "episode_impact": None,
                                "follow_activation_probability": None,
                                "triple_activation_probability": None,
                            },
                        ),
                    }
                )
            feeds.append(
                {
                    "session_id": homepage.homepage_session_id,
                    "viewer": viewer_name,
                    "viewer_kind": homepage.viewer_kind,
                    "cohort_role": homepage.cohort_role,
                    "model_alias": homepage.model_alias or "unknown",
                    "interest": profile.get(
                        "interest_constellation", profile.get("main_need", "随便看看")
                    ),
                    "need": profile.get("main_need", "随便看看"),
                    "visit_mode": homepage.session_context_json.get(
                        "label", "正常浏览"
                    ),
                    "candidates": candidates,
                    "click_count": len(clicked),
                }
            )
        return feeds

    @classmethod
    def _day_moments(
        cls,
        *,
        simulation_day: int,
        daily: VideoStats,
        cumulative: VideoStats,
        ranked_videos: list[dict[str, Any]],
        rising_videos: list[dict[str, Any]],
        catalog: dict[str, dict[str, Any]],
        new_video_count: int,
        previous_daily_ctr: float | None,
    ) -> list[dict[str, Any]]:
        moments: list[dict[str, Any]] = []
        if daily.new_follows and cumulative.new_follows == daily.new_follows:
            moments.append(
                {
                    "kind": "first_follow",
                    "eyebrow": "世界第一次",
                    "title": "平台出现了第一批关注",
                    "detail": f"Day {simulation_day} 新增 {daily.new_follows} 次关注。",
                }
            )
        if daily.triples and cumulative.triples == daily.triples:
            moments.append(
                {
                    "kind": "first_triple",
                    "eyebrow": "世界第一次",
                    "title": "有人第一次按下三连",
                    "detail": f"Day {simulation_day} 产生 {daily.triples} 次强烈认可。",
                }
            )
        daily_leaders = sorted(
            (
                item for item in ranked_videos if item["daily_clicks"] > 0
            ),
            key=lambda item: (
                -item["daily_clicks"],
                -item["daily_triples"],
                item["video_id"],
            ),
        )
        if daily_leaders:
            leader = daily_leaders[0]
            video = catalog[leader["video_id"]]
            moments.append(
                {
                    "kind": "daily_leader",
                    "eyebrow": "今日吸引力",
                    "title": video["title"],
                    "detail": (
                        f"今天获得 {leader['daily_clicks']} 次点击，"
                        f"UP 主是 {video['creator']}。"
                    ),
                    "video_id": leader["video_id"],
                }
            )
        if rising_videos:
            mover = rising_videos[0]
            video = catalog[mover["video_id"]]
            moments.append(
                {
                    "kind": "rank_rise",
                    "eyebrow": "排名移动",
                    "title": video["title"],
                    "detail": f"累计热度排名上升 {mover['rank_delta']} 位。",
                    "video_id": mover["video_id"],
                }
            )
        if previous_daily_ctr is not None and daily.impressions:
            delta = daily.ctr - previous_daily_ctr
            if abs(delta) >= 0.005:
                direction = "上升" if delta > 0 else "下降"
                moments.append(
                    {
                        "kind": "ctr_shift",
                        "eyebrow": "平台温度",
                        "title": f"今日 CTR {direction}",
                        "detail": f"较前一天变化 {abs(delta) * 100:.2f} 个百分点。",
                    }
                )
        if not moments:
            moments.append(
                {
                    "kind": "quiet_day",
                    "eyebrow": "安静的一天",
                    "title": f"{new_video_count} 条新投稿进入世界",
                    "detail": "没有被系统硬写成戏剧；平静也是这次模拟的真实结果。",
                }
            )
        return moments[:5]

    @staticmethod
    def _stable_cover_seed(video_id: str) -> int:
        return int(hashlib.sha256(video_id.encode("utf-8")).hexdigest()[:8], 16)

    @staticmethod
    def _add_metric(target: VideoStats, source: DailyMetricRow) -> None:
        for field in (
            "impressions",
            "clicks",
            "completed_follow_opportunities",
            "new_follows",
            "completed_post_click_opportunities",
            "triples",
        ):
            setattr(target, field, getattr(target, field) + getattr(source, field))

    @staticmethod
    def _stats_from_metric(metric: DailyMetricRow | None) -> VideoStats:
        if metric is None:
            return VideoStats()
        return VideoStats(
            impressions=metric.impressions,
            clicks=metric.clicks,
            completed_follow_opportunities=metric.completed_follow_opportunities,
            new_follows=metric.new_follows,
            completed_post_click_opportunities=metric.completed_post_click_opportunities,
            triples=metric.triples,
        )

    @classmethod
    def _model_ecology_summary(
        cls,
        *,
        creators: list[CreatorRow],
        videos: list[VideoRow],
        sessions: list[HomepageSessionRow],
        metrics: list[DailyMetricRow],
        cumulative: dict[tuple[str, str], VideoStats],
    ) -> dict[str, Any]:
        creator_by_id = {creator.creator_id: creator for creator in creators}
        video_by_id = {video.video_id: video for video in videos}

        creator_models: dict[str, dict[str, Any]] = {}
        mirror_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for creator in creators:
            stats = cumulative.get(("creator", creator.creator_id), VideoStats())
            row = creator_models.setdefault(
                creator.model_alias,
                {
                    "model_alias": creator.model_alias,
                    "creators": 0,
                    "mirror_creators": 0,
                    "videos": 0,
                    "impressions": 0,
                    "clicks": 0,
                    "follow_opportunities": 0,
                    "new_follows": 0,
                    "post_click_opportunities": 0,
                    "triples": 0,
                },
            )
            row["creators"] += 1
            row["mirror_creators"] += int(creator.mirror_group_id is not None)
            row["videos"] += sum(
                video.creator_id == creator.creator_id for video in videos
            )
            row["impressions"] += stats.impressions
            row["clicks"] += stats.clicks
            row["follow_opportunities"] += stats.completed_follow_opportunities
            row["new_follows"] += stats.new_follows
            row["post_click_opportunities"] += (
                stats.completed_post_click_opportunities
            )
            row["triples"] += stats.triples
            if creator.mirror_group_id is not None:
                mirror_groups[creator.mirror_group_id].append(
                    {
                        "creator_id": creator.creator_id,
                        "name": creator.name,
                        "model_alias": creator.model_alias,
                        "mirror_slot": creator.mirror_slot,
                        "videos": sum(
                            video.creator_id == creator.creator_id
                            for video in videos
                        ),
                        **cls._stats_dict(stats),
                    }
                )

        for row in creator_models.values():
            row["ctr"] = cls._rate(row["clicks"], row["impressions"])
            row["follow_rate"] = cls._rate(
                row["new_follows"], row["follow_opportunities"]
            )
            row["triple_rate"] = cls._rate(
                row["triples"], row["post_click_opportunities"]
            )

        viewer_models: dict[tuple[str, str], dict[str, Any]] = {}
        affinity: dict[tuple[str, str], dict[str, Any]] = {}
        compact_ecology = any(
            metric.entity_type == "viewer_model" for metric in metrics
        )
        if compact_ecology:
            session_counts: dict[str, int] = defaultdict(int)
            for metric in metrics:
                if metric.entity_type == "viewer_model":
                    session_counts[metric.entity_id] += int(metric.score or 0)

            for (entity_type, entity_id), stats in cumulative.items():
                if entity_type == "viewer_model":
                    cohort, viewer_model = parse_metric_dimension_id(
                        entity_id, size=2
                    )
                    viewer_models[(cohort, viewer_model)] = {
                        "cohort_role": cohort,
                        "model_alias": viewer_model,
                        "sessions": session_counts[entity_id],
                        "impressions": stats.impressions,
                        "clicks": stats.clicks,
                        "follow_opportunities": (
                            stats.completed_follow_opportunities
                        ),
                        "new_follows": stats.new_follows,
                        "post_click_opportunities": (
                            stats.completed_post_click_opportunities
                        ),
                        "triples": stats.triples,
                    }
                elif entity_type == "model_affinity":
                    creator_model, viewer_model = parse_metric_dimension_id(
                        entity_id, size=2
                    )
                    affinity[(creator_model, viewer_model)] = {
                        "creator_model_alias": creator_model,
                        "viewer_model_alias": viewer_model,
                        "impressions": stats.impressions,
                        "clicks": stats.clicks,
                        "follow_opportunities": (
                            stats.completed_follow_opportunities
                        ),
                        "new_follows": stats.new_follows,
                        "post_click_opportunities": (
                            stats.completed_post_click_opportunities
                        ),
                        "triples": stats.triples,
                    }

        reference_panels: dict[
            tuple[int, str], list[HomepageSessionRow]
        ] = defaultdict(list)
        for homepage in sessions:
            viewer_model = homepage.model_alias or "unknown"
            cohort = homepage.cohort_role or "population"

            if cohort == "reference" and homepage.mirror_group_id:
                reference_panels[
                    (homepage.simulation_day, homepage.mirror_group_id)
                ].append(homepage)

            # New runs already include every population and reference session
            # in the daily model aggregates. The retained reference rows below
            # are used only to compare identical feeds and choices.
            if compact_ecology:
                continue

            row = viewer_models.setdefault(
                (cohort, viewer_model),
                {
                    "cohort_role": cohort,
                    "model_alias": viewer_model,
                    "sessions": 0,
                    "impressions": 0,
                    "clicks": 0,
                    "follow_opportunities": 0,
                    "new_follows": 0,
                    "post_click_opportunities": 0,
                    "triples": 0,
                },
            )
            row["sessions"] += 1
            row["impressions"] += len(homepage.candidate_items_json)
            row["clicks"] += len(homepage.clicked_video_ids_json)
            row["follow_opportunities"] += sum(
                bool(item.get("follow_available"))
                for item in homepage.post_click_decisions_json
            )
            row["new_follows"] += sum(
                bool(item.get("new_follow"))
                for item in homepage.post_click_decisions_json
            )
            row["post_click_opportunities"] += len(
                homepage.post_click_decisions_json
            )
            row["triples"] += sum(
                bool(item.get("triple"))
                for item in homepage.post_click_decisions_json
            )

            clicked = set(homepage.clicked_video_ids_json)
            decisions = {
                item["video_id"]: item
                for item in homepage.post_click_decisions_json
            }
            for item in homepage.candidate_items_json:
                video = video_by_id.get(str(item["video_id"]))
                if video is None:
                    continue
                creator_model = creator_by_id[video.creator_id].model_alias
                pair = affinity.setdefault(
                    (creator_model, viewer_model),
                    {
                        "creator_model_alias": creator_model,
                        "viewer_model_alias": viewer_model,
                        "impressions": 0,
                        "clicks": 0,
                        "follow_opportunities": 0,
                        "new_follows": 0,
                        "post_click_opportunities": 0,
                        "triples": 0,
                    },
                )
                pair["impressions"] += 1
                video_id = video.video_id
                if video_id in clicked:
                    pair["clicks"] += 1
                decision = decisions.get(video_id)
                if decision is not None:
                    pair["follow_opportunities"] += int(
                        bool(decision.get("follow_available"))
                    )
                    pair["new_follows"] += int(
                        bool(decision.get("new_follow"))
                    )
                    pair["post_click_opportunities"] += 1
                    pair["triples"] += int(bool(decision.get("triple")))

        for rows in (viewer_models.values(), affinity.values()):
            for row in rows:
                row["ctr"] = cls._rate(row["clicks"], row["impressions"])
                row["follow_rate"] = cls._rate(
                    row["new_follows"], row["follow_opportunities"]
                )
                row["triple_rate"] = cls._rate(
                    row["triples"], row["post_click_opportunities"]
                )

        complete_reference_panels = [
            rows for rows in reference_panels.values() if len(rows) >= 2
        ]
        identical_candidates = sum(
            len(
                {
                    tuple(
                        str(item["video_id"])
                        for item in row.candidate_items_json
                    )
                    for row in rows
                }
            )
            == 1
            for rows in complete_reference_panels
        )
        unanimous_clicks = sum(
            len(
                {
                    tuple(sorted(row.clicked_video_ids_json))
                    for row in rows
                }
            )
            == 1
            for rows in complete_reference_panels
        )

        return {
            "interpretation": (
                "主世界中的自然差异，不是厂商 benchmark。模型身份不进入推荐或行为 Prompt；"
                "镜像组提供同起点比较，后续分叉属于生态结果。"
            ),
            "creator_models": sorted(
                creator_models.values(), key=lambda item: item["model_alias"]
            ),
            "viewer_models": sorted(
                viewer_models.values(),
                key=lambda item: (item["cohort_role"], item["model_alias"]),
            ),
            "creator_viewer_affinity": sorted(
                affinity.values(),
                key=lambda item: (
                    item["creator_model_alias"],
                    item["viewer_model_alias"],
                ),
            ),
            "creator_mirror_groups": [
                {
                    "mirror_group_id": group_id,
                    "members": sorted(
                        members, key=lambda item: int(item["mirror_slot"])
                    ),
                }
                for group_id, members in sorted(mirror_groups.items())
            ],
            "reference_viewer_panels": {
                "comparable_panels": len(complete_reference_panels),
                "identical_candidate_panels": identical_candidates,
                "identical_candidate_rate": cls._rate(
                    identical_candidates, len(complete_reference_panels)
                ),
                "unanimous_click_panels": unanimous_clicks,
                "unanimous_click_rate": cls._rate(
                    unanimous_clicks, len(complete_reference_panels)
                ),
            },
        }

    @classmethod
    def _llm_sql_rollups(
        cls,
        *,
        session: Session,
        run_id: str,
        latest_day: int,
    ) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
        """Aggregate the cost ledger in SQL instead of loading every prompt.

        A 100-day world can contain hundreds of thousands of logical calls.
        ORM-loading those rows also deserializes their prompt snapshots, which
        is unnecessary for the Lab's token and cost counters.
        """

        daily: dict[int, dict[str, Any]] = {}
        for simulation_day, logical_calls, succeeded_calls in session.execute(
            select(
                LLMCallRow.simulation_day,
                func.count(LLMCallRow.call_id),
                func.sum(
                    case((LLMCallRow.status == "succeeded", 1), else_=0)
                ),
            )
            .where(
                LLMCallRow.run_id == run_id,
                LLMCallRow.simulation_day <= latest_day,
            )
            .group_by(LLMCallRow.simulation_day)
        ):
            row = cls._empty_llm_rollup()
            row["logical_calls"] = int(logical_calls or 0)
            row["succeeded_calls"] = int(succeeded_calls or 0)
            daily[int(simulation_day)] = row

        attempt_query = (
            select(
                LLMCallRow.simulation_day,
                LLMAttemptRow.currency,
                LLMAttemptRow.cost_is_estimate,
                func.count(LLMAttemptRow.attempt_id),
                func.sum(
                    case((LLMAttemptRow.status == "failed", 1), else_=0)
                ),
                func.sum(func.coalesce(LLMAttemptRow.input_tokens, 0)),
                func.sum(func.coalesce(LLMAttemptRow.cached_input_tokens, 0)),
                func.sum(func.coalesce(LLMAttemptRow.output_tokens, 0)),
                func.sum(func.coalesce(LLMAttemptRow.reasoning_tokens, 0)),
                # SQLite stores Numeric as a floating value. Round every
                # ledger row to the declared 9-decimal precision before SUM;
                # otherwise sub-nanodollar representation noise accumulates
                # across tens of thousands of attempts.
                func.sum(
                    func.round(
                        func.coalesce(LLMAttemptRow.estimated_cost, 0), 9
                    )
                ),
            )
            .join(LLMCallRow, LLMCallRow.call_id == LLMAttemptRow.call_id)
            .where(
                LLMCallRow.run_id == run_id,
                LLMCallRow.simulation_day <= latest_day,
            )
            .group_by(
                LLMCallRow.simulation_day,
                LLMAttemptRow.currency,
                LLMAttemptRow.cost_is_estimate,
            )
        )
        for values in session.execute(attempt_query):
            (
                simulation_day,
                currency,
                is_estimate,
                attempts,
                failed,
                input_tokens,
                cached_tokens,
                output_tokens,
                reasoning_tokens,
                amount,
            ) = values
            row = daily.setdefault(int(simulation_day), cls._empty_llm_rollup())
            cls._apply_attempt_rollup(
                row,
                currency=str(currency),
                is_estimate=bool(is_estimate),
                attempts=int(attempts or 0),
                failed_attempts=int(failed or 0),
                input_tokens=int(input_tokens or 0),
                cached_input_tokens=int(cached_tokens or 0),
                output_tokens=int(output_tokens or 0),
                reasoning_tokens=int(reasoning_tokens or 0),
                amount=Decimal(amount or 0),
            )

        model_accumulators: dict[str, dict[str, Any]] = {}
        model_query = (
            select(
                LLMAttemptRow.requested_model,
                LLMAttemptRow.currency,
                LLMAttemptRow.cost_is_estimate,
                func.count(LLMAttemptRow.attempt_id),
                func.sum(
                    case((LLMAttemptRow.status == "succeeded", 1), else_=0)
                ),
                func.sum(func.coalesce(LLMAttemptRow.input_tokens, 0)),
                func.sum(func.coalesce(LLMAttemptRow.cached_input_tokens, 0)),
                func.sum(func.coalesce(LLMAttemptRow.output_tokens, 0)),
                func.sum(func.coalesce(LLMAttemptRow.reasoning_tokens, 0)),
                func.sum(
                    func.round(
                        func.coalesce(LLMAttemptRow.estimated_cost, 0), 9
                    )
                ),
            )
            .join(LLMCallRow, LLMCallRow.call_id == LLMAttemptRow.call_id)
            .where(
                LLMCallRow.run_id == run_id,
                LLMCallRow.simulation_day <= latest_day,
            )
            .group_by(
                LLMAttemptRow.requested_model,
                LLMAttemptRow.currency,
                LLMAttemptRow.cost_is_estimate,
            )
        )
        for values in session.execute(model_query):
            (
                model_name,
                currency,
                is_estimate,
                attempts,
                succeeded,
                input_tokens,
                cached_tokens,
                output_tokens,
                reasoning_tokens,
                amount,
            ) = values
            row = model_accumulators.setdefault(
                str(model_name), cls._empty_model_rollup(str(model_name))
            )
            row["attempts"] += int(attempts or 0)
            row["succeeded"] += int(succeeded or 0)
            row["input_tokens"] += int(input_tokens or 0)
            row["cached_input_tokens"] += int(cached_tokens or 0)
            row["output_tokens"] += int(output_tokens or 0)
            row["reasoning_tokens"] += int(reasoning_tokens or 0)
            cost_key = (
                "estimated_costs" if is_estimate else "provider_costs"
            )
            row[cost_key][str(currency)] += Decimal(amount or 0)
            if is_estimate:
                row["unreported_cost_attempts"] += int(attempts or 0)
            else:
                row["provider_reported_attempts"] += int(attempts or 0)

        provider_query = (
            select(
                LLMAttemptRow.requested_model,
                func.coalesce(LLMAttemptRow.resolved_provider, "unknown"),
                func.count(LLMAttemptRow.attempt_id),
            )
            .join(LLMCallRow, LLMCallRow.call_id == LLMAttemptRow.call_id)
            .where(
                LLMCallRow.run_id == run_id,
                LLMCallRow.simulation_day <= latest_day,
            )
            .group_by(
                LLMAttemptRow.requested_model,
                func.coalesce(LLMAttemptRow.resolved_provider, "unknown"),
            )
        )
        for model_name, provider, attempts in session.execute(provider_query):
            row = model_accumulators.setdefault(
                str(model_name), cls._empty_model_rollup(str(model_name))
            )
            row["provider_counts"][str(provider)] += int(attempts or 0)

        daily_rows = {
            day: cls._finalize_llm_rollup(row)
            for day, row in daily.items()
        }
        model_rows = [
            cls._finalize_model_rollup(row)
            for _, row in sorted(model_accumulators.items())
        ]
        return daily_rows, model_rows

    @staticmethod
    def _empty_llm_rollup() -> dict[str, Any]:
        return {
            "logical_calls": 0,
            "succeeded_calls": 0,
            "attempts": 0,
            "failed_attempts": 0,
            "provider_reported_attempts": 0,
            "unreported_cost_attempts": 0,
            "estimated_attempts": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "provider_costs": defaultdict(Decimal),
            "estimated_costs": defaultdict(Decimal),
        }

    @staticmethod
    def _empty_model_rollup(model_name: str) -> dict[str, Any]:
        return {
            "model": model_name,
            "attempts": 0,
            "succeeded": 0,
            "input_tokens": 0,
            "cached_input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "provider_reported_attempts": 0,
            "unreported_cost_attempts": 0,
            "provider_counts": defaultdict(int),
            "provider_costs": defaultdict(Decimal),
            "estimated_costs": defaultdict(Decimal),
        }

    @staticmethod
    def _apply_attempt_rollup(
        row: dict[str, Any],
        *,
        currency: str,
        is_estimate: bool,
        attempts: int,
        failed_attempts: int,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        amount: Decimal,
    ) -> None:
        row["attempts"] += attempts
        row["failed_attempts"] += failed_attempts
        row["input_tokens"] += input_tokens
        row["cached_input_tokens"] += cached_input_tokens
        row["output_tokens"] += output_tokens
        row["reasoning_tokens"] += reasoning_tokens
        if is_estimate:
            row["unreported_cost_attempts"] += attempts
            row["estimated_attempts"] += attempts
            row["estimated_costs"][currency] += amount
        else:
            row["provider_reported_attempts"] += attempts
            row["provider_costs"][currency] += amount

    @staticmethod
    def _money_rows(amounts: dict[str, Decimal]) -> list[dict[str, Any]]:
        return [
            {"currency": currency, "amount": float(amount)}
            for currency, amount in sorted(amounts.items())
        ]

    @classmethod
    def _finalize_llm_rollup(cls, row: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: value
            for key, value in row.items()
            if key not in {"provider_costs", "estimated_costs"}
        }
        result["provider_cost_by_currency"] = cls._money_rows(
            row["provider_costs"]
        )
        result["estimated_cost_by_currency"] = cls._money_rows(
            row["estimated_costs"]
        )
        result["cost_by_currency"] = result["provider_cost_by_currency"]
        return result

    @classmethod
    def _finalize_model_rollup(cls, row: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: value
            for key, value in row.items()
            if key
            not in {"provider_counts", "provider_costs", "estimated_costs"}
        }
        result["resolved_providers"] = [
            {"provider": provider, "attempts": attempts}
            for provider, attempts in sorted(row["provider_counts"].items())
        ]
        result["provider_cost_by_currency"] = cls._money_rows(
            row["provider_costs"]
        )
        result["estimated_cost_by_currency"] = cls._money_rows(
            row["estimated_costs"]
        )
        result["cost_by_currency"] = result["provider_cost_by_currency"]
        return result

    @classmethod
    def _merge_llm_rollups(
        cls, rows: Any
    ) -> dict[str, Any]:
        total = cls._empty_llm_rollup()
        for row in rows:
            for key in (
                "logical_calls",
                "succeeded_calls",
                "attempts",
                "failed_attempts",
                "provider_reported_attempts",
                "unreported_cost_attempts",
                "estimated_attempts",
                "input_tokens",
                "cached_input_tokens",
                "output_tokens",
                "reasoning_tokens",
            ):
                total[key] += int(row.get(key, 0))
            for public_key, private_key in (
                ("provider_cost_by_currency", "provider_costs"),
                ("estimated_cost_by_currency", "estimated_costs"),
            ):
                for item in row.get(public_key, []):
                    total[private_key][str(item["currency"])] += Decimal(
                        str(item["amount"])
                    )
        return cls._finalize_llm_rollup(total)

    @staticmethod
    def _cumulative_metrics(
        rows: list[DailyMetricRow],
    ) -> dict[tuple[str, str], VideoStats]:
        result: dict[tuple[str, str], VideoStats] = defaultdict(VideoStats)
        for row in rows:
            stats = result[(row.entity_type, row.entity_id)]
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

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return numerator / denominator if denominator else 0.0

    @classmethod
    def _stats_dict(cls, stats: VideoStats) -> dict[str, Any]:
        return {
            "impressions": stats.impressions,
            "clicks": stats.clicks,
            "new_follows": stats.new_follows,
            "triples": stats.triples,
            "ctr": stats.ctr,
            "follow_rate": stats.follow_rate,
            "triple_rate": stats.triple_rate,
        }
