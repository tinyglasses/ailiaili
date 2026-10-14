from __future__ import annotations

import hashlib
import random
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


# Reference categories remain ordered below; new worlds may name their own.
Category = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^\S(?:[^\r\n]*\S)?$")]

CATEGORY_VALUES: tuple[Category, ...] = (
    "AI与未来",
    "科技与数码",
    "科学与知识",
    "历史与人文",
    "财经与商业",
    "游戏",
    "美食",
    "生活与社会",
    "娱乐与创意",
    "旅行与户外",
)

CreatorDrive = Literal[
    "求真与掌握",
    "表达与作品",
    "服务与效用",
    "同类与关系",
    "影响与增长",
]

CreativeMode = Literal[
    "调查考据",
    "原理解释",
    "动手实验",
    "叙事构建",
    "评论怀疑",
    "整理保存",
    "混搭二创",
    "实用解法",
    "陪伴记录",
    "增长经营",
    "企划挑战",
    "角色表演",
    "审美设计",
    "氛围记录",
]


class CreatorPersona(BaseModel):
    """Stable creator identity. Runtime memory deliberately lives elsewhere."""

    model_config = ConfigDict(extra="forbid")

    creator_id: str = Field(pattern=r"^creator_[0-9]{3}$")
    display_name: str = Field(min_length=2, max_length=24)
    mirror_group_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9_]+$"
    )
    mirror_slot: int | None = Field(default=None, ge=0, le=15)
    primary_category: Category
    secondary_categories: list[Category] = Field(default_factory=list, max_length=2)
    public_bio: str = Field(min_length=8, max_length=100)
    origin: str = Field(min_length=10, max_length=180)
    primary_drive: CreatorDrive
    secondary_drive: CreatorDrive
    imagined_audience: str = Field(min_length=8, max_length=140)
    creative_mode: CreativeMode
    creative_instinct: str = Field(min_length=10, max_length=180)
    voice: str = Field(min_length=5, max_length=120)
    evidence_stance: str = Field(min_length=8, max_length=150)
    packaging_instinct: str = Field(min_length=8, max_length=150)
    platform_relationship: str = Field(min_length=8, max_length=180)
    identity_floor: str = Field(min_length=8, max_length=160)
    productive_tension: str = Field(min_length=10, max_length=200)
    blind_spot: str = Field(min_length=8, max_length=160)
    initial_intuitions: list[str] = Field(min_length=1, max_length=2)
    ecology_roles: list[str] = Field(min_length=1, max_length=3)
    temperament_tags: list[str] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def validate_identity(self) -> "CreatorPersona":
        if (self.mirror_group_id is None) != (self.mirror_slot is None):
            raise ValueError(
                "mirror_group_id and mirror_slot must be provided together"
            )
        if self.primary_drive == self.secondary_drive:
            raise ValueError("primary_drive and secondary_drive must differ")
        if self.primary_category in self.secondary_categories:
            raise ValueError("primary category cannot be repeated as secondary")
        if len(self.secondary_categories) != len(set(self.secondary_categories)):
            raise ValueError("secondary categories cannot contain duplicates")
        if len(set(self.temperament_tags)) != len(self.temperament_tags):
            raise ValueError("temperament tags cannot contain duplicates")
        if len(set(self.ecology_roles)) != len(self.ecology_roles):
            raise ValueError("ecology roles cannot contain duplicates")
        if any(len(item) > 100 for item in self.initial_intuitions):
            raise ValueError("initial intuitions must stay concise")
        return self

    def private_prompt_dict(self) -> dict[str, object]:
        """World-facing identity without research-only population labels."""

        return {
            "账号名": self.display_name,
            "主分区": self.primary_category,
            "可自然跨入的分区": self.secondary_categories,
            "创作缘起": self.origin,
            "最重要的创作欲望": self.primary_drive,
            "同样真实的第二欲望": self.secondary_drive,
            "你想找到的观众": self.imagined_audience,
            "你的创作直觉": self.creative_instinct,
            "你的声音": self.voice,
            "你如何对待证据": self.evidence_stance,
            "你如何看标题与封面": self.packaging_instinct,
            "你与平台反馈的关系": self.platform_relationship,
            "你不愿轻易越过的身份底线": self.identity_floor,
            "你长期带着的矛盾": self.productive_tension,
            "创世时的直觉": self.initial_intuitions,
        }

    def lab_only_dict(self) -> dict[str, object]:
        """Initialization metadata, never evidence for an observed outcome."""

        return {
            # Kept only so older persona files remain loadable. Analysts must
            # not cite this design hypothesis as proof of an emergent blind spot.
            "designer_hypothesis_not_evidence": self.blind_spot,
            "creative_mode": self.creative_mode,
            "ecology_roles": self.ecology_roles,
            "temperament_tags": self.temperament_tags,
            "mirror_group_id": self.mirror_group_id,
            "mirror_slot": self.mirror_slot,
        }


class CreatorPopulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    expected_count: int = Field(default=50, ge=1)
    category_targets: dict[Category, int]
    creators: list[CreatorPersona]

    @model_validator(mode="after")
    def validate_population(self) -> "CreatorPopulation":
        if len(self.creators) != self.expected_count:
            raise ValueError(
                f"expected {self.expected_count} creators, got {len(self.creators)}"
            )
        ids = [creator.creator_id for creator in self.creators]
        names = [creator.display_name for creator in self.creators]
        if len(ids) != len(set(ids)):
            raise ValueError("creator IDs must be unique")
        if len(names) != len(set(names)):
            raise ValueError("creator display names must be unique")
        actual = Counter(creator.primary_category for creator in self.creators)
        if dict(actual) != self.category_targets:
            raise ValueError(
                f"category population differs from targets: {dict(actual)}"
            )
        mirror_groups: dict[str, list[CreatorPersona]] = {}
        for creator in self.creators:
            if creator.mirror_group_id is not None:
                mirror_groups.setdefault(creator.mirror_group_id, []).append(creator)
        for group_id, members in mirror_groups.items():
            slots = sorted(int(member.mirror_slot) for member in members)
            if len(members) < 2 or slots != list(range(len(members))):
                raise ValueError(
                    f"mirror group {group_id} must use contiguous slots from zero"
                )
            comparable = [
                member.model_dump(
                    exclude={
                        "creator_id",
                        "display_name",
                        "mirror_group_id",
                        "mirror_slot",
                    }
                )
                for member in members
            ]
            if any(item != comparable[0] for item in comparable[1:]):
                raise ValueError(
                    f"mirror group {group_id} members must share one core persona"
                )
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> "CreatorPopulation":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        extends = raw.pop("extends", None)
        appended = raw.pop("creators_append", [])
        mirror_groups = raw.pop("mirror_groups", [])
        mirror_group_clones = raw.pop("mirror_group_clones", [])
        excluded_creator_ids = {
            str(creator_id) for creator_id in raw.pop("exclude_creator_ids", [])
        }
        name_overrides = dict(raw.pop("creator_name_overrides", {}))
        base_creators: list[dict[str, object]] = []
        if extends is not None:
            base_path = (path.parent / str(extends)).resolve()
            base_raw = cls.from_yaml(base_path).model_dump(mode="python")
            base_creators = list(base_raw.pop("creators", []))
            raw = {**base_raw, **raw}
        else:
            base_creators = list(raw.pop("creators", []))

        base_by_id = {
            str(creator["creator_id"]): dict(creator) for creator in base_creators
        }
        unknown_exclusions = excluded_creator_ids - set(base_by_id)
        if unknown_exclusions:
            raise ValueError(
                "exclude_creator_ids reference unknown IDs: "
                f"{sorted(unknown_exclusions)}"
            )
        base_creators = [
            creator
            for creator in base_creators
            if str(creator["creator_id"]) not in excluded_creator_ids
        ]

        expanded_mirrors: list[dict[str, object]] = []
        for clone in mirror_group_clones:
            clone = dict(clone)
            source_creator_id = str(clone.pop("source_creator_id"))
            group_id = str(clone.pop("mirror_group_id"))
            member_ids = list(clone.pop("member_ids"))
            display_names = list(clone.pop("display_names"))
            if clone:
                raise ValueError(
                    f"unknown mirror clone fields for {group_id}: {sorted(clone)}"
                )
            if source_creator_id not in base_by_id:
                raise ValueError(
                    f"mirror clone source is unknown: {source_creator_id}"
                )
            if len(member_ids) != len(display_names):
                raise ValueError(
                    f"mirror group {group_id} needs one display name per member"
                )
            template = dict(base_by_id[source_creator_id])
            for field in (
                "creator_id",
                "display_name",
                "mirror_group_id",
                "mirror_slot",
            ):
                template.pop(field, None)
            for slot, (creator_id, display_name) in enumerate(
                zip(member_ids, display_names, strict=True)
            ):
                expanded_mirrors.append(
                    {
                        **template,
                        "creator_id": creator_id,
                        "display_name": display_name,
                        "mirror_group_id": group_id,
                        "mirror_slot": slot,
                    }
                )
        for group in mirror_groups:
            group = dict(group)
            group_id = str(group.pop("mirror_group_id"))
            member_ids = list(group.pop("member_ids"))
            display_names = list(group.pop("display_names"))
            template = dict(group.pop("template"))
            if group:
                raise ValueError(
                    f"unknown mirror group fields for {group_id}: {sorted(group)}"
                )
            if len(member_ids) != len(display_names):
                raise ValueError(
                    f"mirror group {group_id} needs one display name per member"
                )
            for slot, (creator_id, display_name) in enumerate(
                zip(member_ids, display_names, strict=True)
            ):
                expanded_mirrors.append(
                    {
                        **template,
                        "creator_id": creator_id,
                        "display_name": display_name,
                        "mirror_group_id": group_id,
                        "mirror_slot": slot,
                    }
                )
        creators = [
            *base_creators,
            *list(appended),
            *expanded_mirrors,
        ]
        if name_overrides:
            seen_overrides: set[str] = set()
            for creator in creators:
                creator_id = str(creator["creator_id"])
                if creator_id in name_overrides:
                    creator["display_name"] = str(name_overrides[creator_id])
                    seen_overrides.add(creator_id)
            unknown = set(name_overrides) - seen_overrides
            if unknown:
                raise ValueError(
                    f"creator name overrides reference unknown IDs: {sorted(unknown)}"
                )
        raw["creators"] = creators
        return cls.model_validate(raw)


