from __future__ import annotations

from dataclasses import dataclass

from ailiaili.domain import (
    HomepageDecision,
    ViewerPostClickAssessment,
)
from ailiaili.llm.config import LLMRuntimeConfig
from ailiaili.llm.gateway import ModelGateway, TailDiscardSignal
from ailiaili.persistence.database import Database
from ailiaili.persistence.models import ViewerRow
from ailiaili.persona import (
    ViewerPersonaFactory,
    ViewerPersonaSnapshot,
    ViewerPopulationSpec,
    ViewerSessionContext,
)
from ailiaili.utils import (
    stable_provider_seed,
    stable_seed,
    stable_unit,
    weighted_alias_assignments,
)
from ailiaili.viewer.prompt import (
    HOMEPAGE_INTENT_SAFETY_CAP,
    homepage_messages,
    post_click_assessment_messages,
    validate_homepage_decision,
    validate_post_click_assessment,
)


HOMEPAGE_PROMPT_VERSION = "viewer-homepage-v0.8-hidden-cap"
POST_CLICK_PROMPT_VERSION = "viewer-post-click-v0.6-assessment"


@dataclass(frozen=True)
class ViewerActor:
    actor_id: str
    kind: str
    name: str
    model_alias: str
    profile: ViewerPersonaSnapshot
    mirror_group_id: str | None = None
    cohort_role: str = "population"


def reference_panel_personas(
    *,
    population: ViewerPopulationSpec,
    persona_count: int,
    constellation_ids: list[str] | None,
    run_seed: int,
) -> list[tuple[str, ViewerPersonaSnapshot]]:
    """Build the exact base personas used by the matched-model viewer panel.

    Keeping this construction outside ``ViewerService`` lets the final
    preflight diagnostic exercise the production personas without copying the
    selection algorithm or creating thousands of ordinary viewer rows.
    """

    if persona_count < 0:
        raise ValueError("reference persona count cannot be negative")
    if not persona_count:
        return []
    factory = ViewerPersonaFactory(population, seed=run_seed)
    catalog = factory.recipe_catalog(variants_per_pair=4)
    by_constellation: dict[str, list[object]] = {}
    for recipe in catalog:
        by_constellation.setdefault(recipe.constellation_id, []).append(recipe)
    selected_constellations = list(constellation_ids or []) or [
        item.constellation_id for item in population.constellations
    ]
    unknown = set(selected_constellations) - set(by_constellation)
    if unknown:
        raise ValueError(
            "unknown reference viewer constellations: "
            + ", ".join(sorted(unknown))
        )
    if constellation_ids and len(selected_constellations) != persona_count:
        raise ValueError(
            "reference constellation IDs must match reference persona count"
        )

    result: list[tuple[str, ViewerPersonaSnapshot]] = []
    for group_index in range(persona_count):
        constellation_id = selected_constellations[
            group_index % len(selected_constellations)
        ]
        choices = by_constellation[constellation_id]
        recipe = choices[
            stable_seed(
                run_seed,
                "reference-viewer-recipe",
                group_index,
            )
            % len(choices)
        ]
        group_id = f"viewer_mirror_{group_index + 1:03d}"
        profile = factory.instantiate(
            recipe,
            viewer_id=group_id,
            instance_seed=stable_seed(
                run_seed,
                "reference-viewer-persona",
                group_index,
            ),
        )
        result.append((group_id, profile))
    return result


