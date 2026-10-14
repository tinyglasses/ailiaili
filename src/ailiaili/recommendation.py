from __future__ import annotations

import random
from collections import defaultdict
from collections.abc import Callable, Iterable

from ailiaili.config import SimulationConfig
from ailiaili.domain import CandidateItem, VideoStats


def smoothed_performance(stats: VideoStats) -> float:
    ctr = (stats.clicks + 1.0) / (stats.impressions + 10.0)
    follow_rate = (stats.new_follows + 1.0) / (
        stats.completed_follow_opportunities + 20.0
    )
    triple_rate = (stats.triples + 0.6) / (
        stats.completed_post_click_opportunities + 20.0
    )
    return 0.60 * ctr + 0.20 * follow_rate + 0.20 * triple_rate


def recommendation_weight(
    *,
    stats: VideoStats,
    published_day: int,
    current_day: int,
    age_half_life_days: float,
) -> float:
    age = max(0, current_day - published_day)
    age_discount = 0.5 ** (age / age_half_life_days)
    return max(smoothed_performance(stats) * age_discount, 1e-9)


class Recommender:
    """Transparent global ranking with two deliberately thin personal lanes.

    The global lane retains the existing exploit/cold/explore rules. Interest
    and following merely receive a few extra candidate slots and do not use a
    separate personalized scoring formula.  Their resulting impressions and
    actions still enter the video's unified platform performance, deliberately
    allowing strong core-audience reception to support later global expansion.
    """

    def __init__(self, config: SimulationConfig):
        self.config = config

    def sample(
        self,
        *,
        videos: list[dict[str, object]],
        stats_by_video: dict[str, VideoStats],
        current_day: int,
        excluded_video_ids: set[str],
        seed: int,
        interest_categories: set[str] | None = None,
        followed_creator_ids: set[str] | None = None,
        shown_history: dict[str, int] | None = None,
    ) -> list[CandidateItem]:
        interest_categories = interest_categories or set()
        followed_creator_ids = followed_creator_ids or set()
        shown_history = shown_history or {}

        all_eligible = [
            video
            for video in videos
            if str(video["video_id"]) not in excluded_video_ids
        ]
        all_eligible.sort(key=lambda video: str(video["video_id"]))
        fresh_eligible = [
            video
            for video in all_eligible
            if str(video["video_id"]) not in shown_history
        ]

        rng = random.Random(seed)
        selected: list[dict[str, object]] = []
        selected_ids: set[str] = set()
        creator_counts: dict[str, int] = defaultdict(int)
        buckets: dict[str, str] = {}
        sources: dict[str, str] = {}

        reserved = self.config.interest_slots_max + self.config.following_slots_max
        global_target = self.config.homepage_size - reserved
        self._fill_global_lanes(
            eligible=fresh_eligible,
            target_size=global_target,
            stats_by_video=stats_by_video,
            current_day=current_day,
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
        )

        # A follow means only "give this author a few extra chances". It does
        # not guarantee a quota and never forces a stale or duplicate card.
        following_pool = [
            video
            for video in fresh_eligible
            if str(video["video_id"]) not in selected_ids
            and str(video["creator_id"]) in followed_creator_ids
        ]
        self._select_weighted(
            pool=following_pool,
            count=self.config.following_slots_max,
            weight=lambda video: self._performance_weight(
                video=video,
                stats_by_video=stats_by_video,
                current_day=current_day,
            ),
            bucket=lambda video: self._performance_bucket(
                video=video, stats_by_video=stats_by_video
            ),
            source="following",
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
            total_limit=self.config.homepage_size,
        )

        interest_pool = [
            video
            for video in fresh_eligible
            if str(video["video_id"]) not in selected_ids
            and str(video.get("category", "")) in interest_categories
        ]
        self._select_weighted(
            pool=interest_pool,
            count=self.config.interest_slots_max,
            weight=lambda video: self._performance_weight(
                video=video,
                stats_by_video=stats_by_video,
                current_day=current_day,
            ),
            bucket=lambda video: self._performance_bucket(
                video=video, stats_by_video=stats_by_video
            ),
            source="interest",
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
            total_limit=self.config.homepage_size,
        )

        # Missing personal cards are ordinary global cards, rather than stale
        # content inserted merely to satisfy a nominal 4+4 quota.
        self._fill_global_fallback(
            eligible=fresh_eligible,
            stats_by_video=stats_by_video,
            current_day=current_day,
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
        )

        # Reference panels share exposure history. This is not permanent
        # exclusion: once unseen inventory cannot fill the page, reuse the
        # least-recently-shown cards. A 100-day panel therefore cannot exhaust
        # the world's finite video inventory or lose same-feed comparability.
        if len(selected) < self.config.homepage_size and shown_history:
            self._fill_least_recently_shown(
                eligible=all_eligible,
                shown_history=shown_history,
                stats_by_video=stats_by_video,
                current_day=current_day,
                rng=rng,
                selected=selected,
                selected_ids=selected_ids,
                creator_counts=creator_counts,
                buckets=buckets,
                sources=sources,
            )

        rng.shuffle(selected)
        return [
            CandidateItem(
                video_id=str(video["video_id"]),
                bucket=buckets[str(video["video_id"])],
                source=sources[str(video["video_id"])],
            )
            for video in selected[: self.config.homepage_size]
        ]

    def _fill_global_lanes(
        self,
        *,
        eligible: list[dict[str, object]],
        target_size: int,
        stats_by_video: dict[str, VideoStats],
        current_day: int,
        rng: random.Random,
        selected: list[dict[str, object]],
        selected_ids: set[str],
        creator_counts: dict[str, int],
        buckets: dict[str, str],
        sources: dict[str, str],
    ) -> None:
        exploit_count, cold_count, explore_count = self.config.bucket_counts(
            target_size
        )
        cold_pool = [
            video
            for video in eligible
            if self._is_cold(video=video, stats_by_video=stats_by_video)
        ]
        self._select_weighted(
            pool=cold_pool,
            count=cold_count,
            weight=lambda video: 1.0
            / (
                1.0
                + stats_by_video.get(
                    str(video["video_id"]), VideoStats()
                ).impressions
            ),
            bucket="cold",
            source="global",
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
        )

        explore_pool = [
            video for video in eligible if str(video["video_id"]) not in selected_ids
        ]
        self._select_weighted(
            pool=explore_pool,
            count=explore_count,
            weight=lambda _: 1.0,
            bucket="explore",
            source="global",
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
        )

        exploit_pool = [
            video
            for video in eligible
            if str(video["video_id"]) not in selected_ids
            and not self._is_cold(video=video, stats_by_video=stats_by_video)
        ]
        self._select_weighted(
            pool=exploit_pool,
            count=exploit_count,
            weight=lambda video: self._performance_weight(
                video=video,
                stats_by_video=stats_by_video,
                current_day=current_day,
            ),
            bucket="exploit",
            source="global",
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
            total_limit=target_size,
        )

        # A young world may not yet have enough mature videos. Fill the global
        # lane without changing the label of the rule that selected each card.
        self._fill_global_fallback(
            eligible=eligible,
            stats_by_video=stats_by_video,
            current_day=current_day,
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
            total_limit=target_size,
        )

    def _fill_global_fallback(
        self,
        *,
        eligible: list[dict[str, object]],
        stats_by_video: dict[str, VideoStats],
        current_day: int,
        rng: random.Random,
        selected: list[dict[str, object]],
        selected_ids: set[str],
        creator_counts: dict[str, int],
        buckets: dict[str, str],
        sources: dict[str, str],
        total_limit: int | None = None,
    ) -> None:
        limit = self.config.homepage_size if total_limit is None else total_limit
        remaining = limit - len(selected)
        if remaining <= 0:
            return
        cold_pool = [
            video
            for video in eligible
            if str(video["video_id"]) not in selected_ids
            and self._is_cold(video=video, stats_by_video=stats_by_video)
        ]
        self._select_weighted(
            pool=cold_pool,
            count=remaining,
            weight=lambda video: 1.0
            / (
                1.0
                + stats_by_video.get(
                    str(video["video_id"]), VideoStats()
                ).impressions
            ),
            bucket="cold",
            source="global",
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
            total_limit=limit,
        )
        remaining = limit - len(selected)
        if remaining <= 0:
            return
        mature_pool = [
            video
            for video in eligible
            if str(video["video_id"]) not in selected_ids
        ]
        self._select_weighted(
            pool=mature_pool,
            count=remaining,
            weight=lambda video: self._performance_weight(
                video=video,
                stats_by_video=stats_by_video,
                current_day=current_day,
            ),
            bucket=lambda video: self._performance_bucket(
                video=video, stats_by_video=stats_by_video
            ),
            source="global",
            rng=rng,
            selected=selected,
            selected_ids=selected_ids,
            creator_counts=creator_counts,
            buckets=buckets,
            sources=sources,
            total_limit=limit,
        )

    def _fill_least_recently_shown(
        self,
        *,
        eligible: list[dict[str, object]],
        shown_history: dict[str, int],
        stats_by_video: dict[str, VideoStats],
        current_day: int,
        rng: random.Random,
        selected: list[dict[str, object]],
        selected_ids: set[str],
        creator_counts: dict[str, int],
        buckets: dict[str, str],
        sources: dict[str, str],
    ) -> None:
        last_shown_days = sorted(
            {
                shown_history[str(video["video_id"])]
                for video in eligible
                if str(video["video_id"]) in shown_history
                and str(video["video_id"]) not in selected_ids
            }
        )
        for last_shown_day in last_shown_days:
            remaining = self.config.homepage_size - len(selected)
            if remaining <= 0:
                return
            pool = [
                video
                for video in eligible
                if str(video["video_id"]) not in selected_ids
                and shown_history.get(str(video["video_id"])) == last_shown_day
            ]
            self._select_weighted(
                pool=pool,
                count=remaining,
                weight=lambda video: self._performance_weight(
                    video=video,
                    stats_by_video=stats_by_video,
                    current_day=current_day,
                ),
                bucket=lambda video: self._performance_bucket(
                    video=video, stats_by_video=stats_by_video
                ),
                source="global",
                rng=rng,
                selected=selected,
                selected_ids=selected_ids,
                creator_counts=creator_counts,
                buckets=buckets,
                sources=sources,
                total_limit=self.config.homepage_size,
            )

    def _performance_weight(
        self,
        *,
        video: dict[str, object],
        stats_by_video: dict[str, VideoStats],
        current_day: int,
    ) -> float:
        return recommendation_weight(
            stats=stats_by_video.get(str(video["video_id"]), VideoStats()),
            published_day=int(video["published_day"]),
            current_day=current_day,
            age_half_life_days=self.config.age_half_life_days,
        )

    def _is_cold(
        self,
        *,
        video: dict[str, object],
        stats_by_video: dict[str, VideoStats],
    ) -> bool:
        return (
            stats_by_video.get(str(video["video_id"]), VideoStats()).impressions
            < self.config.cold_impression_target
        )

    def _performance_bucket(
        self,
        *,
        video: dict[str, object],
        stats_by_video: dict[str, VideoStats],
    ) -> str:
        return (
            "cold"
            if self._is_cold(video=video, stats_by_video=stats_by_video)
            else "exploit"
        )

    def _select_weighted(
        self,
        *,
        pool: Iterable[dict[str, object]],
        count: int,
        weight: Callable[[dict[str, object]], float],
        bucket: str | Callable[[dict[str, object]], str],
        source: str,
        rng: random.Random,
        selected: list[dict[str, object]],
        selected_ids: set[str],
        creator_counts: dict[str, int],
        buckets: dict[str, str],
        sources: dict[str, str],
        total_limit: int | None = None,
    ) -> None:
        available = list(pool)
        picks = 0
        while available and picks < count:
            if total_limit is not None and len(selected) >= total_limit:
                return
            valid = [
                video
                for video in available
                if creator_counts[str(video["creator_id"])]
                < self.config.max_videos_per_creator
            ]
            if not valid:
                return
            weights = [max(weight(video), 0.0) for video in valid]
            total = sum(weights)
            if total <= 0:
                chosen = valid[rng.randrange(len(valid))]
            else:
                cursor = rng.random() * total
                chosen = valid[-1]
                for video, item_weight in zip(valid, weights, strict=True):
                    cursor -= item_weight
                    if cursor <= 0:
                        chosen = video
                        break
            video_id = str(chosen["video_id"])
            creator_id = str(chosen["creator_id"])
            selected.append(chosen)
            selected_ids.add(video_id)
            creator_counts[creator_id] += 1
            buckets[video_id] = bucket(chosen) if callable(bucket) else bucket
            sources[video_id] = source
            available = [video for video in available if video is not chosen]
            picks += 1