ViewerNeedId = Literal[
    "understand",
    "use",
    "wonder",
    "story",
    "resonate",
    "identity",
    "challenge",
    "craft",
    "relax",
    "collect",
]

SelectivityId = Literal["very_selective", "selective", "normal", "open"]


class ViewerNeedTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    need_id: ViewerNeedId
    label: str = Field(min_length=2, max_length=20)
    motive: str = Field(min_length=8, max_length=140)
    follow_rule: str = Field(min_length=8, max_length=160)
    triple_rule: str = Field(min_length=8, max_length=160)
    compatible_secondary: list[ViewerNeedId] = Field(min_length=2, max_length=6)
    compatible_palettes: list[str] = Field(min_length=2, max_length=8)

    @model_validator(mode="after")
    def validate_need(self) -> "ViewerNeedTemplate":
        if self.need_id in self.compatible_secondary:
            raise ValueError("main need cannot repeat as compatible secondary")
        if len(set(self.compatible_secondary)) != len(self.compatible_secondary):
            raise ValueError("compatible secondary needs must be unique")
        if len(set(self.compatible_palettes)) != len(self.compatible_palettes):
            raise ValueError("compatible palettes must be unique")
        return self


class InterestConstellation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    constellation_id: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=2, max_length=30)
    high_categories: list[Category] = Field(min_length=1, max_length=3)
    medium_categories: list[Category] = Field(default_factory=list, max_length=4)
    interest_story: str = Field(min_length=8, max_length=160)
    topic_hooks: list[str] = Field(min_length=2, max_length=5)

    @model_validator(mode="after")
    def validate_categories(self) -> "InterestConstellation":
        high = set(self.high_categories)
        medium = set(self.medium_categories)
        if high & medium:
            raise ValueError("high and medium interest categories cannot overlap")
        if len(high) != len(self.high_categories) or len(medium) != len(
            self.medium_categories
        ):
            raise ValueError("interest categories cannot contain duplicates")
        return self


