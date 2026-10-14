from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StubCreatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=24)
    category: str = Field(min_length=1, max_length=40)
    goal: str = Field(min_length=1)
    style: str = Field(min_length=1)
    topics: list[str] = Field(min_length=1)
    model_alias: Literal["stub-custom"] = "stub-custom"


class SimulationConfig(BaseModel):
    """Versioned configuration for one simulation run."""

    run_id: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    seed: int
    world_anchor_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    days: int = Field(default=7, ge=1)
    creator_count: int = Field(default=6, ge=1)
    stub_creators: list[StubCreatorConfig] = Field(default_factory=list)
    stub_viewer_interests: list[str] = Field(default_factory=list)
    viewer_count: int = Field(default=20, ge=1)
    homepage_size: int = Field(default=10, ge=1)
    max_clicks: int = Field(default=3, ge=0)
    publish_interval_days: int = Field(default=3, ge=1)
    initial_videos_per_creator: int = Field(default=1, ge=1)
    cold_impression_target: int = Field(default=12, ge=1)
    exploit_ratio: float = Field(default=0.8, ge=0, le=1)
    cold_ratio: float = Field(default=0.1, ge=0, le=1)
    explore_ratio: float = Field(default=0.1, ge=0, le=1)
    max_videos_per_creator: int = Field(default=2, ge=1)
    age_half_life_days: float = Field(default=14.0, gt=0)
    interest_slots_max: int = Field(default=0, ge=0)
    following_slots_max: int = Field(default=0, ge=0)
    overall_success_rate: float = Field(default=0.8, gt=0, le=1)
    cohort_model_success_rate: float = Field(default=0.8, gt=0, le=1)
    cohort_model_gate_min_tasks: int = Field(default=5, ge=1)
    viewer_note_mode: Literal["none", "brief"] = "brief"
    random_visitor_note_sample_rate: float = Field(default=0.03, ge=0, le=1)
    # The LLM describes qualitative post-click reaction; these uniform,
    # versioned probabilities model the extra friction of actually pressing a
    # public action button. They are deliberately not model-specific.
    follow_activation_open_to_more: float = Field(default=0.005, ge=0, le=1)
    follow_activation_actively_seek: float = Field(default=0.08, ge=0, le=1)
    triple_activation_notable: float = Field(default=0.003, ge=0, le=1)
    triple_activation_exceptional: float = Field(default=0.12, ge=0, le=1)
    execution_mode: Literal["stub", "llm"] = "stub"
    random_visitor_count: int = Field(default=0, ge=0)
    # Viewer work is I/O bound and OpenRouter can accept a much wider fan-out
    # than the early rehearsal runner used.  The actual stable value remains a
    # versioned Run choice and is validated by the concurrency probe.
    max_concurrency: int = Field(default=1, ge=1, le=256)
    # These control launch smoothness, not the number allowed in flight.  For
    # example, 100 ms + batch size 5 admits at most 50 new HTTP requests/s
    # while still allowing slower requests to accumulate up to max_concurrency.
    request_start_interval_ms: float = Field(default=0, ge=0, le=1000)
    request_start_batch_size: int = Field(default=1, ge=1, le=256)
    # A deterministic fraction of each day's Viewer sessions may run against
    # the previous completed catalogue while today's Creators are producing.
    # The remaining sessions wait for the publication boundary and can see
    # today's new videos. Zero preserves the original sequential day loop.
    viewer_early_fraction: float = Field(default=0, ge=0, le=0.95)
    # Viewer stages normally finish in a few minutes. After this operational
    # gate, a stage that is already at least 99% terminal may discard only its
    # remaining ordinary-population tail. Reference-panel tasks are protected
    # because sparse model-specific loss would bias the matched comparison.
    # Operational defaults are deliberately disabled. Formal v1.2 opts into
    # the ten-minute / 99% policy in its launcher config. These controls are
    # excluded from the scientific manifest because they can only turn a tiny
    # timed-out ordinary tail into missing observations; they never change a
    # successful response, prompt, recommendation, or actor state.
    viewer_tail_gate_seconds: float | None = Field(default=None, exclude=True)
    viewer_tail_recheck_seconds: float | None = Field(default=None, exclude=True)
    viewer_tail_completion_ratio: float | None = Field(
        default=None, exclude=True
    )

    @model_validator(mode="after")
    def validate_viewer_tail_policy(self) -> "SimulationConfig":
        values = (
            self.viewer_tail_gate_seconds,
            self.viewer_tail_recheck_seconds,
            self.viewer_tail_completion_ratio,
        )
        if all(value is None for value in values):
            return self
        if any(value is None for value in values):
            raise ValueError(
                "viewer tail policy fields must be configured together"
            )
        assert self.viewer_tail_gate_seconds is not None
        assert self.viewer_tail_recheck_seconds is not None
        assert self.viewer_tail_completion_ratio is not None
        if not 0 < self.viewer_tail_gate_seconds <= 3600:
            raise ValueError("viewer_tail_gate_seconds must be within 0..3600")
        if not 0 < self.viewer_tail_recheck_seconds <= 600:
            raise ValueError(
                "viewer_tail_recheck_seconds must be within 0..600"
            )
        if not 0 < self.viewer_tail_completion_ratio <= 1:
            raise ValueError(
                "viewer_tail_completion_ratio must be within 0..1"
            )
        return self
    creator_persona_path: Path | None = None
    viewer_persona_path: Path | None = None
    llm_runtime_path: Path | None = None
    creator_ids: list[str] = Field(default_factory=list)
    creator_model_alias: str | None = None
    viewer_model_alias: str | None = None
    creator_model_aliases: list[str] = Field(default_factory=list)
    creator_model_weights: dict[str, float] = Field(default_factory=dict)
    viewer_model_aliases: list[str] = Field(default_factory=list)
    viewer_model_weights: dict[str, float] = Field(default_factory=dict)
    creator_model_assignment: Literal[
        "round_robin", "mirrored_balanced", "mirrored_weighted"
    ] = "round_robin"
    reference_viewer_model_aliases: list[str] = Field(default_factory=list)
    reference_viewer_persona_count: int = Field(default=0, ge=0)
    # Optional explicit interest constellations for the matched-model panel.
    # Leaving this empty preserves the historical "first N" behavior; formal
    # runs name the constellations so the small panel is deliberately broad
    # instead of accidentally knowledge-heavy because of YAML ordering.
    reference_viewer_constellation_ids: list[str] = Field(default_factory=list)
    output_root: Path = Path("runs")
    # Optional lineage metadata for a non-canonical world branch.  A branch
    # keeps the parent's completed world state but owns only the new days it
    # produces; the parent archive is never copied into the branch directory.
    parent_run_id: str | None = Field(default=None, min_length=1, max_length=80)
    parent_day: int | None = Field(default=None, ge=0)
    experiment_purpose: str | None = Field(default=None, min_length=1, max_length=500)
    canonical: bool = True
    # Full evidence remains the default for ordinary/formal runs. Short-lived
    # branches default to compact evidence when created by ``world-fork``.
    evidence_mode: Literal["full", "compact"] = "full"
    # Branches must prove that the external Persona/runtime files used to
    # rebuild services still match the files present when the branch was
    # materialized. Canonical legacy runs intentionally leave this empty.
    dependency_sha256: dict[str, str] = Field(default_factory=dict)
    # A branch also freezes the executable simulation contract. Presentation
    # and diagnostics may evolve freely; behavior/prompt/recommendation code
    # changes require re-forking from the immutable snapshot.
    continuation_code_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def validate_world_lineage(self) -> "SimulationConfig":
        if (self.parent_run_id is None) != (self.parent_day is None):
            raise ValueError("parent_run_id and parent_day must be set together")
        if self.parent_day is not None:
            if self.days <= self.parent_day:
                raise ValueError("a branch must end after its parent day")
            if self.canonical:
                raise ValueError("a world with a parent cannot be canonical")
        elif not self.canonical:
            raise ValueError("a non-canonical world must identify its parent")
        allowed_dependencies = {
            "creator_persona_path",
            "viewer_persona_path",
            "llm_runtime_path",
        }
        unknown_dependencies = set(self.dependency_sha256) - allowed_dependencies
        if unknown_dependencies:
            raise ValueError(
                "unknown dependency hash keys: "
                + ", ".join(sorted(unknown_dependencies))
            )
        invalid_hashes = [
            name
            for name, digest in self.dependency_sha256.items()
            if len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ]
        if invalid_hashes:
            raise ValueError(
                "dependency hashes must be lowercase SHA-256: "
                + ", ".join(sorted(invalid_hashes))
            )
        return self

    @model_validator(mode="after")
    def validate_recommendation_contract(self) -> "SimulationConfig":
        if self.stub_creators:
            if self.execution_mode != "stub" or len(self.stub_creators) != self.creator_count:
                raise ValueError("stub_creators require stub mode and exactly creator_count entries")
            if len({c.name for c in self.stub_creators}) != len(self.stub_creators):
                raise ValueError("stub creator names must be unique")
        if self.stub_viewer_interests and self.execution_mode != "stub":
            raise ValueError("stub_viewer_interests require stub mode")
        ratio_sum = self.exploit_ratio + self.cold_ratio + self.explore_ratio
        if abs(ratio_sum - 1.0) > 1e-9:
            raise ValueError("exploit_ratio + cold_ratio + explore_ratio must equal 1")
        maximum_distinct = self.creator_count * self.max_videos_per_creator
        if self.homepage_size > maximum_distinct:
            raise ValueError(
                "homepage_size exceeds the per-creator cap capacity "
                f"({self.homepage_size} > {maximum_distinct})"
            )
        personalized_capacity = self.interest_slots_max + self.following_slots_max
        if personalized_capacity > self.homepage_size:
            raise ValueError(
                "interest_slots_max + following_slots_max cannot exceed homepage_size"
            )
        if (
            self.follow_activation_actively_seek
            < self.follow_activation_open_to_more
        ):
            raise ValueError(
                "actively-seek follow activation cannot be below open-to-more"
            )
        if self.triple_activation_exceptional < self.triple_activation_notable:
            raise ValueError(
                "exceptional triple activation cannot be below notable"
            )
        if self.execution_mode == "llm":
            required_paths = {
                "creator_persona_path": self.creator_persona_path,
                "viewer_persona_path": self.viewer_persona_path,
                "llm_runtime_path": self.llm_runtime_path,
            }
            missing_paths = [name for name, value in required_paths.items() if value is None]
            if missing_paths:
                raise ValueError(
                    "llm execution requires: " + ", ".join(missing_paths)
                )
            if not self.resolved_creator_model_aliases or not self.resolved_viewer_model_aliases:
                raise ValueError(
                    "llm execution requires a creator and viewer model alias"
                )
            if self.viewer_model_weights:
                aliases = set(self.resolved_viewer_model_aliases)
                if set(self.viewer_model_weights) != aliases:
                    raise ValueError(
                        "viewer_model_weights must cover viewer_model_aliases exactly"
                    )
                if any(weight <= 0 for weight in self.viewer_model_weights.values()):
                    raise ValueError("viewer_model_weights must be positive")
            if self.creator_model_weights:
                aliases = set(self.resolved_creator_model_aliases)
                if set(self.creator_model_weights) != aliases:
                    raise ValueError(
                        "creator_model_weights must cover creator_model_aliases exactly"
                    )
                if any(weight <= 0 for weight in self.creator_model_weights.values()):
                    raise ValueError("creator_model_weights must be positive")
                if self.creator_model_assignment != "mirrored_weighted":
                    raise ValueError(
                        "creator_model_weights are only used by mirrored_weighted"
                    )
            if (
                self.creator_model_assignment == "mirrored_weighted"
                and not self.creator_model_weights
            ):
                raise ValueError(
                    "mirrored_weighted creator assignment requires weights"
                )
            if self.reference_viewer_persona_count:
                if not self.reference_viewer_model_aliases:
                    raise ValueError(
                        "reference viewer personas require reference model aliases"
                    )
                reference_viewers = (
                    self.reference_viewer_persona_count
                    * len(self.reference_viewer_model_aliases)
                )
                if reference_viewers > self.viewer_count:
                    raise ValueError(
                        "reference viewer matrix exceeds fixed viewer_count"
                    )
                if (
                    self.reference_viewer_constellation_ids
                    and len(self.reference_viewer_constellation_ids)
                    != self.reference_viewer_persona_count
                ):
                    raise ValueError(
                        "reference_viewer_constellation_ids must be empty or "
                        "contain exactly reference_viewer_persona_count IDs"
                    )
                if len(set(self.reference_viewer_constellation_ids)) != len(
                    self.reference_viewer_constellation_ids
                ):
                    raise ValueError(
                        "reference_viewer_constellation_ids must be unique"
                    )
            elif self.reference_viewer_constellation_ids:
                raise ValueError(
                    "reference_viewer_constellation_ids require reference "
                    "viewer personas"
                )
            if self.creator_ids and len(self.creator_ids) != self.creator_count:
                raise ValueError(
                    "creator_ids must be empty or contain exactly creator_count IDs"
                )
            if self.initial_videos_per_creator != 1:
                raise ValueError("llm execution uses exactly one Day-0 video per creator")
        return self

    @property
    def resolved_creator_model_aliases(self) -> list[str]:
        if self.creator_model_aliases:
            return self.creator_model_aliases
        return [self.creator_model_alias] if self.creator_model_alias else []

    @property
    def resolved_viewer_model_aliases(self) -> list[str]:
        if self.viewer_model_aliases:
            return self.viewer_model_aliases
        return [self.viewer_model_alias] if self.viewer_model_alias else []

    @property
    def reference_viewer_count(self) -> int:
        return (
            self.reference_viewer_persona_count
            * len(self.reference_viewer_model_aliases)
        )

    @property
    def run_dir(self) -> Path:
        return self.output_root / self.run_id

    @property
    def database_path(self) -> Path:
        return self.run_dir / "ailiaili.db"

    @property
    def planned_viewer_sessions_per_day(self) -> int:
        return self.viewer_count + self.random_visitor_count

    def bucket_counts(self, total: int | None = None) -> tuple[int, int, int]:
        size = self.homepage_size if total is None else total
        cold = round(size * self.cold_ratio)
        explore = round(size * self.explore_ratio)
        exploit = size - cold - explore
        return exploit, cold, explore

    @classmethod
    def from_yaml(cls, path: Path) -> "SimulationConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def manifest_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")
