from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="running")
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    last_completed_day: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DayRow(Base):
    __tablename__ = "days"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"), primary_key=True
    )
    simulation_day: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    planned_creator_turns: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_creator_turns: Mapped[int] = mapped_column(Integer, default=0)
    exhausted_creator_turns: Mapped[int] = mapped_column(Integer, default=0)
    skipped_creator_turns: Mapped[int] = mapped_column(Integer, default=0)
    planned_viewer_sessions: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_viewer_sessions: Mapped[int] = mapped_column(Integer, default=0)
    exhausted_viewer_sessions: Mapped[int] = mapped_column(Integer, default=0)
    skipped_viewer_sessions: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CreatorRow(Base):
    __tablename__ = "creators"

    creator_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(80), nullable=False)
    mirror_group_id: Mapped[str | None] = mapped_column(String(80), index=True)
    mirror_slot: Mapped[int | None] = mapped_column(Integer)
    schedule_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    persona_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    declaration_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    memory_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class CreatorTurnRow(Base):
    __tablename__ = "creator_turns"
    __table_args__ = (
        UniqueConstraint("run_id", "simulation_day", "creator_id"),
    )

    turn_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    simulation_day: Mapped[int] = mapped_column(Integer, index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.creator_id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    creator_note: Mapped[str | None] = mapped_column(Text)
    expected_reception: Mapped[str | None] = mapped_column(String(20))
    prediction_reason: Mapped[str | None] = mapped_column(Text)
    memory_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    video_id: Mapped[str | None] = mapped_column(String(120))


class ViewerRow(Base):
    __tablename__ = "viewers"

    viewer_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(80), nullable=False)
    mirror_group_id: Mapped[str | None] = mapped_column(String(80), index=True)
    decision_variant: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profile_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class FollowRow(Base):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("run_id", "viewer_id", "creator_id"),)

    follow_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    viewer_id: Mapped[str] = mapped_column(ForeignKey("viewers.viewer_id"))
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.creator_id"))
    created_day: Mapped[int] = mapped_column(Integer, nullable=False)


class ViewerClickHistoryRow(Base):
    """Compacted pre-branch click exclusions for persistent viewers.

    Full parent homepage sessions stay in the parent archive.  A branch only
    needs to know which old videos each fixed viewer has already opened.
    """

    __tablename__ = "viewer_click_history"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"), primary_key=True
    )
    viewer_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    video_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    first_clicked_day: Mapped[int] = mapped_column(Integer, nullable=False)
    last_clicked_day: Mapped[int] = mapped_column(Integer, nullable=False)


class PanelShownHistoryRow(Base):
    """Last inherited exposure per matched Viewer panel and video."""

    __tablename__ = "panel_shown_history"

    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.run_id"), primary_key=True
    )
    mirror_group_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    video_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    last_shown_day: Mapped[int] = mapped_column(Integer, nullable=False)


class VideoRow(Base):
    __tablename__ = "videos"

    video_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    creator_id: Mapped[str] = mapped_column(ForeignKey("creators.creator_id"), index=True)
    published_day: Mapped[int] = mapped_column(Integer, index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    grounding_mode: Mapped[str] = mapped_column(
        String(30), nullable=False, default="synthesis"
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    cover_description: Mapped[str] = mapped_column(Text, nullable=False)
    premise: Mapped[str] = mapped_column(Text, nullable=False)
    outline_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    inspiration_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(30), nullable=False, default="stub")


class HomepageSessionRow(Base):
    __tablename__ = "homepage_sessions"
    __table_args__ = (
        UniqueConstraint("run_id", "simulation_day", "viewer_id"),
    )

    homepage_session_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    simulation_day: Mapped[int] = mapped_column(Integer, index=True)
    # Ephemeral visitors intentionally have no ViewerRow.  The stable actor ID
    # remains in the session evidence, but is not a foreign key.
    viewer_id: Mapped[str] = mapped_column(String(120), nullable=False)
    viewer_kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="fixed"
    )
    cohort_role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="population"
    )
    model_alias: Mapped[str | None] = mapped_column(String(80))
    mirror_group_id: Mapped[str | None] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    viewer_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    session_context_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    candidate_items_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    clicked_video_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    post_click_decisions_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )
    technical_failures_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    viewer_note_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)


class DailyMetricRow(Base):
    __tablename__ = "daily_metrics"
    __table_args__ = (
        UniqueConstraint("run_id", "simulation_day", "entity_type", "entity_id"),
    )

    metric_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    simulation_day: Mapped[int] = mapped_column(Integer, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120), nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    completed_follow_opportunities: Mapped[int] = mapped_column(Integer, default=0)
    new_follows: Mapped[int] = mapped_column(Integer, default=0)
    completed_post_click_opportunities: Mapped[int] = mapped_column(Integer, default=0)
    triples: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[float | None] = mapped_column(Float)


class LLMCallRow(Base):
    """One logical task that needs one accepted model result."""

    __tablename__ = "llm_calls"
    __table_args__ = (UniqueConstraint("run_id", "idempotency_key"),)

    call_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    simulation_day: Mapped[int] = mapped_column(Integer, index=True)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    agent_role: Mapped[str] = mapped_column(String(30), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False)
    cohort_role: Mapped[str] = mapped_column(String(30), nullable=False)
    workload_scope: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    accepted_attempt_id: Mapped[str | None] = mapped_column(String(140))
    input_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LLMAttemptRow(Base):
    """One request actually dispatched to a model provider."""

    __tablename__ = "llm_attempts"
    __table_args__ = (UniqueConstraint("call_id", "attempt_number"),)

    attempt_id: Mapped[str] = mapped_column(String(140), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("llm_calls.call_id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(160), nullable=False)
    resolved_model: Mapped[str | None] = mapped_column(String(160))
    resolved_provider: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(180))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer)
    usage_raw_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 9))
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    cost_is_estimate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    request_ref: Mapped[str | None] = mapped_column(Text)
    response_ref: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