class ExpressionPalette(BaseModel):
    model_config = ConfigDict(extra="forbid")

    palette_id: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=2, max_length=24)
    preferred_presentations: list[str] = Field(min_length=2, max_length=5)
    aversions: list[str] = Field(min_length=1, max_length=4)
    novelty_posture: str = Field(min_length=6, max_length=100)
    title_posture: str = Field(min_length=6, max_length=120)


class SelectivityTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selectivity_id: SelectivityId
    label: str = Field(min_length=2, max_length=16)
    browse_rule: str = Field(min_length=8, max_length=140)


class SessionAttentionMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode_id: str = Field(pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=2, max_length=20)
    click_cap: int = Field(ge=1, le=8)
    instruction: str = Field(min_length=8, max_length=160)
    weight: int = Field(gt=0)
    selection_contract: Literal["explicit_cap", "qualitative"] = "explicit_cap"


class ViewerSessionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode_id: str
    label: str
    click_cap: int = Field(ge=1, le=8)
    instruction: str
    selection_contract: Literal["explicit_cap", "qualitative"] = "explicit_cap"


class ViewerPopulationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    needs: list[ViewerNeedTemplate]
    constellations: list[InterestConstellation]
    palettes: list[ExpressionPalette]
    selectivity: list[SelectivityTemplate]
    main_need_weights: dict[ViewerNeedId, int]
    constellation_weights: dict[str, int]
    selectivity_weights: dict[SelectivityId, int]
    surface_contexts: list[str] = Field(min_length=4)
    include_low_interests: bool = False
    low_interest_rule: str | None = Field(default=None, min_length=8, max_length=180)
    attention_modes: list[SessionAttentionMode] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_spec(self) -> "ViewerPopulationSpec":
        need_ids = [item.need_id for item in self.needs]
        constellation_ids = [item.constellation_id for item in self.constellations]
        palette_ids = [item.palette_id for item in self.palettes]
        selectivity_ids = [item.selectivity_id for item in self.selectivity]
        for values, label in (
            (need_ids, "need"),
            (constellation_ids, "constellation"),
            (palette_ids, "palette"),
            (selectivity_ids, "selectivity"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} IDs must be unique")
        if len(self.needs) != 10:
            raise ValueError("the v0.1 audience grammar requires exactly 10 needs")
        if not self.constellations:
            raise ValueError("audience grammar needs at least one constellation")
        if set(self.main_need_weights) != set(need_ids):
            raise ValueError("main need weights must cover need templates exactly")
        if set(self.constellation_weights) != set(constellation_ids):
            raise ValueError("constellation weights must cover constellations exactly")
        if set(self.selectivity_weights) != set(selectivity_ids):
            raise ValueError("selectivity weights must cover selectivity exactly")
        if any(weight <= 0 for weight in self.main_need_weights.values()):
            raise ValueError("main need weights must be positive")
        if any(weight <= 0 for weight in self.constellation_weights.values()):
            raise ValueError("constellation weights must be positive")
        if any(weight <= 0 for weight in self.selectivity_weights.values()):
            raise ValueError("selectivity weights must be positive")
        if self.include_low_interests and self.low_interest_rule is None:
            raise ValueError(
                "low_interest_rule is required when include_low_interests is true"
            )
        attention_ids = [item.mode_id for item in self.attention_modes]
        if len(attention_ids) != len(set(attention_ids)):
            raise ValueError("attention mode IDs must be unique")
        known_needs = set(need_ids)
        known_palettes = set(palette_ids)
        for need in self.needs:
            if not set(need.compatible_secondary) <= known_needs:
                raise ValueError(f"unknown secondary need in {need.need_id}")
            if not set(need.compatible_palettes) <= known_palettes:
                raise ValueError(f"unknown palette in {need.need_id}")
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> "ViewerPopulationSpec":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        extends = raw.pop("extends", None)
        if extends is not None:
            base_path = (path.parent / str(extends)).resolve()
            base_raw = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
            if "extends" in base_raw:
                raise ValueError("viewer persona overlays cannot chain extends")
            raw = {**base_raw, **raw}
        return cls.model_validate(raw)

    @property
    def active_categories(self) -> tuple[Category, ...]:
        """Categories used by this version, preserving old overlay behavior."""

        used = {
            category
            for constellation in self.constellations
            for category in (
                *constellation.high_categories,
                *constellation.medium_categories,
            )
        }
        return tuple(category for category in CATEGORY_VALUES if category in used) + tuple(
            sorted(used - set(CATEGORY_VALUES))
        )


class ViewerPersonaRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    main_need_id: ViewerNeedId
    secondary_need_id: ViewerNeedId
    constellation_id: str
    palette_id: str
    selectivity_id: SelectivityId


class ViewerPersonaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    viewer_id: str
    population_version: str
    recipe_id: str
    main_need: str
    secondary_need: str
    interest_constellation: str
    high_interests: list[Category]
    medium_interests: list[Category]
    low_interests: list[Category] = Field(default_factory=list)
    low_interest_rule: str | None = None
    interest_story: str
    topic_hooks: list[str]
    browsing_motive: str
    preferred_presentations: list[str]
    novelty_posture: str
    title_posture: str
    aversions: list[str]
    selectivity: str
    browse_rule: str
    follow_rule: str
    triple_rule: str
    surface_context: str

    def prompt_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "你这次主要想得到": self.main_need,
            "同样在意": self.secondary_need,
            "兴趣星座": self.interest_constellation,
            "高兴趣分区": self.high_interests,
            "中等兴趣分区": self.medium_interests,
            "兴趣不是标签而是": self.interest_story,
            "容易让你停下来的题材": self.topic_hooks,
            "浏览动机": self.browsing_motive,
            "偏爱的表达": self.preferred_presentations,
            "对新奇的态度": self.novelty_posture,
            "对标题包装的态度": self.title_posture,
            "容易劝退你的东西": self.aversions,
            "选择性": self.selectivity,
            "今天怎么刷": self.browse_rule,
            "关注意味着": self.follow_rule,
            "三连只会发生在": self.triple_rule,
            "常见浏览语境": self.surface_context,
        }
        if self.low_interests:
            result["低兴趣分区"] = self.low_interests
            result["低兴趣不是禁止，而是"] = self.low_interest_rule
        return result

    def homepage_prompt_dict(self) -> dict[str, object]:
        """Return only the persona facts available before a click.

        Follow and triple rules cannot affect whether a card is tempting and
        are sent later with the isolated post-click decision. Keeping them off
        the 40-card request shortens the dominant workload without deleting
        any audience dimension from the simulation.
        """

        result = self.prompt_dict()
        result.pop("关注意味着", None)
        result.pop("三连只会发生在", None)
        return result

    def post_click_prompt_dict(self, *, category: str) -> dict[str, object]:
        """Return the stable tastes needed after one video was opened.

        The model still receives every substantive preference that can affect
        follow, triple, or the two short notes. Homepage attention mechanics,
        the browsing situation, and eight-category lists are replaced by the
        clicked video's single interest relation because the click has already
        happened.
        """

        if category in self.high_interests:
            category_interest = "高兴趣"
        elif category in self.medium_interests:
            category_interest = "中等兴趣"
        elif category in self.low_interests:
            category_interest = "通常低兴趣，但这次已经主动点开"
        else:
            category_interest = "未预设"
        return {
            "这次主要想得到": self.main_need,
            "同样在意": self.secondary_need,
            "对本分区的兴趣": category_interest,
            "兴趣口味": self.interest_story,
            "容易被打动的题材": self.topic_hooks,
            "浏览动机": self.browsing_motive,
            "偏爱的表达": self.preferred_presentations,
            "对新奇的态度": self.novelty_posture,
            "对标题包装的态度": self.title_posture,
            "容易劝退": self.aversions,
            "选择性": self.selectivity,
            "关注规则": self.follow_rule,
            "三连规则": self.triple_rule,
        }


