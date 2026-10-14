from __future__ import annotations

import json
import os
import hashlib
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ailiaili.config import SimulationConfig
from ailiaili.continuation_contract import continuation_code_sha256
from ailiaili.creator.service import CreatorService
from ailiaili.domain import CreatorMemory, DaySummary, PostClickDecision, VideoStats
from ailiaili.llm.config import LLMRuntimeConfig
from ailiaili.llm.gateway import (
    BudgetExceeded,
    IndeterminateAttempt,
    ModelCallExhausted,
    ModelGateway,
    TailDiscardRequested,
    TailDiscardSignal,
)
from ailiaili.llm.provider import ChatProvider, RoutingChatProvider
from ailiaili.persistence.database import Database
from ailiaili.persistence.models import (
    CreatorRow,
    CreatorTurnRow,
    DailyMetricRow,
    DayRow,
    FollowRow,
    HomepageSessionRow,
    LLMCallRow,
    LLMAttemptRow,
    RunRow,
    PanelShownHistoryRow,
    VideoRow,
    ViewerClickHistoryRow,
    ViewerRow,
    utc_now,
)
from ailiaili.recommendation import Recommender
from ailiaili.persona import (
    CreatorPopulation,
    ViewerPopulationSpec,
    ViewerSessionContext,
)
from ailiaili.stubs import (
    choose_clicks,
    creator_blueprint,
    creator_turn,
    declaration,
    decide_post_click,
    initial_memory,
    make_video,
    viewer_profile,
)
from ailiaili.utils import stable_seed
from ailiaili.utils import metric_dimension_id
from ailiaili.viewer.service import ViewerActor, ViewerService
from ailiaili.viewer.behavior import activate_post_click


class InjectedFailure(RuntimeError):
    """Test-only interruption after persisted work."""