class ViewerService:
    """Production audience path with persistent viewers and ephemeral visitors."""

    def __init__(
        self,
        *,
        database: Database,
        gateway: ModelGateway,
        runtime: LLMRuntimeConfig,
        population: ViewerPopulationSpec,
        model_aliases: list[str],
        model_weights: dict[str, float] | None = None,
        reference_model_aliases: list[str] | None = None,
        reference_persona_count: int = 0,
        reference_constellation_ids: list[str] | None = None,
        fixed_viewer_count: int,
        random_visitor_count: int,
        fixed_note_mode: str,
        random_note_sample_rate: float,
        run_seed: int,
    ):
        self.database = database
        self.gateway = gateway
        self.runtime = runtime
        if not model_aliases:
            raise ValueError("at least one viewer model alias is required")
        self.model_aliases = list(model_aliases)
        self.model_weights = dict(model_weights or {})
        if self.model_weights:
            if set(self.model_weights) != set(self.model_aliases):
                raise ValueError("model weights must cover population aliases exactly")
            if any(weight <= 0 for weight in self.model_weights.values()):
                raise ValueError("model weights must be positive")
        self.reference_model_aliases = list(reference_model_aliases or [])
        self.reference_persona_count = reference_persona_count
        self.reference_constellation_ids = list(
            reference_constellation_ids or []
        )
        self.fixed_viewer_count = fixed_viewer_count
        self.random_visitor_count = random_visitor_count
        self.fixed_note_mode = fixed_note_mode
        self.random_note_sample_rate = random_note_sample_rate
        self.run_seed = run_seed
        self.factory = ViewerPersonaFactory(population, seed=run_seed)
        self.population = population
        reference_viewer_count = (
            self.reference_persona_count * len(self.reference_model_aliases)
        )
        if reference_viewer_count > self.fixed_viewer_count:
            raise ValueError("reference viewer matrix exceeds fixed viewer count")
        if self.reference_persona_count and not self.reference_model_aliases:
            raise ValueError("reference viewer personas require model aliases")
        known_constellations = {
            item.constellation_id for item in self.population.constellations
        }
        unknown_constellations = set(self.reference_constellation_ids) - known_constellations
        if unknown_constellations:
            raise ValueError(
                "unknown reference viewer constellations: "
                + ", ".join(sorted(unknown_constellations))
            )
        if self.reference_constellation_ids and len(
            self.reference_constellation_ids
        ) != self.reference_persona_count:
            raise ValueError(
                "reference constellation IDs must match reference persona count"
            )
        for alias in set(self.model_aliases + self.reference_model_aliases):
            self.runtime.model(alias)

    @property
    def planned_session_count(self) -> int:
        return self.fixed_viewer_count + self.random_visitor_count

    def viewer_row_values(self, *, run_id: str) -> list[dict[str, object]]:
        rows = self._reference_viewer_rows(run_id=run_id)
        population_count = self.fixed_viewer_count - len(rows)
        if population_count == 0:
            return rows
        snapshots = self.factory.sample_population(
            count=population_count,
            seed=stable_seed(self.run_seed, "fixed-viewer-population"),
        )
        model_assignments = self._population_model_assignments(
            count=population_count,
            seed=stable_seed(self.run_seed, "fixed-viewer-models"),
        )
        for index, snapshot in enumerate(snapshots):
            viewer_id = f"viewer_pop_{index + 1:05d}"
            snapshot = snapshot.model_copy(update={"viewer_id": viewer_id})
            rows.append(
                {
                    "viewer_id": viewer_id,
                    "run_id": run_id,
                    "name": (
                        f"{snapshot.main_need} · {snapshot.interest_constellation}"
                    ),
                    "model_alias": model_assignments[index],
                    "mirror_group_id": None,
                    "decision_variant": 0,
                    "profile_json": snapshot.model_dump(mode="json"),
                }
            )
        return rows

    def _reference_viewer_rows(self, *, run_id: str) -> list[dict[str, object]]:
        if not self.reference_persona_count:
            return []
        rows: list[dict[str, object]] = []
        bases = reference_panel_personas(
            population=self.population,
            persona_count=self.reference_persona_count,
            constellation_ids=self.reference_constellation_ids,
            run_seed=self.run_seed,
        )
        for group_index, (group_id, base) in enumerate(bases):
            for model_slot, model_alias in enumerate(
                self.reference_model_aliases
            ):
                viewer_id = f"viewer_ref_{group_index + 1:03d}_m{model_slot + 1:02d}"
                snapshot = base.model_copy(update={"viewer_id": viewer_id})
                rows.append(
                    {
                        "viewer_id": viewer_id,
                        "run_id": run_id,
                        "name": (
                            f"{snapshot.main_need} · "
                            f"{snapshot.interest_constellation}"
                        ),
                        "model_alias": model_alias,
                        "mirror_group_id": group_id,
                        "decision_variant": model_slot,
                        "profile_json": snapshot.model_dump(mode="json"),
                    }
                )
        return rows

    def actors_for_day(self, *, run_id: str, simulation_day: int) -> list[ViewerActor]:
        with self.database.session() as session:
            fixed_rows = session.query(ViewerRow).filter(
                ViewerRow.run_id == run_id
            ).order_by(ViewerRow.viewer_id).all()
        actors = [
            ViewerActor(
                actor_id=row.viewer_id,
                kind="fixed",
                name=row.name,
                model_alias=row.model_alias,
                profile=ViewerPersonaSnapshot.model_validate(row.profile_json),
                mirror_group_id=row.mirror_group_id,
                cohort_role=(
                    "reference" if row.mirror_group_id else "population"
                ),
            )
            for row in fixed_rows
        ]
        if self.random_visitor_count:
            snapshots = self.factory.sample_population(
                count=self.random_visitor_count,
                seed=stable_seed(
                    self.run_seed, "random-visitors", simulation_day
                ),
            )
            model_assignments = self._population_model_assignments(
                count=self.random_visitor_count,
                seed=stable_seed(
                    self.run_seed, "random-visitor-models", simulation_day
                ),
            )
            for index, snapshot in enumerate(snapshots):
                actor_id = f"visitor_d{simulation_day:03d}_{index + 1:05d}"
                snapshot = snapshot.model_copy(update={"viewer_id": actor_id})
                actors.append(
                    ViewerActor(
                        actor_id=actor_id,
                        kind="random",
                        name=(
                            f"随机访客 · {snapshot.main_need} · "
                            f"{snapshot.interest_constellation}"
                        ),
                        model_alias=model_assignments[index],
                        profile=snapshot,
                        cohort_role="population",
                    )
                )
        return actors

    def _population_model_assignments(
        self, *, count: int, seed: int
    ) -> list[str]:
        """Return reproducible exact-ish quotas without persona/order coupling."""

        if count <= 0:
            return []
        if not self.model_weights:
            return [
                self.model_aliases[index % len(self.model_aliases)]
                for index in range(count)
            ]

        return weighted_alias_assignments(
            aliases=self.model_aliases,
            weights=self.model_weights,
            count=count,
            seed=seed,
        )

    def session_context(
        self, *, actor: ViewerActor, simulation_day: int
    ) -> ViewerSessionContext:
        return self.factory.session_context(
            viewer_id=actor.mirror_group_id or actor.actor_id,
            simulation_day=simulation_day,
            session_index=0,
        )

    def note_mode(self, *, actor: ViewerActor, simulation_day: int) -> str:
        if actor.kind == "fixed":
            return self.fixed_note_mode
        sampled = stable_unit(
            self.run_seed,
            "random-visitor-note",
            simulation_day,
            actor.actor_id,
        ) < self.random_note_sample_rate
        return "brief" if sampled else "none"

    def choose_homepage(
        self,
        *,
        run_id: str,
        simulation_day: int,
        actor: ViewerActor,
        context: ViewerSessionContext,
        candidate_videos: list[dict[str, object]],
        tail_discard_signal: TailDiscardSignal | None = None,
    ) -> HomepageDecision:
        canonical_ids = [str(video["video_id"]) for video in candidate_videos]
        short_ids = [f"c{index:02d}" for index in range(1, len(canonical_ids) + 1)]
        canonical_by_short = dict(zip(short_ids, canonical_ids, strict=True))
        prompt_videos = [
            {**video, "video_id": short_id}
            for video, short_id in zip(candidate_videos, short_ids, strict=True)
        ]
        decision = self.gateway.invoke_json(
            run_id=run_id,
            simulation_day=simulation_day,
            task_type="homepage_choice",
            agent_role="viewer",
            agent_id=actor.actor_id,
            cohort_role=actor.cohort_role,
            workload_scope="main_world",
            model_spec=self.runtime.model(actor.model_alias),
            prompt_version=HOMEPAGE_PROMPT_VERSION,
            idempotency_key=(
                f"homepage:{HOMEPAGE_PROMPT_VERSION}:"
                f"d{simulation_day:03d}:{actor.actor_id}"
            ),
            messages=homepage_messages(
                persona=actor.profile.homepage_prompt_dict(),
                session_context=context.model_dump(mode="json"),
                candidate_videos=prompt_videos,
            ),
            output_model=HomepageDecision,
            max_output_tokens=256,
            temperature=0,
            seed=stable_provider_seed(
                self.run_seed,
                "homepage-choice",
                simulation_day,
                actor.mirror_group_id or actor.actor_id,
            ),
            reasoning_enabled=(
                self.runtime.reasoning.reference_viewer
                if actor.cohort_role == "reference"
                else self.runtime.reasoning.population_homepage
            ),
            output_validator=lambda result: validate_homepage_decision(
                result,
                candidate_video_ids=short_ids,
                output_cap=HOMEPAGE_INTENT_SAFETY_CAP,
            ),
            tail_discard_signal=tail_discard_signal,
        )
        # The prompt asks for unconstrained qualitative intentions. The world
        # still records at most its configured 0-3 actions, in stated order.
        admitted_short_ids = decision.clicked_video_ids[: context.click_cap]
        return HomepageDecision(
            clicked_video_ids=[
                canonical_by_short[short_id]
                for short_id in admitted_short_ids
            ]
        )

    def choose_post_click(
        self,
        *,
        run_id: str,
        simulation_day: int,
        actor: ViewerActor,
        clicked_video: dict[str, object],
        note_mode: str,
        tail_discard_signal: TailDiscardSignal | None = None,
    ) -> ViewerPostClickAssessment:
        video_id = str(clicked_video["video_id"])
        return self.gateway.invoke_json(
            run_id=run_id,
            simulation_day=simulation_day,
            task_type="post_click_choice",
            agent_role="viewer",
            agent_id=actor.actor_id,
            cohort_role=actor.cohort_role,
            workload_scope="main_world",
            model_spec=self.runtime.model(actor.model_alias),
            prompt_version=POST_CLICK_PROMPT_VERSION,
            idempotency_key=(
                f"post-click:{POST_CLICK_PROMPT_VERSION}:"
                f"d{simulation_day:03d}:{actor.actor_id}:{video_id}"
            ),
            messages=post_click_assessment_messages(
                persona=actor.profile.post_click_prompt_dict(
                    category=str(clicked_video["category"])
                ),
                clicked_video=clicked_video,
                note_mode=note_mode,
                calibration_variant="semantic",
            ),
            output_model=ViewerPostClickAssessment,
            max_output_tokens=400 if note_mode == "brief" else 192,
            temperature=0,
            seed=stable_provider_seed(
                self.run_seed,
                "post-click-choice",
                simulation_day,
                actor.mirror_group_id or actor.actor_id,
                video_id,
            ),
            reasoning_enabled=(
                self.runtime.reasoning.reference_viewer
                if actor.cohort_role == "reference"
                else self.runtime.reasoning.population_post_click
            ),
            output_validator=lambda result: validate_post_click_assessment(
                result,
                clicked_video_id=video_id,
                note_mode=note_mode,
            ),
            tail_discard_signal=tail_discard_signal,
        )