class ViewerPersonaFactory:
    """Deterministic, non-LLM audience recipe and persona generator."""

    def __init__(self, spec: ViewerPopulationSpec, *, seed: int):
        self.spec = spec
        self.seed = seed
        self._needs = {item.need_id: item for item in spec.needs}
        self._constellations = {
            item.constellation_id: item for item in spec.constellations
        }
        self._palettes = {item.palette_id: item for item in spec.palettes}
        self._selectivity = {
            item.selectivity_id: item for item in spec.selectivity
        }

    @staticmethod
    def _stable_index(text: str, length: int) -> int:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % length

    def recipe_catalog(self, *, variants_per_pair: int = 2) -> list[ViewerPersonaRecipe]:
        if variants_per_pair < 1:
            raise ValueError("variants_per_pair must be at least 1")
        recipes: list[ViewerPersonaRecipe] = []
        selectivity_ids = list(self._selectivity)
        for need in self.spec.needs:
            for constellation in self.spec.constellations:
                base = f"{self.spec.version}:{need.need_id}:{constellation.constellation_id}"
                for variant in range(variants_per_pair):
                    secondary_index = (
                        self._stable_index(base + ":secondary", len(need.compatible_secondary))
                        + variant
                    ) % len(need.compatible_secondary)
                    palette_index = (
                        self._stable_index(base + ":palette", len(need.compatible_palettes))
                        + variant
                    ) % len(need.compatible_palettes)
                    selectivity_index = (
                        self._stable_index(base + ":selectivity", len(selectivity_ids))
                        + variant
                    ) % len(selectivity_ids)
                    secondary = need.compatible_secondary[secondary_index]
                    palette = need.compatible_palettes[palette_index]
                    selectivity = selectivity_ids[selectivity_index]
                    recipe_id = (
                        f"{need.need_id}__{secondary}__"
                        f"{constellation.constellation_id}__{palette}__"
                        f"{selectivity}__v{variant + 1}"
                    )
                    recipes.append(
                        ViewerPersonaRecipe(
                            recipe_id=recipe_id,
                            main_need_id=need.need_id,
                            secondary_need_id=secondary,
                            constellation_id=constellation.constellation_id,
                            palette_id=palette,
                            selectivity_id=selectivity,
                        )
                    )
        return recipes

    def instantiate(
        self,
        recipe: ViewerPersonaRecipe,
        *,
        viewer_id: str,
        instance_seed: int,
    ) -> ViewerPersonaSnapshot:
        main = self._needs[recipe.main_need_id]
        secondary = self._needs[recipe.secondary_need_id]
        constellation = self._constellations[recipe.constellation_id]
        palette = self._palettes[recipe.palette_id]
        selectivity = self._selectivity[recipe.selectivity_id]
        rng = random.Random(f"{self.seed}:{instance_seed}:{recipe.recipe_id}")
        surface_context = rng.choice(self.spec.surface_contexts)
        high_and_medium = set(constellation.high_categories) | set(
            constellation.medium_categories
        )
        low_interests = (
            [
                category
                for category in self.spec.active_categories
                if category not in high_and_medium
            ]
            if self.spec.include_low_interests
            else []
        )
        return ViewerPersonaSnapshot(
            viewer_id=viewer_id,
            population_version=self.spec.version,
            recipe_id=recipe.recipe_id,
            main_need=main.label,
            secondary_need=secondary.label,
            interest_constellation=constellation.label,
            high_interests=constellation.high_categories,
            medium_interests=constellation.medium_categories,
            low_interests=low_interests,
            low_interest_rule=self.spec.low_interest_rule,
            interest_story=constellation.interest_story,
            topic_hooks=constellation.topic_hooks,
            browsing_motive=main.motive,
            preferred_presentations=palette.preferred_presentations,
            novelty_posture=palette.novelty_posture,
            title_posture=palette.title_posture,
            aversions=palette.aversions,
            selectivity=selectivity.label,
            browse_rule=selectivity.browse_rule,
            follow_rule=(
                f"主要看重：{main.follow_rule}；同时也希望：{secondary.follow_rule}"
            ),
            triple_rule=main.triple_rule,
            surface_context=surface_context,
        )

    def sample_population(self, *, count: int, seed: int) -> list[ViewerPersonaSnapshot]:
        if count < 1:
            raise ValueError("count must be positive")
        rng = random.Random(f"{self.seed}:population:{seed}")
        # Four variants guarantee that every need/constellation pair has one
        # recipe for each of the four stable selectivity modes.
        catalog = self.recipe_catalog(variants_per_pair=4)
        grouped: dict[tuple[str, str, str], list[ViewerPersonaRecipe]] = {}
        for recipe in catalog:
            grouped.setdefault(
                (
                    recipe.main_need_id,
                    recipe.constellation_id,
                    recipe.selectivity_id,
                ),
                [],
            ).append(recipe)

        need_ids = list(self.spec.main_need_weights)
        constellation_ids = list(self.spec.constellation_weights)
        selectivity_ids = list(self.spec.selectivity_weights)
        snapshots: list[ViewerPersonaSnapshot] = []
        for index in range(count):
            main_need = rng.choices(
                need_ids,
                weights=[self.spec.main_need_weights[item] for item in need_ids],
                k=1,
            )[0]
            constellation = rng.choices(
                constellation_ids,
                weights=[
                    self.spec.constellation_weights[item]
                    for item in constellation_ids
                ],
                k=1,
            )[0]
            selectivity = rng.choices(
                selectivity_ids,
                weights=[
                    self.spec.selectivity_weights[item] for item in selectivity_ids
                ],
                k=1,
            )[0]
            choices = grouped[(main_need, constellation, selectivity)]
            recipe = rng.choice(choices)
            snapshots.append(
                self.instantiate(
                    recipe,
                    viewer_id=f"viewer_{index + 1:05d}",
                    instance_seed=seed * 1_000_000 + index,
                )
            )
        return snapshots

    def session_context(
        self,
        *,
        viewer_id: str,
        simulation_day: int,
        session_index: int,
    ) -> ViewerSessionContext:
        """Sample one ephemeral, seeded attention constraint for a homepage."""

        if not self.spec.attention_modes:
            return ViewerSessionContext(
                mode_id="legacy_global_cap",
                label="沿用全局上限",
                click_cap=3,
                instruction="本版本没有独立注意力状态，沿用平台全局最多三条。",
                selection_contract="explicit_cap",
            )
        rng = random.Random(
            f"{self.seed}:attention:{viewer_id}:{simulation_day}:{session_index}"
        )
        mode = rng.choices(
            self.spec.attention_modes,
            weights=[item.weight for item in self.spec.attention_modes],
            k=1,
        )[0]
        return ViewerSessionContext(
            mode_id=mode.mode_id,
            label=mode.label,
            click_cap=mode.click_cap,
            instruction=mode.instruction,
            selection_contract=mode.selection_contract,
        )