def resume_request_start_batch_size(
    *,
    configured_batch_size: int,
    attempt_count: int,
    rate_limit_count: int,
) -> int:
    """Choose a resume launch rate from one recent completed day.

    A few recovered 429s among roughly ten thousand daily attempts are normal
    upstream noise.  They must not permanently halve every later day's launch
    rate.  Only a meaningful recent rate-limit incident reduces the configured
    ceiling; each individual 429 still receives its ordinary retry backoff.
    """

    if configured_batch_size < 1:
        raise ValueError("configured_batch_size must be positive")
    if attempt_count < 0 or rate_limit_count < 0:
        raise ValueError("attempt counts cannot be negative")
    if rate_limit_count > attempt_count:
        raise ValueError("rate_limit_count cannot exceed attempt_count")
    rate = rate_limit_count / attempt_count if attempt_count else 0.0
    if rate_limit_count >= 100 and rate > 0.05:
        return 1
    if rate_limit_count >= 20 and rate >= 0.01:
        return max(1, configured_batch_size // 2)
    return configured_batch_size


@dataclass
class _ViewerSessionWork:
    """Mutable orchestration state for one viewer's two-stage decision chain."""

    actor: ViewerActor
    session_id: str
    candidate_items: list[dict[str, Any]]
    candidate_videos: list[dict[str, object]]
    context: ViewerSessionContext
    note_mode: str
    session_seed: int
    followed_creator_ids: set[str]
    delivery_phase: str = "full"
    catalog_through_day: int | None = None
    status: str = "pending"
    clicked_video_ids: list[str] = field(default_factory=list)
    post_click_results: dict[int, dict[str, Any]] = field(default_factory=dict)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    technical_failures: list[dict[str, Any]] = field(default_factory=list)
    new_follow_creator_ids: list[str] = field(default_factory=list)


class SimulationRunner:
    def __init__(
        self,
        config: SimulationConfig,
        *,
        creator_service: CreatorService | None = None,
        viewer_service: ViewerService | None = None,
    ):
        self.config = config
        self.database = Database(config.database_path)
        self.recommender = Recommender(config)
        self.creator_service = creator_service
        self.viewer_service = viewer_service

    def _emit_progress(
        self,
        event: str,
        *,
        simulation_day: int,
        **details: Any,
    ) -> None:
        """Persist and print a lightweight orchestration heartbeat.

        This is operational evidence only. It is deliberately kept outside the
        simulation tables and never becomes an input to creators, viewers, or
        recommendation.
        """

        record = {
            "timestamp": utc_now().isoformat(),
            "run_id": self.config.run_id,
            "simulation_day": simulation_day,
            "event": event,
            **details,
        }
        self.config.run_dir.mkdir(parents=True, exist_ok=True)
        history_path = self.config.run_dir / "runtime_progress.jsonl"
        with history_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
        latest_path = self.config.run_dir / "runtime_status.json"
        temporary_path = self.config.run_dir / (
            f"runtime_status.{os.getpid()}.tmp"
        )
        temporary_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # On Windows, a browser or status reader can hold the destination for
        # a few milliseconds. Monitoring is an observation concern and must
        # never terminate the simulation. Retry briefly, then leave the
        # append-only JSONL as the authoritative operational record.
        for attempt in range(5):
            try:
                temporary_path.replace(latest_path)
                break
            except PermissionError:
                if attempt < 4:
                    time.sleep(0.02 * (attempt + 1))
        else:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        detail_text = " ".join(
            f"{key}={value}" for key, value in details.items()
        )
        print(
            f"[AiliAili] Day {simulation_day:02d} {event} {detail_text}".rstrip(),
            flush=True,
        )

    @classmethod
    def with_llm(
        cls,
        config: SimulationConfig,
        *,
        runtime: LLMRuntimeConfig | None = None,
        provider: ChatProvider | None = None,
    ) -> "SimulationRunner":
        if config.execution_mode != "llm":
            raise ValueError("with_llm requires execution_mode=llm")
        assert config.creator_persona_path is not None
        assert config.viewer_persona_path is not None
        assert config.llm_runtime_path is not None
        runtime = runtime or LLMRuntimeConfig.from_yaml(config.llm_runtime_path)
        viewer_population = ViewerPopulationSpec.from_yaml(
            config.viewer_persona_path
        )
        viewer_click_caps = {
            mode.click_cap for mode in viewer_population.attention_modes
        }
        # The top-level field is also used by the stub engine and is visible in
        # manifests. Never allow it to describe a different world than the
        # production Persona session contract actually enforced by prompts.
        if viewer_click_caps and max(viewer_click_caps) != config.max_clicks:
            raise ValueError(
                "config max_clicks must equal the maximum Viewer Persona "
                f"click cap ({config.max_clicks} != {max(viewer_click_caps)})"
            )

        required_aliases = dict.fromkeys(
            [
                *config.resolved_creator_model_aliases,
                *config.resolved_viewer_model_aliases,
                *config.reference_viewer_model_aliases,
            ]
        )
        required_specs = [runtime.model(alias) for alias in required_aliases]
        if provider is None:
            missing_credentials = sorted(
                {
                    spec.api_key_env
                    for spec in required_specs
                    if spec.api_key_env is not None and not os.environ.get(spec.api_key_env, "").strip()
                }
            )
            if missing_credentials:
                # This check intentionally runs before constructing Database or
                # ModelGateway. A typo must not leave a half-created Day 0.
                raise RuntimeError(
                    "missing model credentials: "
                    + ", ".join(missing_credentials)
                )

        runner = cls(config)
        request_start_batch_size = config.request_start_batch_size
        if provider is None and config.database_path.exists():
            # Learn only from the most recent completed day. Historical 429s
            # are not a permanent sentence for every later day, and a few
            # recovered upstream errors among thousands of calls are ordinary
            # noise rather than evidence that the configured launch rate is
            # unsafe.
            runner.database.create_schema()
            with runner.database.session() as session:
                run = session.get(RunRow, config.run_id)
                recent_day = run.last_completed_day if run is not None else -1
                recent_openrouter_attempts = int(
                    session.scalar(
                        select(func.count())
                        .select_from(LLMAttemptRow)
                        .join(LLMCallRow, LLMAttemptRow.call_id == LLMCallRow.call_id)
                        .where(
                            LLMAttemptRow.run_id == config.run_id,
                            LLMCallRow.simulation_day == recent_day,
                            LLMCallRow.provider == "openrouter",
                        )
                    )
                    or 0
                )
                recent_rate_limits = int(
                    session.scalar(
                        select(func.count())
                        .select_from(LLMAttemptRow)
                        .join(LLMCallRow, LLMAttemptRow.call_id == LLMCallRow.call_id)
                        .where(
                            LLMAttemptRow.run_id == config.run_id,
                            LLMCallRow.simulation_day == recent_day,
                            LLMCallRow.provider == "openrouter",
                            LLMAttemptRow.error_code == "http_429",
                        )
                    )
                    or 0
                )
            request_start_batch_size = resume_request_start_batch_size(
                configured_batch_size=config.request_start_batch_size,
                attempt_count=recent_openrouter_attempts,
                rate_limit_count=recent_rate_limits,
            )
            if request_start_batch_size < config.request_start_batch_size:
                rate = (
                    recent_rate_limits / recent_openrouter_attempts
                    if recent_openrouter_attempts
                    else 0.0
                )
                print(
                    "[AiliAili] Recent completed-day 429 incident detected; "
                    f"resume launch batch {config.request_start_batch_size}"
                    f"->{request_start_batch_size} per "
                    f"{config.request_start_interval_ms:g}ms "
                    f"({recent_rate_limits}/{recent_openrouter_attempts}, "
                    f"{rate:.2%})",
                    flush=True,
                )
        gateway = ModelGateway(
            database=runner.database,
            run_dir=config.run_dir,
            budget=runtime.budget,
            evidence_mode=config.evidence_mode,
            provider=provider
            or RoutingChatProvider(
                timeout_seconds=runtime.budget.timeout_seconds,
                max_connections=config.max_concurrency,
                request_start_interval_ms=config.request_start_interval_ms,
                request_start_batch_size=request_start_batch_size,
            ),
        )
        runner.creator_service = CreatorService(
            database=runner.database,
            gateway=gateway,
            runtime=runtime,
            population=CreatorPopulation.from_yaml(config.creator_persona_path),
            creator_ids=config.creator_ids,
            creator_count=config.creator_count,
            model_aliases=config.resolved_creator_model_aliases,
            model_weights=config.creator_model_weights,
            model_assignment=config.creator_model_assignment,
            world_anchor_date=config.world_anchor_date,
            run_seed=config.seed,
        )
        runner.viewer_service = ViewerService(
            database=runner.database,
            gateway=gateway,
            runtime=runtime,
            population=viewer_population,
            model_aliases=config.resolved_viewer_model_aliases,
            model_weights=config.viewer_model_weights,
            reference_model_aliases=config.reference_viewer_model_aliases,
            reference_persona_count=config.reference_viewer_persona_count,
            reference_constellation_ids=(
                config.reference_viewer_constellation_ids
            ),
            fixed_viewer_count=config.viewer_count,
            random_visitor_count=config.random_visitor_count,
            fixed_note_mode=config.viewer_note_mode,
            random_note_sample_rate=config.random_visitor_note_sample_rate,
            run_seed=config.seed,
        )
        return runner

    def initialize(self) -> None:
        if self.config.execution_mode == "llm" and (
            self.creator_service is None or self.viewer_service is None
        ):
            raise RuntimeError(
                "llm run requires SimulationRunner.with_llm(...)"
            )
        self._validate_branch_dependencies()
        self.database.create_schema()
        with self.database.session() as session, session.begin():
            existing = session.get(RunRow, self.config.run_id)
            if existing is not None:
                # Additive config fields with defaults must not make an old
                # completed rehearsal unreadable. Validating the stored JSON
                # upgrades only the comparison view; the historical manifest
                # itself remains untouched.
                stored_config = SimulationConfig.model_validate(
                    existing.config_json
                ).manifest_dict()
                if stored_config != self.config.manifest_dict():
                    raise ValueError(
                        "run_id already exists with a different configuration"
                    )
            else:
                is_llm = self.config.execution_mode == "llm"
                session.add(
                    RunRow(
                        run_id=self.config.run_id,
                        seed=self.config.seed,
                        status="running",
                        config_json=self.config.manifest_dict(),
                        last_completed_day=-1 if is_llm else 0,
                    )
                )
                # Bootstrap rows reference the run.  Flush the parent explicitly
                # instead of adding an ORM relationship graph just for setup.
                session.flush()
                session.add(
                    DayRow(
                        run_id=self.config.run_id,
                        simulation_day=0,
                        status="running" if is_llm else "completed",
                        planned_creator_turns=self.config.creator_count,
                        succeeded_creator_turns=(
                            0 if is_llm else self.config.creator_count
                        ),
                        planned_viewer_sessions=0,
                        succeeded_viewer_sessions=0,
                        completed_at=None if is_llm else utc_now(),
                    )
                )
                if is_llm:
                    assert self.creator_service is not None
                    assert self.viewer_service is not None
                    for values in self.creator_service.creator_row_values(
                        run_id=self.config.run_id,
                        publish_interval_days=self.config.publish_interval_days,
                    ):
                        session.add(CreatorRow(**values))
                    for values in self.viewer_service.viewer_row_values(
                        run_id=self.config.run_id
                    ):
                        session.add(ViewerRow(**values))
                else:
                    self._initialize_creators(session)
                    self._initialize_viewers(session)

        self._write_manifest()
        if self.config.execution_mode == "llm":
            self._run_llm_genesis()

    def _validate_branch_dependencies(self) -> None:
        """Reject silent Persona/runtime drift before any branch writes/calls."""

        expected_code = self.config.continuation_code_sha256
        if expected_code is not None:
            actual_code = continuation_code_sha256()
            if actual_code != expected_code:
                raise RuntimeError(
                    "branch continuation code changed after fork creation: "
                    f"expected {expected_code}, got {actual_code}"
                )

        for field, expected in self.config.dependency_sha256.items():
            path = getattr(self.config, field)
            if path is None:
                raise RuntimeError(
                    f"branch dependency path is missing from config: {field}"
                )
            resolved = Path(path).resolve()
            if not resolved.is_file():
                raise RuntimeError(
                    f"branch dependency file is missing ({field}): {resolved}"
                )
            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
            if digest != expected:
                raise RuntimeError(
                    "frozen run dependency changed after preparation or fork "
                    f"({field}): expected {expected}, got {digest}"
                )

    def run_through(
        self,
        target_day: int | None = None,
        *,
        fail_after_new_sessions: int | None = None,
    ) -> list[DaySummary]:
        target = self.config.days if target_day is None else target_day
        if target < 1 or target > self.config.days:
            raise ValueError(
                f"target day must be within 1..{self.config.days}: {target}"
            )
        self.initialize()
        completed: list[DaySummary] = []
        with self.database.session() as session:
            run = session.get(RunRow, self.config.run_id)
            assert run is not None
            if run.status == "invalidated":
                raise RuntimeError(
                    f"run {run.run_id} is invalidated and cannot be resumed"
                )
            first_day = run.last_completed_day + 1

        for day in range(first_day, target + 1):
            completed.append(
                self.run_day(
                    day,
                    fail_after_new_sessions=fail_after_new_sessions,
                )
            )
            fail_after_new_sessions = None

        if target >= self.config.days:
            with self.database.session() as session, session.begin():
                run = session.get(RunRow, self.config.run_id)
                assert run is not None
                if run.last_completed_day >= self.config.days:
                    run.status = "completed"
        return completed

    def run_day(
        self,
        simulation_day: int,
        *,
        fail_after_new_sessions: int | None = None,
    ) -> DaySummary:
        if simulation_day < 1 or simulation_day > self.config.days:
            raise ValueError(
                "simulation day must be within "
                f"1..{self.config.days}: {simulation_day}"
            )
        self.initialize()
        with self.database.session() as session:
            run = session.get(RunRow, self.config.run_id)
            assert run is not None
            if run.status == "invalidated":
                raise RuntimeError(
                    f"run {run.run_id} is invalidated and cannot be resumed"
                )
        self._ensure_previous_day_completed(simulation_day)
        day = self._ensure_running_day(simulation_day)
        if day.status == "completed":
            return self.day_summary(simulation_day)

        day_started = time.perf_counter()
        self._emit_progress("day_started", simulation_day=simulation_day)
        if (
            self.viewer_service is not None
            and self.config.viewer_early_fraction > 0
            and fail_after_new_sessions is None
        ):
            new_sessions = self._run_pipelined_llm_day(simulation_day)
        else:
            creator_started = time.perf_counter()
            self._emit_progress(
                "creator_stage_started", simulation_day=simulation_day
            )
            self._run_creator_stage(simulation_day)
            self._assert_creator_stage_health(simulation_day)
            self._emit_progress(
                "creator_stage_completed",
                simulation_day=simulation_day,
                elapsed_seconds=round(
                    time.perf_counter() - creator_started, 3
                ),
            )
            viewer_started = time.perf_counter()
            self._emit_progress(
                "viewer_stage_started", simulation_day=simulation_day
            )
            new_sessions = self._run_viewer_stage(
                simulation_day,
                fail_after_new_sessions=fail_after_new_sessions,
            )
            self._emit_progress(
                "viewer_stage_completed",
                simulation_day=simulation_day,
                new_sessions=new_sessions,
                elapsed_seconds=round(
                    time.perf_counter() - viewer_started, 3
                ),
            )
        del new_sessions
        self._finalize_day(simulation_day)

        report_started = time.perf_counter()
        if self.config.canonical:
            from ailiaili.reports.exporter import ReportExporter

            self._emit_progress(
                "report_export_started", simulation_day=simulation_day
            )
            ReportExporter(self.config, self.database).export()
        else:
            # A report is a rebuildable projection and can be nearly as large
            # as a short branch database. Non-canonical experiments keep only
            # facts by default; ``ailiaili report`` materializes the UI on
            # demand when a branch is worth inspecting.
            self._emit_progress(
                "report_export_skipped",
                simulation_day=simulation_day,
                reason="noncanonical_branch",
            )
        summary = self.day_summary(simulation_day)
        self._emit_progress(
            "day_completed",
            simulation_day=simulation_day,
            impressions=summary.impressions,
            clicks=summary.clicks,
            new_follows=summary.new_follows,
            triples=summary.triples,
            report_seconds=(
                round(time.perf_counter() - report_started, 3)
                if self.config.canonical
                else 0.0
            ),
            day_seconds=round(time.perf_counter() - day_started, 3),
        )
        return summary

    def _run_pipelined_llm_day(self, simulation_day: int) -> int:
        """Overlap old-catalogue viewing with today's Creator production.

        Phase assignment depends only on the Run seed, day, and stable actor or
        mirror-panel ID. Provider latency therefore cannot decide who sees a
        newly published video. Both phases use recommendation statistics frozen
        through the previous completed day; this is wall-clock scheduling, not
        an extra same-day feedback loop.
        """

        assert self.viewer_service is not None
        actors = self.viewer_service.actors_for_day(
            run_id=self.config.run_id,
            simulation_day=simulation_day,
        )
        early_actors, late_actors = self._partition_viewer_actors(
            actors, simulation_day=simulation_day
        )
        viewer_started = time.perf_counter()
        creator_started = time.perf_counter()
        self._emit_progress(
            "creator_stage_started", simulation_day=simulation_day
        )
        self._emit_progress(
            "viewer_stage_started",
            simulation_day=simulation_day,
            pipelined=True,
            early_sessions=len(early_actors),
            late_sessions=len(late_actors),
        )
        self._emit_progress(
            "viewer_phase_started",
            simulation_day=simulation_day,
            phase="early",
            sessions=len(early_actors),
            catalog_through_day=simulation_day - 1,
        )

        def creator_job() -> float:
            started = time.perf_counter()
            self._run_creator_stage(simulation_day)
            return time.perf_counter() - started

        def early_viewer_job() -> tuple[int, float]:
            started = time.perf_counter()
            sessions = self._run_llm_viewer_stage(
                simulation_day,
                fail_after_new_sessions=None,
                actors=early_actors,
                catalog_through_day=simulation_day - 1,
                delivery_phase="early",
            )
            return sessions, time.perf_counter() - started

        with ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="ailiaili-day-pipeline"
        ) as executor:
            creator_future = executor.submit(creator_job)
            early_future = executor.submit(early_viewer_job)
            creator_elapsed = creator_future.result()
            early_sessions, early_elapsed = early_future.result()

        self._assert_creator_stage_health(simulation_day)
        self._emit_progress(
            "creator_stage_completed",
            simulation_day=simulation_day,
            elapsed_seconds=round(creator_elapsed, 3),
            overlapped_with="viewer_early",
        )
        self._emit_progress(
            "viewer_phase_completed",
            simulation_day=simulation_day,
            phase="early",
            new_sessions=early_sessions,
            elapsed_seconds=round(early_elapsed, 3),
        )

        late_started = time.perf_counter()
        self._emit_progress(
            "viewer_phase_started",
            simulation_day=simulation_day,
            phase="late",
            sessions=len(late_actors),
            catalog_through_day=simulation_day,
        )
        late_sessions = self._run_llm_viewer_stage(
            simulation_day,
            fail_after_new_sessions=None,
            actors=late_actors,
            catalog_through_day=simulation_day,
            delivery_phase="late",
        )
        self._emit_progress(
            "viewer_phase_completed",
            simulation_day=simulation_day,
            phase="late",
            new_sessions=late_sessions,
            elapsed_seconds=round(time.perf_counter() - late_started, 3),
        )
        total_sessions = early_sessions + late_sessions
        self._emit_progress(
            "viewer_stage_completed",
            simulation_day=simulation_day,
            pipelined=True,
            new_sessions=total_sessions,
            elapsed_seconds=round(time.perf_counter() - viewer_started, 3),
        )
        return total_sessions

    def _partition_viewer_actors(
        self,
        actors: list[ViewerActor],
        *,
        simulation_day: int,
    ) -> tuple[list[ViewerActor], list[ViewerActor]]:
        """Seeded, near-exact phase split that keeps mirror panels together."""

        if not actors:
            return [], []
        grouped: dict[str, list[ViewerActor]] = defaultdict(list)
        for actor in actors:
            grouped[actor.mirror_group_id or actor.actor_id].append(actor)
        ordered_groups = sorted(
            grouped.items(),
            key=lambda item: (
                stable_seed(
                    self.config.seed,
                    "viewer-delivery-phase",
                    simulation_day,
                    item[0],
                ),
                item[0],
            ),
        )
        target = round(len(actors) * self.config.viewer_early_fraction)
        if len(actors) > 1:
            target = min(len(actors) - 1, max(1, target))
        early_group_ids: set[str] = set()
        early_count = 0
        for group_id, members in ordered_groups:
            if early_count >= target:
                break
            early_group_ids.add(group_id)
            early_count += len(members)
        early = [
            actor
            for actor in actors
            if (actor.mirror_group_id or actor.actor_id) in early_group_ids
        ]
        late = [
            actor
            for actor in actors
            if (actor.mirror_group_id or actor.actor_id) not in early_group_ids
        ]
        return early, late

    def _assert_creator_stage_health(self, simulation_day: int) -> None:
        """Stop before costly viewer traffic if creator failures are broad."""

        if self.config.execution_mode != "llm":
            return
        with self.database.session() as session:
            creator_query = select(CreatorRow).where(
                CreatorRow.run_id == self.config.run_id
            )
            if simulation_day > 0:
                creator_query = creator_query.where(
                    CreatorRow.schedule_offset
                    == (simulation_day - 1) % self.config.publish_interval_days
                )
            creators = session.scalars(
                creator_query.order_by(CreatorRow.creator_id)
            ).all()
            turns = session.scalars(
                select(CreatorTurnRow).where(
                    CreatorTurnRow.run_id == self.config.run_id,
                    CreatorTurnRow.simulation_day == simulation_day,
                )
            ).all()

        turn_by_creator = {turn.creator_id: turn for turn in turns}
        if len(turn_by_creator) < len(creators):
            raise RuntimeError(
                "creator stage has tasks that did not reach a terminal state"
            )
        records = [
            (
                "mirror" if creator.mirror_group_id else "population",
                creator.model_alias,
                turn_by_creator[creator.creator_id].status == "succeeded",
            )
            for creator in creators
        ]
        self._assert_group_health(records=records, stage="creator")

    def _assert_viewer_stage_health(
        self, sessions: list[HomepageSessionRow]
    ) -> None:
        if self.config.execution_mode != "llm":
            return
        records = [
            (
                session.cohort_role,
                session.model_alias or "unknown",
                session.status == "succeeded",
            )
            for session in sessions
        ]
        self._assert_group_health(records=records, stage="viewer")

    def _assert_post_click_stage_health(
        self, sessions: list[HomepageSessionRow]
    ) -> None:
        """Do not misread a model-wide consumer-format failure as preference.

        Individual clicked videos may still fail and be skipped. This reuses
        the deliberately tolerant group gate, so only at least three failures
        plus a sub-threshold success rate stop completion of the day.
        """

        if self.config.execution_mode != "llm":
            return
        records: list[tuple[str, str, bool]] = []
        for session in sessions:
            decided_ids = {
                str(decision["video_id"])
                for decision in session.post_click_decisions_json
            }
            for video_id in session.clicked_video_ids_json:
                records.append(
                    (
                        session.cohort_role,
                        session.model_alias or "unknown",
                        str(video_id) in decided_ids,
                    )
                )
        self._assert_group_health(records=records, stage="post-click")

    def _assert_group_health(
        self,
        *,
        records: list[tuple[str, str, bool]],
        stage: str,
    ) -> None:
        """Prevent one failed model/cohort from vanishing inside a good total."""

        if not records:
            return
        overall_successes = sum(success for _, _, success in records)
        overall_rate = overall_successes / len(records)
        if overall_rate < self.config.overall_success_rate:
            raise RuntimeError(
                f"broad {stage} failure: {overall_successes}/{len(records)} "
                "tasks succeeded"
            )

        groups: dict[tuple[str, ...], list[bool]] = defaultdict(list)
        for cohort, model_alias, success in records:
            groups[("model", model_alias)].append(success)
            groups[("cohort_model", cohort, model_alias)].append(success)

        for key, outcomes in sorted(groups.items()):
            successes = sum(outcomes)
            planned = len(outcomes)
            failures = planned - successes
            if (
                # Two tasks are enough to identify a complete small-model
                # outage (including tiny formal Creator cohorts on one day).
                # One or two isolated failures remain ordinary sparse skips.
                (planned >= 2 and successes == 0)
                or (
                    planned >= self.config.cohort_model_gate_min_tasks
                    # With a seven-task creator cohort, two ordinary exhausted
                    # items would otherwise turn 5/7 into a false outage.
                    and failures >= 3
                    and successes / planned
                    < self.config.cohort_model_success_rate
                )
            ):
                raise RuntimeError(
                    f"{stage} health gate failed for {'/'.join(key)}: "
                    f"{successes}/{planned} tasks succeeded"
                )

    def authorize_incomplete_day_retry(self) -> dict[str, int]:
        """Reopen only exhausted technical work in the one unfinished day.

        This is deliberately explicit. Normal sparse failures remain part of a
        completed world; only an operator-facing recovery command may reopen a
        blocked day after the underlying provider/format incident is fixed.
        Successful logical calls and every attempt ledger row remain intact.
        """

        if self.config.execution_mode != "llm":
            raise ValueError("technical retry authorization is only for LLM runs")
        if not self.config.database_path.exists():
            raise RuntimeError("run has not been initialized")
        self.database.create_schema()
        with self.database.session() as session, session.begin():
            run = session.get(RunRow, self.config.run_id)
            if run is None:
                raise RuntimeError("run has not been initialized")
            running_days = session.scalars(
                select(DayRow)
                .where(
                    DayRow.run_id == self.config.run_id,
                    DayRow.status == "running",
                )
                .order_by(DayRow.simulation_day)
            ).all()
            if len(running_days) != 1:
                raise RuntimeError(
                    "expected exactly one unfinished day; found "
                    f"{len(running_days)}"
                )
            simulation_day = running_days[0].simulation_day
            if simulation_day != run.last_completed_day + 1:
                raise RuntimeError(
                    "unfinished day is not immediately after last completed day"
                )

            indeterminate = int(
                session.scalar(
                    select(func.count())
                    .select_from(LLMAttemptRow)
                    .join(
                        LLMCallRow,
                        LLMAttemptRow.call_id == LLMCallRow.call_id,
                    )
                    .where(
                        LLMCallRow.run_id == self.config.run_id,
                        LLMCallRow.simulation_day == simulation_day,
                        LLMAttemptRow.status == "running",
                    )
                )
                or 0
            )
            if indeterminate:
                raise RuntimeError(
                    f"day {simulation_day} has {indeterminate} indeterminate "
                    "dispatched attempts; resolve them manually before retry"
                )

            skipped_turns = session.scalars(
                select(CreatorTurnRow).where(
                    CreatorTurnRow.run_id == self.config.run_id,
                    CreatorTurnRow.simulation_day == simulation_day,
                    CreatorTurnRow.status == "skipped",
                )
            ).all()
            affected_sessions = [
                item
                for item in session.scalars(
                    select(HomepageSessionRow).where(
                        HomepageSessionRow.run_id == self.config.run_id,
                        HomepageSessionRow.simulation_day == simulation_day,
                    )
                ).all()
                if item.status == "skipped" or item.technical_failures_json
            ]
            creator_ids = {turn.creator_id for turn in skipped_turns}
            viewer_ids = {item.viewer_id for item in affected_sessions}

            relevant_calls = session.scalars(
                select(LLMCallRow).where(
                    LLMCallRow.run_id == self.config.run_id,
                    LLMCallRow.simulation_day == simulation_day,
                    LLMCallRow.status == "exhausted",
                )
            ).all()
            authorized_calls = [
                call
                for call in relevant_calls
                if (
                    call.task_type == "creator_turn"
                    and call.agent_id in creator_ids
                )
                or (
                    call.task_type in {"homepage_choice", "post_click_choice"}
                    and call.agent_id in viewer_ids
                )
            ]

            for call in authorized_calls:
                call.status = "retry_authorized"
                call.completed_at = None
            for turn in skipped_turns:
                session.delete(turn)
            if viewer_ids:
                session.execute(
                    delete(FollowRow).where(
                        FollowRow.run_id == self.config.run_id,
                        FollowRow.created_day == simulation_day,
                        FollowRow.viewer_id.in_(viewer_ids),
                    )
                )
            for homepage in affected_sessions:
                session.delete(homepage)

            day = running_days[0]
            day.succeeded_creator_turns = int(
                session.scalar(
                    select(func.count())
                    .select_from(CreatorTurnRow)
                    .where(
                        CreatorTurnRow.run_id == self.config.run_id,
                        CreatorTurnRow.simulation_day == simulation_day,
                        CreatorTurnRow.status == "succeeded",
                    )
                )
                or 0
            )
            day.skipped_creator_turns = 0
            day.exhausted_creator_turns = 0
            day.succeeded_viewer_sessions = 0
            day.skipped_viewer_sessions = 0
            day.exhausted_viewer_sessions = 0

        result = {
            "simulation_day": simulation_day,
            "creator_turns_reset": len(skipped_turns),
            "viewer_sessions_reset": len(affected_sessions),
            "logical_calls_authorized": len(authorized_calls),
        }
        self._emit_progress(
            "technical_retry_authorized",
            simulation_day=simulation_day,
            **{key: value for key, value in result.items() if key != "simulation_day"},
        )
        return result

    def abandon_indeterminate_attempts(self) -> dict[str, int]:
        """Resolve a hard-stop crash after the operator confirms it is over.

        A request can be charged by a provider while the local process dies
        before recording the response. We keep its conservative reservation
        in the ledger, mark that attempt as an explicit technical failure, and
        authorize only its logical call for a fresh retry. This must never be
        used while the original process could still return.
        """

        if self.config.execution_mode != "llm":
            raise ValueError("interrupted-attempt recovery is only for LLM runs")
        if not self.config.database_path.exists():
            raise RuntimeError("run has not been initialized")
        self.database.create_schema()
        with self.database.session() as session, session.begin():
            run = session.get(RunRow, self.config.run_id)
            if run is None:
                raise RuntimeError("run has not been initialized")
            running_days = session.scalars(
                select(DayRow)
                .where(
                    DayRow.run_id == self.config.run_id,
                    DayRow.status == "running",
                )
                .order_by(DayRow.simulation_day)
            ).all()
            if len(running_days) != 1:
                raise RuntimeError(
                    "expected exactly one unfinished day; found "
                    f"{len(running_days)}"
                )
            simulation_day = running_days[0].simulation_day
            if simulation_day != run.last_completed_day + 1:
                raise RuntimeError(
                    "unfinished day is not immediately after last completed day"
                )

            attempts = session.scalars(
                select(LLMAttemptRow)
                .join(LLMCallRow, LLMAttemptRow.call_id == LLMCallRow.call_id)
                .where(
                    LLMCallRow.run_id == self.config.run_id,
                    LLMCallRow.simulation_day == simulation_day,
                    LLMAttemptRow.status == "running",
                )
            ).all()
            if not attempts:
                raise RuntimeError(
                    f"day {simulation_day} has no indeterminate attempts"
                )
            call_ids = {attempt.call_id for attempt in attempts}
            for attempt in attempts:
                attempt.status = "failed"
                attempt.error_code = "operator_abandoned_indeterminate"
                attempt.error_message = (
                    "Original process was confirmed stopped; external charge "
                    "and completion remain unknown. Conservative reservation kept."
                )
                attempt.finished_at = utc_now()
            for call_id in call_ids:
                call = session.get(LLMCallRow, call_id)
                assert call is not None
                call.status = "retry_authorized"
                call.completed_at = None

        result = {
            "simulation_day": simulation_day,
            "attempts_abandoned": len(attempts),
            "logical_calls_authorized": len(call_ids),
        }
        self._emit_progress(
            "indeterminate_attempts_abandoned",
            simulation_day=simulation_day,
            attempts=len(attempts),
            logical_calls=len(call_ids),
        )
        return result

    def day_summary(self, simulation_day: int) -> DaySummary:
        with self.database.session() as session:
            day = session.get(DayRow, (self.config.run_id, simulation_day))
            if day is None:
                raise ValueError(f"simulation day {simulation_day} does not exist")
            platform = session.scalar(
                select(DailyMetricRow).where(
                    DailyMetricRow.run_id == self.config.run_id,
                    DailyMetricRow.simulation_day == simulation_day,
                    DailyMetricRow.entity_type == "platform",
                    DailyMetricRow.entity_id == "platform",
                )
            )
            return DaySummary(
                simulation_day=simulation_day,
                status=day.status,
                planned_creator_turns=day.planned_creator_turns,
                succeeded_creator_turns=day.succeeded_creator_turns,
                skipped_creator_turns=day.skipped_creator_turns,
                planned_viewer_sessions=day.planned_viewer_sessions,
                succeeded_viewer_sessions=day.succeeded_viewer_sessions,
                skipped_viewer_sessions=day.skipped_viewer_sessions,
                impressions=platform.impressions if platform else 0,
                clicks=platform.clicks if platform else 0,
                new_follows=platform.new_follows if platform else 0,
                triples=platform.triples if platform else 0,
            )

    def _run_llm_genesis(self) -> None:
        assert self.creator_service is not None
        with self.database.session() as session:
            day = session.get(DayRow, (self.config.run_id, 0))
            if day is None:
                raise RuntimeError("Day 0 was not initialized")
            if day.status == "completed":
                return
            creator_ids = session.scalars(
                select(CreatorRow.creator_id)
                .where(CreatorRow.run_id == self.config.run_id)
                .order_by(CreatorRow.creator_id)
            ).all()

        started = time.perf_counter()
        self._emit_progress(
            "genesis_started",
            simulation_day=0,
            planned_creator_turns=len(creator_ids),
        )
        self._run_llm_creator_batch(creator_ids, simulation_day=0)

        self._update_creator_counts(0)
        self._assert_creator_stage_health(0)
        with self.database.session() as session, session.begin():
            day = session.get(DayRow, (self.config.run_id, 0))
            assert day is not None
            day.status = "completed"
            day.completed_at = utc_now()
            run = session.get(RunRow, self.config.run_id)
            assert run is not None
            run.last_completed_day = 0
        self._emit_progress(
            "genesis_completed",
            simulation_day=0,
            creator_turns=len(creator_ids),
            elapsed_seconds=round(time.perf_counter() - started, 3),
        )

    def _run_llm_creator_batch(
        self, creator_ids: list[str], *, simulation_day: int
    ) -> None:
        if self.config.max_concurrency == 1 or len(creator_ids) < 2:
            for creator_id in creator_ids:
                self._run_one_llm_creator_turn(
                    creator_id=creator_id,
                    simulation_day=simulation_day,
                )
            return

        with ThreadPoolExecutor(
            max_workers=min(self.config.max_concurrency, len(creator_ids)),
            thread_name_prefix="ailiaili-creator",
        ) as executor:
            futures: list[Future[None]] = [
                executor.submit(
                    self._run_one_llm_creator_turn,
                    creator_id=creator_id,
                    simulation_day=simulation_day,
                )
                for creator_id in creator_ids
            ]
            try:
                for future in as_completed(futures):
                    future.result()
            except Exception:
                for future in futures:
                    future.cancel()
                raise

    def _run_one_llm_creator_turn(
        self, *, creator_id: str, simulation_day: int
    ) -> None:
        assert self.creator_service is not None
        turn_id = f"turn_{creator_id}_d{simulation_day:03d}"
        with self.database.session() as session:
            if session.get(CreatorTurnRow, turn_id) is not None:
                return

        try:
            result = self.creator_service.create_turn(
                run_id=self.config.run_id,
                creator_id=creator_id,
                simulation_day=simulation_day,
            )
        except ModelCallExhausted as error:
            with self.database.session() as session, session.begin():
                if session.get(CreatorTurnRow, turn_id) is None:
                    session.add(
                        CreatorTurnRow(
                            turn_id=turn_id,
                            run_id=self.config.run_id,
                            simulation_day=simulation_day,
                            creator_id=creator_id,
                            status="skipped",
                            creator_note=f"技术性跳过：{error}",
                            memory_json=None,
                            video_id=None,
                        )
                    )
            return
        except (BudgetExceeded, IndeterminateAttempt):
            # A budget stop or an uncertain dispatched request pauses the day;
            # silently replacing either with a skip would hide cost risk.
            raise

        with self.database.session() as session, session.begin():
            if session.get(CreatorTurnRow, turn_id) is not None:
                return
            creator = session.get(CreatorRow, creator_id)
            assert creator is not None
            video_id = self._video_id(creator_id, simulation_day, 1)
            self._add_video(
                session,
                creator_id=creator_id,
                simulation_day=simulation_day,
                sequence=1,
                draft=result.video.model_dump(mode="json"),
                source="llm",
            )
            creator.memory_json = result.updated_memory.model_dump(mode="json")
            session.add(
                CreatorTurnRow(
                    turn_id=turn_id,
                    run_id=self.config.run_id,
                    simulation_day=simulation_day,
                    creator_id=creator_id,
                    status="succeeded",
                    creator_note=result.creator_note,
                    expected_reception=result.expected_reception,
                    prediction_reason=result.prediction_reason,
                    memory_json=result.updated_memory.model_dump(mode="json"),
                    video_id=video_id,
                )
            )

    def _update_creator_counts(self, simulation_day: int) -> None:
        with self.database.session() as session, session.begin():
            day = session.get(DayRow, (self.config.run_id, simulation_day))
            assert day is not None
            succeeded = int(
                session.scalar(
                    select(func.count()).select_from(CreatorTurnRow).where(
                        CreatorTurnRow.run_id == self.config.run_id,
                        CreatorTurnRow.simulation_day == simulation_day,
                        CreatorTurnRow.status == "succeeded",
                    )
                )
                or 0
            )
            skipped = int(
                session.scalar(
                    select(func.count()).select_from(CreatorTurnRow).where(
                        CreatorTurnRow.run_id == self.config.run_id,
                        CreatorTurnRow.simulation_day == simulation_day,
                        CreatorTurnRow.status == "skipped",
                    )
                )
                or 0
            )
            day.succeeded_creator_turns = succeeded
            day.skipped_creator_turns = skipped
            day.exhausted_creator_turns = skipped

    def _initialize_creators(self, session: Session) -> None:
        for index in range(self.config.creator_count):
            blueprint = self._stub_blueprint(index)
            creator_id = f"creator_{index + 1:03d}"
            memory = initial_memory(blueprint)
            session.add(
                CreatorRow(
                    creator_id=creator_id,
                    run_id=self.config.run_id,
                    name=blueprint["name"],
                    category=blueprint["category"],
                    model_alias=blueprint["model_alias"],
                    mirror_group_id=None,
                    mirror_slot=None,
                    schedule_offset=index % self.config.publish_interval_days,
                    persona_json=blueprint,
                    declaration_json=declaration(blueprint),
                    memory_json=memory.model_dump(mode="json"),
                )
            )
            session.add(
                CreatorTurnRow(
                    turn_id=f"turn_{creator_id}_d000",
                    run_id=self.config.run_id,
                    simulation_day=0,
                    creator_id=creator_id,
                    status="succeeded",
                    creator_note="Day 0：先按自己的直觉做两期内容。",
                    memory_json=memory.model_dump(mode="json"),
                    video_id=None,
                )
            )
            for sequence in range(1, self.config.initial_videos_per_creator + 1):
                draft = make_video(
                    blueprint,
                    simulation_day=0,
                    sequence=sequence,
                )
                self._add_video(
                    session,
                    creator_id=creator_id,
                    simulation_day=0,
                    sequence=sequence,
                    draft=draft.model_dump(mode="json"),
                )

    def _stub_blueprint(self, index: int) -> dict[str, Any]:
        if self.config.stub_creators:
            return self.config.stub_creators[index].model_dump(mode="json")
        return creator_blueprint(index)

    def _initialize_viewers(self, session: Session) -> None:
        categories = self.config.stub_viewer_interests or [
            blueprint["category"]
            for blueprint in (self._stub_blueprint(i) for i in range(self.config.creator_count))
        ]
        categories = list(dict.fromkeys(categories))
        for index in range(self.config.viewer_count):
            mirror_group_id: str | None = None
            profile_index = index
            decision_variant = 0
            if index in (0, 1):
                mirror_group_id = "mirror_knowledge"
                profile_index = 0
                decision_variant = index
            elif index in (2, 3):
                mirror_group_id = "mirror_explore"
                profile_index = 3
                decision_variant = index - 2
            viewer_id = f"viewer_{index + 1:04d}"
            session.add(
                ViewerRow(
                    viewer_id=viewer_id,
                    run_id=self.config.run_id,
                    name=f"固定观众 {index + 1:02d}",
                    model_alias=f"stub-viewer-{decision_variant + 1}",
                    mirror_group_id=mirror_group_id,
                    decision_variant=decision_variant,
                    profile_json=viewer_profile(profile_index, categories),
                )
            )

    def _write_manifest(self) -> None:
        self.config.run_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = self.config.run_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(self.config.manifest_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_previous_day_completed(self, simulation_day: int) -> None:
        if simulation_day < 1:
            raise ValueError("run_day only accepts day >= 1")
        with self.database.session() as session:
            previous = session.get(
                DayRow, (self.config.run_id, simulation_day - 1)
            )
            if previous is None or previous.status != "completed":
                raise RuntimeError(
                    f"day {simulation_day - 1} must be completed before day {simulation_day}"
                )

    def _ensure_running_day(self, simulation_day: int) -> DayRow:
        with self.database.session() as session, session.begin():
            day = session.get(DayRow, (self.config.run_id, simulation_day))
            if day is None:
                due_count = session.scalar(
                    select(func.count()).select_from(CreatorRow).where(
                        CreatorRow.run_id == self.config.run_id,
                        CreatorRow.schedule_offset
                        == (simulation_day - 1) % self.config.publish_interval_days,
                    )
                )
                day = DayRow(
                    run_id=self.config.run_id,
                    simulation_day=simulation_day,
                    status="running",
                    planned_creator_turns=int(due_count or 0),
                    planned_viewer_sessions=(
                        self.viewer_service.planned_session_count
                        if self.viewer_service is not None
                        else self.config.viewer_count
                    ),
                )
                session.add(day)
            return day

    def _run_creator_stage(self, simulation_day: int) -> None:
        with self.database.session() as session:
            due_creators = session.scalars(
                select(CreatorRow)
                .where(
                    CreatorRow.run_id == self.config.run_id,
                    CreatorRow.schedule_offset
                    == (simulation_day - 1) % self.config.publish_interval_days,
                )
                .order_by(CreatorRow.creator_id)
            ).all()

        if self.creator_service is not None:
            self._run_llm_creator_batch(
                [creator.creator_id for creator in due_creators],
                simulation_day=simulation_day,
            )
            self._update_creator_counts(simulation_day)
            return

        for creator in due_creators:
            turn_id = f"turn_{creator.creator_id}_d{simulation_day:03d}"
            with self.database.session() as session, session.begin():
                if session.get(CreatorTurnRow, turn_id) is not None:
                    continue
                stored_creator = session.get(CreatorRow, creator.creator_id)
                assert stored_creator is not None
                video_count = session.scalar(
                    select(func.count()).select_from(VideoRow).where(
                        VideoRow.run_id == self.config.run_id,
                        VideoRow.creator_id == creator.creator_id,
                    )
                )
                previous_memory = CreatorMemory.model_validate(
                    stored_creator.memory_json
                )
                result = creator_turn(
                    stored_creator.persona_json,
                    simulation_day=simulation_day,
                    sequence=int(video_count or 0) + 1,
                    previous_memory=previous_memory,
                )
                video_id = self._video_id(
                    creator.creator_id,
                    simulation_day,
                    1,
                )
                self._add_video(
                    session,
                    creator_id=creator.creator_id,
                    simulation_day=simulation_day,
                    sequence=1,
                    draft=result.video.model_dump(mode="json"),
                )
                stored_creator.memory_json = result.memory.model_dump(mode="json")
                session.add(
                    CreatorTurnRow(
                        turn_id=turn_id,
                        run_id=self.config.run_id,
                        simulation_day=simulation_day,
                        creator_id=creator.creator_id,
                        status="succeeded",
                        creator_note=result.note,
                        expected_reception=result.expected_reception,
                        prediction_reason=result.prediction_reason,
                        memory_json=result.memory.model_dump(mode="json"),
                        video_id=video_id,
                    )
                )

        with self.database.session() as session, session.begin():
            day = session.get(DayRow, (self.config.run_id, simulation_day))
            assert day is not None
            day.succeeded_creator_turns = int(
                session.scalar(
                    select(func.count()).select_from(CreatorTurnRow).where(
                        CreatorTurnRow.run_id == self.config.run_id,
                        CreatorTurnRow.simulation_day == simulation_day,
                        CreatorTurnRow.status == "succeeded",
                    )
                )
                or 0
            )

    def _run_viewer_stage(
        self,
        simulation_day: int,
        *,
        fail_after_new_sessions: int | None,
    ) -> int:
        if self.viewer_service is not None:
            return self._run_llm_viewer_stage(
                simulation_day,
                fail_after_new_sessions=fail_after_new_sessions,
            )
        with self.database.session() as session:
            viewers = session.scalars(
                select(ViewerRow)
                .where(ViewerRow.run_id == self.config.run_id)
                .order_by(ViewerRow.viewer_id)
            ).all()
            videos = self._available_videos(session, simulation_day)
            stats_by_video = self._cumulative_video_stats(session, simulation_day - 1)
            historical_clicks = self._historical_clicks(session, simulation_day)
            historical_follows = self._historical_follows(session)
            panel_shown_history = self._panel_shown_history(
                session, simulation_day
            )

        viewers_by_group: dict[str, list[ViewerRow]] = defaultdict(list)
        for viewer in viewers:
            if viewer.mirror_group_id:
                viewers_by_group[viewer.mirror_group_id].append(viewer)

        feed_cache: dict[str, list[dict[str, object]]] = {}
        for group_id, group_viewers in viewers_by_group.items():
            group_profile = group_viewers[0].profile_json
            feed_cache[group_id] = [
                item.model_dump(mode="json")
                for item in self.recommender.sample(
                    videos=videos,
                    stats_by_video=stats_by_video,
                    current_day=simulation_day,
                    excluded_video_ids=set(),
                    interest_categories=self._profile_high_interests(
                        group_profile
                    ),
                    # Reference actions are outputs, never future feed inputs.
                    followed_creator_ids=set(),
                    shown_history=panel_shown_history.get(group_id, {}),
                    seed=stable_seed(
                        self.config.seed,
                        "feed",
                        simulation_day,
                        group_id,
                    ),
                )
            ]

        videos_by_id = {str(video["video_id"]): video for video in videos}
        new_sessions = 0
        for viewer in viewers:
            session_id = f"home_d{simulation_day:03d}_{viewer.viewer_id}"
            with self.database.session() as session:
                if session.get(HomepageSessionRow, session_id) is not None:
                    continue

            if viewer.mirror_group_id:
                candidate_items = feed_cache[viewer.mirror_group_id]
            else:
                candidate_items = [
                    item.model_dump(mode="json")
                    for item in self.recommender.sample(
                        videos=videos,
                        stats_by_video=stats_by_video,
                        current_day=simulation_day,
                        excluded_video_ids=historical_clicks.get(
                            viewer.viewer_id, set()
                        ),
                        interest_categories=self._profile_high_interests(
                            viewer.profile_json
                        ),
                        followed_creator_ids=historical_follows.get(
                            viewer.viewer_id, set()
                        ),
                        seed=stable_seed(
                            self.config.seed,
                            "feed",
                            simulation_day,
                            viewer.viewer_id,
                        ),
                    )
                ]

            candidate_videos = [
                videos_by_id[item["video_id"]] for item in candidate_items
            ]
            clicked_video_ids = choose_clicks(
                base_seed=self.config.seed,
                simulation_day=simulation_day,
                viewer_id=viewer.viewer_id,
                decision_variant=viewer.decision_variant,
                profile=viewer.profile_json,
                candidate_videos=candidate_videos,
                max_clicks=self.config.max_clicks,
            )

            session_seed = stable_seed(
                self.config.seed,
                "viewer-session",
                simulation_day,
                viewer.viewer_id,
            )
            with self.database.session() as session, session.begin():
                followed_creator_ids = set(
                    session.scalars(
                        select(FollowRow.creator_id).where(
                            FollowRow.run_id == self.config.run_id,
                            FollowRow.viewer_id == viewer.viewer_id,
                        )
                    ).all()
                )
                decisions: list[dict[str, Any]] = []
                for video_id in clicked_video_ids:
                    video = videos_by_id[video_id]
                    creator_id = str(video["creator_id"])
                    follow_available = creator_id not in followed_creator_ids
                    decision = decide_post_click(
                        base_seed=self.config.seed,
                        simulation_day=simulation_day,
                        viewer_id=viewer.viewer_id,
                        decision_variant=viewer.decision_variant,
                        profile=viewer.profile_json,
                        video=video,
                        follow_available=follow_available,
                        viewer_note_mode=self.config.viewer_note_mode,
                    )
                    if decision.new_follow:
                        session.add(
                            FollowRow(
                                run_id=self.config.run_id,
                                viewer_id=viewer.viewer_id,
                                creator_id=creator_id,
                                created_day=simulation_day,
                            )
                        )
                        followed_creator_ids.add(creator_id)
                    decisions.append(decision.model_dump(mode="json"))

                session.add(
                    HomepageSessionRow(
                        homepage_session_id=session_id,
                        run_id=self.config.run_id,
                        simulation_day=simulation_day,
                        viewer_id=viewer.viewer_id,
                        mirror_group_id=viewer.mirror_group_id,
                        status="succeeded",
                        viewer_snapshot_json=viewer.profile_json,
                        candidate_items_json=candidate_items,
                        clicked_video_ids_json=clicked_video_ids,
                        post_click_decisions_json=decisions,
                        viewer_note_mode=self.config.viewer_note_mode,
                        seed=session_seed,
                    )
                )

            new_sessions += 1
            if (
                fail_after_new_sessions is not None
                and new_sessions >= fail_after_new_sessions
            ):
                raise InjectedFailure(
                    f"injected interruption after {new_sessions} persisted sessions"
                )

        return new_sessions

    def _run_llm_viewer_stage(
        self,
        simulation_day: int,
        *,
        fail_after_new_sessions: int | None,
        actors: list[ViewerActor] | None = None,
        catalog_through_day: int | None = None,
        delivery_phase: str = "full",
    ) -> int:
        assert self.viewer_service is not None
        if actors is None:
            actors = self.viewer_service.actors_for_day(
                run_id=self.config.run_id,
                simulation_day=simulation_day,
            )
        if not actors:
            return 0
        if catalog_through_day is None:
            catalog_through_day = simulation_day
        if catalog_through_day not in {simulation_day - 1, simulation_day}:
            raise ValueError(
                "viewer catalogue cutoff must be the previous or current day"
            )
        if delivery_phase not in {"full", "early", "late"}:
            raise ValueError(f"unsupported viewer delivery phase: {delivery_phase}")
        with self.database.session() as session:
            videos = self._available_videos(session, catalog_through_day)
            stats_by_video = self._cumulative_video_stats(
                session, simulation_day - 1
            )
            historical_clicks = self._historical_clicks(
                session, simulation_day
            )
            historical_follows = self._historical_follows(session)
            panel_shown_history = self._panel_shown_history(
                session, simulation_day
            )
            existing_session_ids = set(
                session.scalars(
                    select(HomepageSessionRow.homepage_session_id).where(
                        HomepageSessionRow.run_id == self.config.run_id,
                        HomepageSessionRow.simulation_day == simulation_day,
                    )
                ).all()
            )
        videos_by_id = {str(video["video_id"]): video for video in videos}

        if fail_after_new_sessions is not None or self.config.max_concurrency == 1:
            new_sessions = 0
            for actor in actors:
                if self._run_one_llm_viewer_session(
                    simulation_day=simulation_day,
                    actor=actor,
                    videos=videos,
                    videos_by_id=videos_by_id,
                    stats_by_video=stats_by_video,
                    historical_clicks=historical_clicks,
                    historical_follows=historical_follows,
                    panel_shown_history=panel_shown_history,
                    delivery_phase=delivery_phase,
                    catalog_through_day=catalog_through_day,
                ):
                    new_sessions += 1
                    self._maybe_inject_failure(
                        new_sessions, fail_after_new_sessions
                    )
            return new_sessions

        pending_actors = [
            actor
            for actor in actors
            if f"home_d{simulation_day:03d}_{actor.actor_id}"
            not in existing_session_ids
        ]
        if not pending_actors:
            return 0

        # Reference viewers in one mirror panel must receive byte-for-byte the
        # same ordered feed.  Build it once instead of repeating recommendation
        # work for every model member.
        mirror_feed_cache: dict[str, list[dict[str, Any]]] = {}
        work_items: list[_ViewerSessionWork] = []
        for actor in pending_actors:
            if actor.mirror_group_id and actor.mirror_group_id in mirror_feed_cache:
                candidate_items = list(mirror_feed_cache[actor.mirror_group_id])
            else:
                candidate_items = [
                    item.model_dump(mode="json")
                    for item in self.recommender.sample(
                        videos=videos,
                        stats_by_video=stats_by_video,
                        current_day=simulation_day,
                        excluded_video_ids=(
                            set()
                            if actor.cohort_role == "reference"
                            else historical_clicks.get(actor.actor_id, set())
                        ),
                        interest_categories=set(actor.profile.high_interests),
                        followed_creator_ids=(
                            set()
                            if actor.cohort_role == "reference"
                            else historical_follows.get(actor.actor_id, set())
                        ),
                        shown_history=(
                            panel_shown_history.get(actor.mirror_group_id, {})
                            if actor.mirror_group_id
                            else None
                        ),
                        seed=stable_seed(
                            self.config.seed,
                            "feed",
                            simulation_day,
                            actor.mirror_group_id or actor.actor_id,
                        ),
                    )
                ]
                if actor.mirror_group_id:
                    mirror_feed_cache[actor.mirror_group_id] = list(candidate_items)

            context = self.viewer_service.session_context(
                actor=actor,
                simulation_day=simulation_day,
            )
            work_items.append(
                _ViewerSessionWork(
                    actor=actor,
                    session_id=f"home_d{simulation_day:03d}_{actor.actor_id}",
                    candidate_items=candidate_items,
                    candidate_videos=[
                        videos_by_id[str(item["video_id"])]
                        for item in candidate_items
                    ],
                    context=context,
                    note_mode=self.viewer_service.note_mode(
                        actor=actor,
                        simulation_day=simulation_day,
                    ),
                    session_seed=stable_seed(
                        self.config.seed,
                        "viewer-session",
                        simulation_day,
                        actor.mirror_group_id or actor.actor_id,
                    ),
                    followed_creator_ids=(
                        set(historical_follows.get(actor.actor_id, set()))
                        if actor.kind == "fixed"
                        else set()
                    ),
                    delivery_phase=delivery_phase,
                    catalog_through_day=catalog_through_day,
                )
            )

        # First fan out every homepage choice, then fan out every admitted
        # clicked video in one global post-click queue. The three clicked videos
        # are independent model judgments; no rank waits for another rank.
        # Platform follow de-duplication is applied deterministically afterwards.
        worker_count = min(
            self.config.max_concurrency,
            len(work_items) * max(1, self.config.max_clicks),
        )
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="ailiaili-viewer",
        ) as executor:
            homepage_tail_signal = TailDiscardSignal()
            homepage_future_work: dict[Future[None], _ViewerSessionWork] = {
                executor.submit(
                    self._run_llm_homepage_choice,
                    simulation_day=simulation_day,
                    work=work,
                    tail_discard_signal=(
                        None
                        if work.actor.cohort_role == "reference"
                        else homepage_tail_signal
                    ),
                ): work
                for work in work_items
            }
            homepage_futures = list(homepage_future_work)
            homepage_started = time.perf_counter()
            self._emit_progress(
                "homepage_choices_started",
                simulation_day=simulation_day,
                sessions=len(homepage_futures),
                workers=worker_count,
            )
            self._wait_for_viewer_stage(
                homepage_future_work,
                simulation_day=simulation_day,
                stage="homepage",
                tail_discard_signal=homepage_tail_signal,
            )
            self._emit_progress(
                "homepage_choices_completed",
                simulation_day=simulation_day,
                sessions=len(homepage_futures),
                clicked_videos=sum(
                    len(work.clicked_video_ids) for work in work_items
                ),
                skipped_sessions=sum(
                    work.status != "succeeded" for work in work_items
                ),
                elapsed_seconds=round(time.perf_counter() - homepage_started, 3),
            )

            post_click_started = time.perf_counter()
            post_click_tail_signal = TailDiscardSignal()
            post_click_future_work: dict[Future[None], _ViewerSessionWork] = {
                executor.submit(
                    self._run_llm_post_click_choice,
                    simulation_day=simulation_day,
                    work=work,
                    click_index=click_index,
                    videos_by_id=videos_by_id,
                    tail_discard_signal=(
                        None
                        if work.actor.cohort_role == "reference"
                        else post_click_tail_signal
                    ),
                ): work
                for work in work_items
                if work.status == "succeeded"
                for click_index in range(len(work.clicked_video_ids))
            }
            post_click_futures = list(post_click_future_work)
            self._wait_for_viewer_stage(
                post_click_future_work,
                simulation_day=simulation_day,
                stage="post_click",
                tail_discard_signal=post_click_tail_signal,
            )
            self._emit_progress(
                "post_click_choices_completed",
                simulation_day=simulation_day,
                decisions=len(post_click_futures),
                elapsed_seconds=round(
                    time.perf_counter() - post_click_started, 3
                ),
            )

        for work in work_items:
            self._finalize_post_click_results(work=work)

        # Network workers never write session/follow state.  Commit in stable
        # actor order after all model decisions, avoiding hundreds of SQLite
        # writers while preserving the existing resumability contract.
        for work in work_items:
            self._persist_homepage_session(
                session_id=work.session_id,
                simulation_day=simulation_day,
                actor=work.actor,
                context=self._stored_session_context(work),
                status=work.status,
                candidate_items=work.candidate_items,
                clicked_video_ids=work.clicked_video_ids,
                decisions=work.decisions,
                technical_failures=work.technical_failures,
                note_mode=work.note_mode,
                session_seed=work.session_seed,
                new_follow_creator_ids=work.new_follow_creator_ids,
            )
        self._emit_progress(
            "viewer_sessions_persisted",
            simulation_day=simulation_day,
            sessions=len(work_items),
        )
        return len(work_items)

    @staticmethod
    def _stored_session_context(work: _ViewerSessionWork) -> dict[str, Any]:
        """Add replay-only delivery facts after the model has made its choice."""

        return {
            **work.context.model_dump(mode="json"),
            "delivery_phase": work.delivery_phase,
            "catalog_through_day": work.catalog_through_day,
        }

    @staticmethod
    def _wait_for_stage(futures: list[Future[None]]) -> None:
        try:
            for future in as_completed(futures):
                future.result()
        except Exception:
            for future in futures:
                future.cancel()
            raise

    def _wait_for_viewer_stage(
        self,
        future_work: dict[Future[None], _ViewerSessionWork],
        *,
        simulation_day: int,
        stage: str,
        tail_discard_signal: TailDiscardSignal,
    ) -> None:
        """Wait normally, but stop retrying an already negligible long tail.

        At the first ten-minute gate (versioned in SimulationConfig), at least
        99% of the stage must already be terminal. Otherwise the same test is
        repeated once per minute. Reference-panel tasks are never signalled:
        losing a few matched observations can bias the comparison much more
        than losing the same number from the ordinary population.

        A running Python thread cannot be killed safely. Signalled calls stop
        before dispatch when possible and never retry; a request already in
        HTTP is allowed to reach its existing provider timeout so the attempt
        and cost ledger remain determinate.
        """

        if not future_work:
            return
        if self.config.viewer_tail_gate_seconds is None:
            self._wait_for_stage(list(future_work))
            return
        pending = set(future_work)
        total = len(pending)
        started = time.perf_counter()
        next_check = self.config.viewer_tail_gate_seconds
        discard_requested = False

        try:
            while pending:
                if discard_requested:
                    completed, pending = wait(pending)
                else:
                    remaining = max(
                        0.0,
                        next_check - (time.perf_counter() - started),
                    )
                    completed, pending = wait(pending, timeout=remaining)

                for future in completed:
                    future.result()
                if not pending or discard_requested:
                    continue

                elapsed = time.perf_counter() - started
                if elapsed + 1e-6 < next_check:
                    continue
                terminal = total - len(pending)
                completion_ratio = terminal / total
                ordinary_pending = sum(
                    future_work[future].actor.cohort_role != "reference"
                    for future in pending
                )
                reference_pending = len(pending) - ordinary_pending
                self._emit_progress(
                    "viewer_tail_checked",
                    simulation_day=simulation_day,
                    stage=stage,
                    elapsed_seconds=round(elapsed, 3),
                    terminal=terminal,
                    total=total,
                    completion_ratio=round(completion_ratio, 6),
                    ordinary_pending=ordinary_pending,
                    reference_pending=reference_pending,
                )
                if (
                    self.config.viewer_tail_completion_ratio is not None
                    and
                    completion_ratio
                    >= self.config.viewer_tail_completion_ratio
                    and ordinary_pending > 0
                ):
                    tail_discard_signal.request()
                    discard_requested = True
                    self._emit_progress(
                        "viewer_tail_discard_requested",
                        simulation_day=simulation_day,
                        stage=stage,
                        ordinary_tasks=ordinary_pending,
                        reference_tasks_protected=reference_pending,
                    )
                    continue
                assert self.config.viewer_tail_recheck_seconds is not None
                next_check = elapsed + self.config.viewer_tail_recheck_seconds
        except Exception:
            tail_discard_signal.request()
            for future in pending:
                future.cancel()
            raise

    def _run_llm_homepage_choice(
        self,
        *,
        simulation_day: int,
        work: _ViewerSessionWork,
        tail_discard_signal: TailDiscardSignal | None = None,
    ) -> None:
        assert self.viewer_service is not None
        if not work.candidate_videos:
            work.status = "succeeded"
            return
        try:
            homepage = self.viewer_service.choose_homepage(
                run_id=self.config.run_id,
                simulation_day=simulation_day,
                actor=work.actor,
                context=work.context,
                candidate_videos=work.candidate_videos,
                tail_discard_signal=tail_discard_signal,
            )
            work.clicked_video_ids = list(homepage.clicked_video_ids)
            work.status = "succeeded"
        except ModelCallExhausted as error:
            work.status = "skipped"
            work.technical_failures.append(
                {"stage": "homepage", "error": str(error)}
            )
        except TailDiscardRequested:
            work.status = "skipped"
            work.technical_failures.append(
                {"stage": "homepage", "error": "operational_tail_discard"}
            )
        except (BudgetExceeded, IndeterminateAttempt):
            raise

    def _run_llm_post_click_choice(
        self,
        *,
        simulation_day: int,
        work: _ViewerSessionWork,
        click_index: int,
        videos_by_id: dict[str, dict[str, object]],
        tail_discard_signal: TailDiscardSignal | None = None,
    ) -> None:
        assert self.viewer_service is not None
        video_id = work.clicked_video_ids[click_index]
        video = videos_by_id[video_id]
        try:
            assessment = self.viewer_service.choose_post_click(
                run_id=self.config.run_id,
                simulation_day=simulation_day,
                actor=work.actor,
                clicked_video=video,
                note_mode=work.note_mode,
                tail_discard_signal=tail_discard_signal,
            )
        except ModelCallExhausted as error:
            work.technical_failures.append(
                {
                    "stage": "post_click",
                    "video_id": video_id,
                    "error": str(error),
                }
            )
            return
        except TailDiscardRequested:
            work.technical_failures.append(
                {
                    "stage": "post_click",
                    "video_id": video_id,
                    "error": "operational_tail_discard",
                }
            )
            return
        except (BudgetExceeded, IndeterminateAttempt):
            raise

        work.post_click_results[click_index] = assessment.model_dump(mode="json")

    def _finalize_post_click_results(self, *, work: _ViewerSessionWork) -> None:
        """Convert qualitative reactions to actions in stable click order."""

        for click_index, video_id in enumerate(work.clicked_video_ids):
            raw = work.post_click_results.get(click_index)
            if raw is None:
                continue
            creator_id = str(
                work.candidate_videos[
                    next(
                        index
                        for index, item in enumerate(work.candidate_videos)
                        if str(item["video_id"]) == video_id
                    )
                ]["creator_id"]
            )
            follow_available = creator_id not in work.followed_creator_ids
            action_identity = work.actor.mirror_group_id or work.actor.actor_id
            activation = activate_post_click(
                base_seed=self.config.seed,
                actor_identity=action_identity,
                video_id=video_id,
                channel_pull=str(raw["channel_pull"]),
                episode_impact=str(raw["episode_impact"]),
                follow_open_to_more=(
                    self.config.follow_activation_open_to_more
                ),
                follow_actively_seek=(
                    self.config.follow_activation_actively_seek
                ),
                triple_notable=self.config.triple_activation_notable,
                triple_exceptional=self.config.triple_activation_exceptional,
            )
            new_follow = follow_available and activation.follow_intent
            if new_follow:
                work.followed_creator_ids.add(creator_id)
                if work.actor.kind == "fixed":
                    work.new_follow_creator_ids.append(creator_id)
            work.decisions.append(
                PostClickDecision(
                    video_id=video_id,
                    follow_available=follow_available,
                    # ``follow_intent`` is retained for schema compatibility,
                    # but is now an observable platform action rather than a
                    # hidden model score.  An unavailable follow button cannot
                    # produce an attempted follow in the event record.
                    follow_intent=new_follow,
                    new_follow=new_follow,
                    triple=activation.triple,
                    channel_pull=str(raw["channel_pull"]),
                    episode_impact=str(raw["episode_impact"]),
                    follow_activation_probability=(
                        activation.follow_probability if follow_available else 0.0
                    ),
                    triple_activation_probability=activation.triple_probability,
                    click_note=raw.get("click_note"),
                    post_click_note=raw.get("post_click_note"),
                ).model_dump(mode="json")
            )

    def _run_one_llm_viewer_session(
        self,
        *,
        simulation_day: int,
        actor: ViewerActor,
        videos: list[dict[str, object]],
        videos_by_id: dict[str, dict[str, object]],
        stats_by_video: dict[str, VideoStats],
        historical_clicks: dict[str, set[str]],
        historical_follows: dict[str, set[str]],
        panel_shown_history: dict[str, dict[str, int]],
        delivery_phase: str = "full",
        catalog_through_day: int | None = None,
    ) -> bool:
        assert self.viewer_service is not None
        session_id = f"home_d{simulation_day:03d}_{actor.actor_id}"
        with self.database.session() as session:
            if session.get(HomepageSessionRow, session_id) is not None:
                return False

        candidate_items = [
            item.model_dump(mode="json")
            for item in self.recommender.sample(
                videos=videos,
                stats_by_video=stats_by_video,
                current_day=simulation_day,
                excluded_video_ids=(
                    set()
                    if actor.cohort_role == "reference"
                    else historical_clicks.get(actor.actor_id, set())
                ),
                interest_categories=set(actor.profile.high_interests),
                followed_creator_ids=(
                    set()
                    if actor.cohort_role == "reference"
                    else historical_follows.get(actor.actor_id, set())
                ),
                shown_history=(
                    panel_shown_history.get(actor.mirror_group_id, {})
                    if actor.mirror_group_id
                    else None
                ),
                seed=stable_seed(
                    self.config.seed,
                    "feed",
                    simulation_day,
                    actor.mirror_group_id or actor.actor_id,
                ),
            )
        ]
        candidate_videos = [
            videos_by_id[str(item["video_id"])] for item in candidate_items
        ]
        context = self.viewer_service.session_context(
            actor=actor,
            simulation_day=simulation_day,
        )
        note_mode = self.viewer_service.note_mode(
            actor=actor,
            simulation_day=simulation_day,
        )
        session_seed = stable_seed(
            self.config.seed,
            "viewer-session",
            simulation_day,
            actor.mirror_group_id or actor.actor_id,
        )
        stored_context = {
            **context.model_dump(mode="json"),
            "delivery_phase": delivery_phase,
            "catalog_through_day": (
                simulation_day
                if catalog_through_day is None
                else catalog_through_day
            ),
        }

        try:
            if candidate_videos:
                homepage = self.viewer_service.choose_homepage(
                    run_id=self.config.run_id,
                    simulation_day=simulation_day,
                    actor=actor,
                    context=context,
                    candidate_videos=candidate_videos,
                )
                clicked_video_ids = homepage.clicked_video_ids
            else:
                clicked_video_ids = []
        except ModelCallExhausted as error:
            self._persist_homepage_session(
                session_id=session_id,
                simulation_day=simulation_day,
                actor=actor,
                context=stored_context,
                status="skipped",
                candidate_items=candidate_items,
                clicked_video_ids=[],
                decisions=[],
                technical_failures=[
                    {"stage": "homepage", "error": str(error)}
                ],
                note_mode=note_mode,
                session_seed=session_seed,
                new_follow_creator_ids=[],
            )
            return True
        except (BudgetExceeded, IndeterminateAttempt):
            raise

        with self.database.session() as session:
            followed_creator_ids = (
                set(
                    session.scalars(
                        select(FollowRow.creator_id).where(
                            FollowRow.run_id == self.config.run_id,
                            FollowRow.viewer_id == actor.actor_id,
                        )
                    ).all()
                )
                if actor.kind == "fixed"
                else set()
            )

        decisions: list[dict[str, Any]] = []
        technical_failures: list[dict[str, Any]] = []
        new_follow_creator_ids: list[str] = []
        completed_assessments: list[
            tuple[str, dict[str, object], dict[str, Any]]
        ] = []
        for video_id in clicked_video_ids:
            video = videos_by_id[video_id]
            try:
                assessment = self.viewer_service.choose_post_click(
                    run_id=self.config.run_id,
                    simulation_day=simulation_day,
                    actor=actor,
                    clicked_video=video,
                    note_mode=note_mode,
                )
            except ModelCallExhausted as error:
                technical_failures.append(
                    {
                        "stage": "post_click",
                        "video_id": video_id,
                        "error": str(error),
                    }
                )
                continue
            except (BudgetExceeded, IndeterminateAttempt):
                raise

            completed_assessments.append(
                (video_id, video, assessment.model_dump(mode="json"))
            )

        for video_id, video, raw in completed_assessments:
            creator_id = str(video["creator_id"])
            follow_available = creator_id not in followed_creator_ids
            action_identity = actor.mirror_group_id or actor.actor_id
            activation = activate_post_click(
                base_seed=self.config.seed,
                actor_identity=action_identity,
                video_id=video_id,
                channel_pull=str(raw["channel_pull"]),
                episode_impact=str(raw["episode_impact"]),
                follow_open_to_more=(
                    self.config.follow_activation_open_to_more
                ),
                follow_actively_seek=(
                    self.config.follow_activation_actively_seek
                ),
                triple_notable=self.config.triple_activation_notable,
                triple_exceptional=self.config.triple_activation_exceptional,
            )
            new_follow = follow_available and activation.follow_intent
            if new_follow:
                followed_creator_ids.add(creator_id)
                if actor.kind == "fixed":
                    new_follow_creator_ids.append(creator_id)
            decisions.append(
                PostClickDecision(
                    video_id=video_id,
                    follow_available=follow_available,
                    follow_intent=new_follow,
                    new_follow=new_follow,
                    triple=activation.triple,
                    channel_pull=str(raw["channel_pull"]),
                    episode_impact=str(raw["episode_impact"]),
                    follow_activation_probability=(
                        activation.follow_probability if follow_available else 0.0
                    ),
                    triple_activation_probability=activation.triple_probability,
                    click_note=raw.get("click_note"),
                    post_click_note=raw.get("post_click_note"),
                ).model_dump(mode="json")
            )

        self._persist_homepage_session(
            session_id=session_id,
            simulation_day=simulation_day,
            actor=actor,
            context=stored_context,
            status="succeeded",
            candidate_items=candidate_items,
            clicked_video_ids=clicked_video_ids,
            decisions=decisions,
            technical_failures=technical_failures,
            note_mode=note_mode,
            session_seed=session_seed,
            new_follow_creator_ids=new_follow_creator_ids,
        )
        return True

    def _persist_homepage_session(
        self,
        *,
        session_id: str,
        simulation_day: int,
        actor: ViewerActor,
        context: dict[str, Any],
        status: str,
        candidate_items: list[dict[str, Any]],
        clicked_video_ids: list[str],
        decisions: list[dict[str, Any]],
        technical_failures: list[dict[str, Any]],
        note_mode: str,
        session_seed: int,
        new_follow_creator_ids: list[str],
    ) -> None:
        with self.database.session() as session, session.begin():
            if session.get(HomepageSessionRow, session_id) is not None:
                return
            for creator_id in new_follow_creator_ids:
                session.add(
                    FollowRow(
                        run_id=self.config.run_id,
                        viewer_id=actor.actor_id,
                        creator_id=creator_id,
                        created_day=simulation_day,
                    )
                )
            viewer_snapshot = actor.profile.model_dump(mode="json")
            if self.config.evidence_mode == "compact":
                # Persistent profiles already live once in ViewerRow. Random
                # visitors keep a compact identity sufficient for the report
                # and audit strata instead of repeating the full persona text.
                viewer_snapshot = {
                    "storage": "compact-homepage-v1",
                    "viewer_id": actor.actor_id,
                    "main_need": actor.profile.main_need,
                    "secondary_need": actor.profile.secondary_need,
                    "interest_constellation": (
                        actor.profile.interest_constellation
                    ),
                    "high_interests": actor.profile.high_interests,
                    "medium_interests": actor.profile.medium_interests,
                    "selectivity": actor.profile.selectivity,
                }
            session.add(
                HomepageSessionRow(
                    homepage_session_id=session_id,
                    run_id=self.config.run_id,
                    simulation_day=simulation_day,
                    viewer_id=actor.actor_id,
                    viewer_kind=actor.kind,
                    cohort_role=actor.cohort_role,
                    model_alias=actor.model_alias,
                    mirror_group_id=actor.mirror_group_id,
                    status=status,
                    viewer_snapshot_json=viewer_snapshot,
                    session_context_json=context,
                    candidate_items_json=candidate_items,
                    clicked_video_ids_json=clicked_video_ids,
                    post_click_decisions_json=decisions,
                    technical_failures_json=technical_failures,
                    viewer_note_mode=note_mode,
                    seed=session_seed,
                )
            )

    @staticmethod
    def _maybe_inject_failure(
        new_sessions: int, fail_after_new_sessions: int | None
    ) -> None:
        if (
            fail_after_new_sessions is not None
            and new_sessions >= fail_after_new_sessions
        ):
            raise InjectedFailure(
                f"injected interruption after {new_sessions} persisted sessions"
            )

    def _finalize_day(self, simulation_day: int) -> None:
        with self.database.session() as session, session.begin():
            day = session.get(DayRow, (self.config.run_id, simulation_day))
            assert day is not None
            terminal_sessions = session.scalars(
                select(HomepageSessionRow).where(
                    HomepageSessionRow.run_id == self.config.run_id,
                    HomepageSessionRow.simulation_day == simulation_day,
                    HomepageSessionRow.status.in_(["succeeded", "skipped"]),
                )
            ).all()
            sessions = [
                item for item in terminal_sessions if item.status == "succeeded"
            ]
            day.succeeded_viewer_sessions = len(sessions)
            day.skipped_viewer_sessions = sum(
                item.status == "skipped" for item in terminal_sessions
            )
            day.exhausted_viewer_sessions = day.skipped_viewer_sessions
            if len(terminal_sessions) < day.planned_viewer_sessions:
                raise RuntimeError(
                    "not all viewer sessions reached a terminal state"
                )
            if self.config.execution_mode == "stub" and day.skipped_viewer_sessions:
                raise RuntimeError("stub slice expects every viewer session to succeed")
            self._assert_viewer_stage_health(terminal_sessions)
            self._assert_post_click_stage_health(sessions)

            session.execute(
                delete(DailyMetricRow).where(
                    DailyMetricRow.run_id == self.config.run_id,
                    DailyMetricRow.simulation_day == simulation_day,
                )
            )
            metrics, viewer_model_sessions = self._aggregate_sessions(
                session, sessions
            )
            for (entity_type, entity_id), stats in metrics.items():
                session.add(
                    DailyMetricRow(
                        run_id=self.config.run_id,
                        simulation_day=simulation_day,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        score=(
                            float(viewer_model_sessions[entity_id])
                            if entity_type == "viewer_model"
                            else None
                        ),
                        **stats.model_dump(),
                    )
                )

            day.status = "completed"
            day.completed_at = utc_now()
            run = session.get(RunRow, self.config.run_id)
            assert run is not None
            run.last_completed_day = simulation_day

    def _aggregate_sessions(
        self,
        session: Session,
        sessions: list[HomepageSessionRow],
    ) -> tuple[
        dict[tuple[str, str], VideoStats],
        dict[str, int],
    ]:
        videos = session.scalars(
            select(VideoRow).where(VideoRow.run_id == self.config.run_id)
        ).all()
        video_to_creator = {video.video_id: video.creator_id for video in videos}
        video_to_category = {video.video_id: video.category for video in videos}
        creator_models = {
            creator.creator_id: creator.model_alias
            for creator in session.scalars(
                select(CreatorRow).where(
                    CreatorRow.run_id == self.config.run_id
                )
            ).all()
        }
        metrics: dict[tuple[str, str], VideoStats] = defaultdict(VideoStats)
        viewer_model_sessions: dict[str, int] = defaultdict(int)

        for homepage in sessions:
            viewer_model_id = metric_dimension_id(
                homepage.cohort_role or "population",
                homepage.model_alias or "unknown",
            )
            viewer_model_sessions[viewer_model_id] += 1
            clicked = set(homepage.clicked_video_ids_json)
            decisions = {
                decision["video_id"]: decision
                for decision in homepage.post_click_decisions_json
            }
            candidate_ids = [
                item["video_id"] for item in homepage.candidate_items_json
            ]
            for video_id in candidate_ids:
                creator_model = creator_models[video_to_creator[video_id]]
                affinity_id = metric_dimension_id(
                    creator_model,
                    homepage.model_alias or "unknown",
                )
                analytical_keys = (
                    ("viewer_model", viewer_model_id),
                    ("model_affinity", affinity_id),
                )
                self._increment_metric(
                    metrics,
                    video_id=video_id,
                    creator_id=video_to_creator[video_id],
                    category=video_to_category[video_id],
                    field="impressions",
                    analytical_keys=analytical_keys,
                )
                if video_id not in clicked:
                    continue
                self._increment_metric(
                    metrics,
                    video_id=video_id,
                    creator_id=video_to_creator[video_id],
                    category=video_to_category[video_id],
                    field="clicks",
                    analytical_keys=analytical_keys,
                )
                decision = decisions.get(video_id)
                # A homepage click is still a real click when the isolated
                # post-click task later exhausts.  It simply contributes no
                # completed follow/triple opportunity.
                if decision is None:
                    continue
                self._increment_metric(
                    metrics,
                    video_id=video_id,
                    creator_id=video_to_creator[video_id],
                    category=video_to_category[video_id],
                    field="completed_post_click_opportunities",
                    analytical_keys=analytical_keys,
                )
                if decision["follow_available"]:
                    self._increment_metric(
                        metrics,
                        video_id=video_id,
                        creator_id=video_to_creator[video_id],
                        category=video_to_category[video_id],
                        field="completed_follow_opportunities",
                        analytical_keys=analytical_keys,
                    )
                if decision["new_follow"]:
                    self._increment_metric(
                        metrics,
                        video_id=video_id,
                        creator_id=video_to_creator[video_id],
                        category=video_to_category[video_id],
                        field="new_follows",
                        analytical_keys=analytical_keys,
                    )
                if decision["triple"]:
                    self._increment_metric(
                        metrics,
                        video_id=video_id,
                        creator_id=video_to_creator[video_id],
                        category=video_to_category[video_id],
                        field="triples",
                        analytical_keys=analytical_keys,
                    )
        return metrics, dict(viewer_model_sessions)

    @staticmethod
    def _increment_metric(
        metrics: dict[tuple[str, str], VideoStats],
        *,
        video_id: str,
        creator_id: str,
        category: str,
        field: str,
        analytical_keys: tuple[tuple[str, str], ...] = (),
    ) -> None:
        for key in (
            ("video", video_id),
            ("creator", creator_id),
            ("category", category),
            ("platform", "platform"),
            *analytical_keys,
        ):
            stats = metrics[key]
            setattr(stats, field, getattr(stats, field) + 1)

    def _available_videos(
        self, session: Session, simulation_day: int
    ) -> list[dict[str, object]]:
        creators = {
            row.creator_id: row
            for row in session.scalars(
                select(CreatorRow).where(CreatorRow.run_id == self.config.run_id)
            ).all()
        }
        rows = session.scalars(
            select(VideoRow)
            .where(
                VideoRow.run_id == self.config.run_id,
                VideoRow.published_day <= simulation_day,
            )
            .order_by(VideoRow.video_id)
        ).all()
        return [
            {
                "video_id": row.video_id,
                "creator_id": row.creator_id,
                "creator_name": creators[row.creator_id].name,
                "creator_bio": creators[row.creator_id].declaration_json.get(
                    "我想做的账号",
                    creators[row.creator_id].persona_json.get("goal", ""),
                ),
                "published_day": row.published_day,
                "category": row.category,
                "grounding_mode": row.grounding_mode,
                "title": row.title,
                "cover_description": row.cover_description,
                "premise": row.premise,
                "outline": row.outline_json,
            }
            for row in rows
        ]

    def _cumulative_video_stats(
        self, session: Session, through_day: int
    ) -> dict[str, VideoStats]:
        rows = session.scalars(
            select(DailyMetricRow).where(
                DailyMetricRow.run_id == self.config.run_id,
                DailyMetricRow.simulation_day <= through_day,
                DailyMetricRow.entity_type == "video",
            )
        ).all()
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

    def _historical_clicks(
        self, session: Session, before_day: int
    ) -> dict[str, set[str]]:
        inherited = session.scalars(
            select(ViewerClickHistoryRow).where(
                ViewerClickHistoryRow.run_id == self.config.run_id,
                ViewerClickHistoryRow.last_clicked_day < before_day,
            )
        ).all()
        rows = session.scalars(
            select(HomepageSessionRow).where(
                HomepageSessionRow.run_id == self.config.run_id,
                HomepageSessionRow.simulation_day < before_day,
                HomepageSessionRow.status == "succeeded",
            )
        ).all()
        result: dict[str, set[str]] = defaultdict(set)
        for row in inherited:
            result[row.viewer_id].add(row.video_id)
        for row in rows:
            result[row.viewer_id].update(row.clicked_video_ids_json)
        return result

    def _historical_follows(self, session: Session) -> dict[str, set[str]]:
        rows = session.scalars(
            select(FollowRow).where(FollowRow.run_id == self.config.run_id)
        ).all()
        result: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            result[row.viewer_id].add(row.creator_id)
        return result

    def _panel_shown_history(
        self, session: Session, before_day: int
    ) -> dict[str, dict[str, int]]:
        inherited = session.scalars(
            select(PanelShownHistoryRow).where(
                PanelShownHistoryRow.run_id == self.config.run_id,
                PanelShownHistoryRow.last_shown_day < before_day,
            )
        ).all()
        rows = session.scalars(
            select(HomepageSessionRow).where(
                HomepageSessionRow.run_id == self.config.run_id,
                HomepageSessionRow.simulation_day < before_day,
                HomepageSessionRow.mirror_group_id.is_not(None),
            )
        ).all()
        result: dict[str, dict[str, int]] = defaultdict(dict)
        for row in inherited:
            result[row.mirror_group_id][row.video_id] = row.last_shown_day
        for row in rows:
            assert row.mirror_group_id is not None
            for item in row.candidate_items_json:
                video_id = str(item["video_id"])
                previous = result[row.mirror_group_id].get(video_id, -1)
                result[row.mirror_group_id][video_id] = max(
                    previous, row.simulation_day
                )
        return {group_id: dict(history) for group_id, history in result.items()}

    @staticmethod
    def _profile_high_interests(profile: dict[str, Any]) -> set[str]:
        high_interests = profile.get("high_interests")
        if isinstance(high_interests, list):
            return {str(category) for category in high_interests}
        interests = profile.get("interests")
        if isinstance(interests, dict) and interests:
            ranked = sorted(
                interests,
                key=lambda category: (-float(interests[category]), str(category)),
            )
            return {str(category) for category in ranked[:2]}
        primary = profile.get("primary_interest")
        return {str(primary)} if primary else set()

    def _add_video(
        self,
        session: Session,
        *,
        creator_id: str,
        simulation_day: int,
        sequence: int,
        draft: dict[str, Any],
        source: str = "stub",
    ) -> None:
        session.add(
            VideoRow(
                video_id=self._video_id(creator_id, simulation_day, sequence),
                run_id=self.config.run_id,
                creator_id=creator_id,
                published_day=simulation_day,
                category=draft["category"],
                grounding_mode=draft["grounding_mode"],
                title=draft["title"],
                cover_description=draft["cover_description"],
                premise=draft["premise"],
                outline_json=draft["outline"],
                inspiration_json=draft.get("inspiration"),
                source=source,
            )
        )

    @staticmethod
    def _video_id(creator_id: str, simulation_day: int, sequence: int) -> str:
        return f"video_{creator_id}_d{simulation_day:03d}_{sequence:02d}"
