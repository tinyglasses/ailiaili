from __future__ import annotations

from ailiaili.creator.observation import CreatorObservationBuilder
from ailiaili.creator.prompt import creator_turn_messages, validate_creator_turn
from ailiaili.domain import (
    CreatorAccountOverview,
    CreatorMemory,
    CreatorObservationPack,
    CreatorTurnResult,
)
from ailiaili.llm.config import LLMRuntimeConfig
from ailiaili.llm.gateway import ModelGateway
from ailiaili.persistence.database import Database
from ailiaili.persona import CreatorPersona, CreatorPopulation
from ailiaili.utils import (
    stable_provider_seed,
    stable_seed,
    weighted_alias_assignments,
)


CREATOR_PROMPT_VERSION = "creator-turn-v0.9"


def creator_success_definition(creator: CreatorPersona) -> str:
    definitions = {
        "求真与掌握": "做出自己愿意反复推敲、也能让观众真正多弄懂一点的内容。",
        "表达与作品": "做出带着自己声音、换一个账号就不会自然出现的作品。",
        "服务与效用": "让观众看完以后真的多一个能使用、判断或尝试的办法。",
        "同类与关系": "让某些人觉得自己被看见，并愿意以后再来找这个账号。",
        "影响与增长": "让更多人愿意停下来，但不把账号做成自己都认不出的样子。",
    }
    return definitions[creator.primary_drive]


def creator_declaration(creator: CreatorPersona) -> dict[str, str]:
    return {
        "我想做的账号": creator.public_bio,
        "我为什么开始": creator.origin,
        "我想遇见的人": creator.imagined_audience,
        "我不会为了平台轻易放弃的东西": creator.identity_floor,
        "我目前怎样理解成功": creator_success_definition(creator),
        "我最初认为什么内容会成功": creator.initial_intuitions[0],
    }


def creator_initial_memory(creator: CreatorPersona) -> CreatorMemory:
    return CreatorMemory(
        current_direction="先按自己的创作直觉做第一期，不假装已经知道平台规律。",
        beliefs=list(creator.initial_intuitions),
        memorable_experiences=[],
        ideas_to_try=[],
        unresolved_tension=creator.productive_tension,
    )


def genesis_observation_pack(
    creator: CreatorPersona, *, world_anchor_date: str
) -> CreatorObservationPack:
    """Build a normal Creator Turn input with no invented Day-0 history."""

    return CreatorObservationPack(
        simulation_day=0,
        world_anchor_date=world_anchor_date,
        creator_id=creator.creator_id,
        creator_name=creator.display_name,
        primary_category=creator.primary_category,
        persona=creator.private_prompt_dict(),
        declaration=creator_declaration(creator),
        current_memory=creator_initial_memory(creator),
        account_overview=CreatorAccountOverview(
            video_count=0,
            cumulative_follows=0,
            impressions=0,
            clicks=0,
            new_follows=0,
            triples=0,
            ctr=0,
            follow_rate=0,
            triple_rate=0,
        ),
        own_recent_videos=[],
        global_trending=[],
        category_trending=[],
        recent_new_videos=[],
    )


class CreatorService:
    """Production creator path: bounded observation, one call, one publication."""

    def __init__(
        self,
        *,
        database: Database,
        gateway: ModelGateway,
        runtime: LLMRuntimeConfig,
        population: CreatorPopulation,
        creator_ids: list[str],
        creator_count: int,
        model_aliases: list[str],
        model_weights: dict[str, float] | None = None,
        model_assignment: str = "round_robin",
        world_anchor_date: str,
        run_seed: int,
    ):
        self.database = database
        self.gateway = gateway
        self.runtime = runtime
        if not model_aliases:
            raise ValueError("at least one creator model alias is required")
        self.model_aliases = list(model_aliases)
        self.model_weights = dict(model_weights or {})
        self.model_assignment = model_assignment
        self.world_anchor_date = world_anchor_date
        self.run_seed = run_seed
        by_id = {creator.creator_id: creator for creator in population.creators}
        selected_ids = creator_ids or [
            creator.creator_id for creator in population.creators[:creator_count]
        ]
        if len(selected_ids) != creator_count:
            raise ValueError("selected creator count does not match creator_count")
        unknown = set(selected_ids) - set(by_id)
        if unknown:
            raise ValueError(f"unknown creator IDs: {sorted(unknown)}")
        self.creators = [by_id[creator_id] for creator_id in selected_ids]
        self._by_id = {creator.creator_id: creator for creator in self.creators}
        self._model_by_creator = self._assign_models()
        # Fail fast before creating a run with a misspelled alias.
        for alias in set(self.model_aliases):
            self.runtime.model(alias)

    def _assign_models(self) -> dict[str, str]:
        if self.model_assignment == "round_robin":
            return {
                creator.creator_id: self.model_aliases[index % len(self.model_aliases)]
                for index, creator in enumerate(self.creators)
            }
        if self.model_assignment not in {
            "mirrored_balanced",
            "mirrored_weighted",
        }:
            raise ValueError(
                f"unknown creator model assignment: {self.model_assignment}"
            )

        groups: dict[str, list[CreatorPersona]] = {}
        independent: list[CreatorPersona] = []
        for creator in self.creators:
            if creator.mirror_group_id is None:
                independent.append(creator)
            else:
                groups.setdefault(creator.mirror_group_id, []).append(creator)

        assignments: dict[str, str] = {}
        for group_id, members in groups.items():
            if len(members) != len(self.model_aliases):
                raise ValueError(
                    f"mirror group {group_id} has {len(members)} members but "
                    f"the run has {len(self.model_aliases)} creator models"
                )
            for member in members:
                assert member.mirror_slot is not None
                assignments[member.creator_id] = self.model_aliases[
                    member.mirror_slot
                ]
        if self.model_assignment == "mirrored_weighted":
            independent_aliases = weighted_alias_assignments(
                aliases=self.model_aliases,
                weights=self.model_weights,
                count=len(independent),
                seed=stable_seed(
                    self.run_seed, "independent-creator-models"
                ),
            )
        else:
            independent_aliases = [
                self.model_aliases[index % len(self.model_aliases)]
                for index in range(len(independent))
            ]
        for creator, alias in zip(
            independent, independent_aliases, strict=True
        ):
            assignments[creator.creator_id] = alias
        return assignments

    def creator_row_values(
        self, *, run_id: str, publish_interval_days: int
    ) -> list[dict[str, object]]:
        """Return deterministic bootstrap rows without issuing model calls."""

        rows: list[dict[str, object]] = []
        schedule_group_offsets: dict[str, int] = {}
        next_schedule_offset = 0
        for index, creator in enumerate(self.creators):
            if creator.mirror_group_id is not None:
                if creator.mirror_group_id not in schedule_group_offsets:
                    schedule_group_offsets[creator.mirror_group_id] = (
                        next_schedule_offset % publish_interval_days
                    )
                    next_schedule_offset += 1
                schedule_offset = schedule_group_offsets[creator.mirror_group_id]
            else:
                schedule_offset = next_schedule_offset % publish_interval_days
                next_schedule_offset += 1
            rows.append(
                {
                    "creator_id": creator.creator_id,
                    "run_id": run_id,
                    "name": creator.display_name,
                    "category": creator.primary_category,
                    "model_alias": self._model_by_creator[creator.creator_id],
                    "mirror_group_id": creator.mirror_group_id,
                    "mirror_slot": creator.mirror_slot,
                    "schedule_offset": schedule_offset,
                    "persona_json": creator.private_prompt_dict(),
                    "declaration_json": creator_declaration(creator),
                    "memory_json": creator_initial_memory(creator).model_dump(mode="json"),
                }
            )
        return rows

    def create_turn(
        self, *, run_id: str, creator_id: str, simulation_day: int
    ) -> CreatorTurnResult:
        creator = self._by_id[creator_id]
        if simulation_day == 0:
            pack = genesis_observation_pack(
                creator, world_anchor_date=self.world_anchor_date
            )
        else:
            pack = CreatorObservationBuilder(self.database, run_id).build(
                creator_id=creator_id,
                simulation_day=simulation_day,
            )
        allowed_categories = [
            creator.primary_category,
            *creator.secondary_categories,
        ]
        model_alias = self._model_by_creator[creator_id]
        cohort_role = (
            "mirror"
            if creator.mirror_group_id is not None
            else "population"
        )
        model_spec = self.runtime.model(model_alias)
        return self.gateway.invoke_json(
            run_id=run_id,
            simulation_day=simulation_day,
            task_type="creator_turn",
            agent_role="creator",
            agent_id=creator_id,
            cohort_role=cohort_role,
            workload_scope="main_world",
            model_spec=model_spec,
            prompt_version=CREATOR_PROMPT_VERSION,
            idempotency_key=(
                f"creator-turn:{CREATOR_PROMPT_VERSION}:"
                f"d{simulation_day:03d}:{creator_id}"
            ),
            messages=creator_turn_messages(
                pack, allowed_categories=allowed_categories
            ),
            output_model=CreatorTurnResult,
            max_output_tokens=model_spec.creator_max_output_tokens,
            temperature=0.8,
            seed=stable_provider_seed(
                self.run_seed,
                "creator-turn",
                simulation_day,
                creator.mirror_group_id or creator_id,
            ),
            reasoning_enabled=self.runtime.reasoning.creator_turn,
            output_validator=lambda result: validate_creator_turn(
                result,
                pack=pack,
                allowed_categories=set(allowed_categories),
            ),
        )
