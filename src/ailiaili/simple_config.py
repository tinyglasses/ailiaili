"""Compile one small, strict configuration into the existing run/persona formats.

Preparation is local only: no model calls, credential reads or database creation.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ailiaili.config import SimulationConfig
from ailiaili.llm.config import LLMBudget, LLMRuntimeConfig, ModelSpec
from ailiaili.persona import Category, CreatorPersona, CreatorPopulation, ViewerPopulationSpec

Alias = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]
Topic = Annotated[str, Field(min_length=1, max_length=80)]

CREATOR_STARTS = {
    "storyteller": dict(
        primary_drive="表达与作品", secondary_drive="同类与关系", creative_mode="叙事构建",
        instinct="从人物、具体处境和变化进入这个创作方向：",
        audience="愿意听完一个具体故事，也愿意给不同声音留一点空间的人。",
        evidence="区分事实、自己的推测和虚构设定，不假装经历过没有经历的事。",
        boundary="保留自己的表达方式，不把未发生的结果当作事实。",
        tension="既希望自己的作品被更多人看见，也希望保留原本想说的东西。"),
    "explainer": dict(
        primary_drive="求真与掌握", secondary_drive="服务与效用", creative_mode="原理解释",
        instinct="用可核对的例子拆清概念与因果，探索这个方向：",
        audience="对问题有好奇心，愿意了解原因，也希望知道结论适用条件的人。",
        evidence="优先解释依据与限制，区分证据和推测，遇到不确定的地方明确保留。",
        boundary="不为了一个漂亮结论抹掉反例，也不把猜测包装成已经证实。",
        tension="想把问题讲得容易懂，又担心省略了真正决定结论的条件。"),
    "maker": dict(
        primary_drive="服务与效用", secondary_drive="求真与掌握", creative_mode="动手实验",
        instinct="设计可以复核的步骤和失败条件，探索这个方向：",
        audience="喜欢看过程、愿意尝试小改动，也想知道什么情况下会失败的人。",
        evidence="明确区分方案推演、虚构演示与实际测试，不把未做过的实验说成亲手完成。",
        boundary="不捏造测试经历与实测数据，也不把一次假设当作普遍有效的方法。",
        tension="希望方案足够简单便于尝试，又不愿回避材料、时间和失败的成本。"),
    "observer": dict(
        primary_drive="同类与关系", secondary_drive="表达与作品", creative_mode="陪伴记录",
        instinct="从被忽视的生活细节和人的感受进入这个方向：",
        audience="愿意停下来看看日常小事，希望自己的经验被认真理解的人。",
        evidence="把观察、解释和虚构分开，不替不认识的人编造真实经历。",
        boundary="不把他人的处境当猎奇材料，也不把角色的个人感受说成所有人的答案。",
        tension="想让观众产生共鸣，又不想为了共鸣把复杂处境写成一个现成口号。"),
}


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SimpleCreator(StrictConfig):
    name: str = Field(min_length=2, max_length=24)
    category: Category
    personality: str = Field(min_length=2, max_length=100)
    goal: str = Field(min_length=4, max_length=100)
    topics: list[Topic] = Field(min_length=1, max_length=2)
    approach: Literal["storyteller", "explainer", "maker", "observer"] = "storyteller"
    audience: str | None = Field(default=None, min_length=8, max_length=140)
    boundary: str | None = Field(default=None, min_length=8, max_length=160)
    tension: str | None = Field(default=None, min_length=10, max_length=200)
    model: Alias | None = None


class SimpleViewers(StrictConfig):
    count: int = Field(default=6, ge=1)
    interests: list[Category] = Field(default_factory=list)
    personality: str = Field(default="喜欢具体的小事和展开充分的故事。", min_length=2, max_length=80)
    model: Alias | None = None


class SimpleModel(StrictConfig):
    provider: Literal["openrouter", "volcengine_ark", "openai_compatible"] = "openrouter"
    model_id: str = Field(min_length=1)
    endpoint: str | None = None
    api_key_env: str | None = "AILIAILI_MODEL_API_KEY"
    supports_json_mode: bool = True
    supports_temperature: bool = True
    token_limit_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    currency: Literal["USD", "CNY"] = "USD"
    input_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output_per_million: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    def runtime_spec(self) -> ModelSpec:
        if self.input_per_million is None or self.output_per_million is None:
            raise ValueError("LLM mode needs explicit input_per_million and output_per_million prices; local free inference may use 0")
        if self.model_id.startswith("REPLACE_"):
            raise ValueError("replace the placeholder model_id before preparing LLM mode")
        endpoint = self.endpoint
        if endpoint is None:
            if self.provider == "openai_compatible":
                raise ValueError("openai_compatible requires the complete endpoint, including /chat/completions")
            endpoint = {
                "openrouter": "https://openrouter.ai/api/v1/chat/completions",
                "volcengine_ark": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            }[self.provider]
        return ModelSpec(
            provider=self.provider, model_id=self.model_id, endpoint=endpoint,
            api_key_env=self.api_key_env, supports_json_mode=self.supports_json_mode,
            supports_temperature=self.supports_temperature,
            token_limit_parameter=self.token_limit_parameter,
            max_in_flight=1, request_start_interval_ms=1000,
            creator_max_output_tokens=2048, allow_provider_fallbacks=False,
            pricing={"currency": self.currency, "input_per_million": self.input_per_million,
                     "output_per_million": self.output_per_million},
        )


class SimpleBudget(LLMBudget):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    max_attempts_per_call: int = Field(default=1, ge=1, le=5)


class SimpleWorld(StrictConfig):
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=80)
    mode: Literal["stub", "llm"] = "stub"
    seed: int = 42
    days: int = Field(default=5, ge=1)
    world_anchor_date: date = date(2026, 1, 1)
    categories: list[Category] = Field(min_length=1, max_length=30)
    creators: list[SimpleCreator] = Field(min_length=1, max_length=999)
    viewers: SimpleViewers = Field(default_factory=SimpleViewers)
    model: Alias | None = None
    models: dict[Alias, SimpleModel] = Field(default_factory=dict)
    budget: SimpleBudget = Field(default_factory=SimpleBudget)
    publish_every: int = Field(default=2, ge=1)
    homepage_size: int | None = Field(default=None, ge=1)
    recommendation: Literal["balanced", "exploratory"] = "balanced"
    question: str | None = Field(default=None, min_length=4, max_length=300)
    output_root: Path = Path("runs")

    @model_validator(mode="after")
    def validate_references(self) -> "SimpleWorld":
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("categories must be unique")
        if len({creator.name for creator in self.creators}) != len(self.creators):
            raise ValueError("creator names must be unique")
        used = {c.category for c in self.creators} | set(self.viewers.interests)
        unknown = used - set(self.categories)
        if unknown:
            raise ValueError("unknown categories: " + ", ".join(sorted(unknown)))
        if len(set(self.viewers.interests)) != len(self.viewers.interests):
            raise ValueError("viewer interests must be unique")
        aliases = [self.model, self.viewers.model, *(c.model for c in self.creators)]
        unknown_models = {alias for alias in aliases if alias and alias not in self.models}
        if unknown_models:
            raise ValueError("unknown model aliases: " + ", ".join(sorted(unknown_models)))
        if self.mode == "llm":
            if not (self.viewers.model or self.model) or any(not (c.model or self.model) for c in self.creators):
                raise ValueError("LLM mode requires a default model or a model for every creator and the viewers")
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> "SimpleWorld":
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def creator_persona(creator: SimpleCreator, index: int) -> CreatorPersona:
    start = CREATOR_STARTS[creator.approach]
    return CreatorPersona(
        creator_id=f"creator_{index + 1:03d}", display_name=creator.name,
        primary_category=creator.category,
        public_bio=f"创作方向：{creator.goal}"[:100],
        origin=f"我开始这个账号，是因为一直想做这样的内容：{creator.goal}",
        primary_drive=start["primary_drive"], secondary_drive=start["secondary_drive"],
        imagined_audience=creator.audience or start["audience"],
        creative_mode=start["creative_mode"], creative_instinct=f"{start['instinct']}{creator.goal}",
        voice=f"表达习惯：{creator.personality}",
        evidence_stance=start["evidence"],
        packaging_instinct="用具体的事物说明作品内容，标题与正文保持一致。",
        platform_relationship="可以从平台反馈中学习，但不把每次创作都变成追逐指标。",
        identity_floor=creator.boundary or start["boundary"],
        productive_tension=creator.tension or start["tension"],
        blind_spot="这是读者自行配置的角色，具体盲点尚未由运行观察得到。",
        initial_intuitions=[f"我想试着从这个题材开始：{topic}" for topic in creator.topics],
        ecology_roles=["自定义创作者"], temperament_tags=["自定义表达", "独立账号"],
    )


def viewer_population(world: SimpleWorld) -> ViewerPopulationSpec:
    template = Path(__file__).parent / "presets/viewers.yaml"
    raw = yaml.safe_load(template.read_text(encoding="utf-8"))
    interests = world.viewers.interests or world.categories
    raw["version"] = "simple-audience-v1"
    raw["constellations"] = [
        {"constellation_id": f"custom_{i:03d}", "label": f"兴趣组{i + 1}",
         "high_categories": [category],
         "medium_categories": [c for c in world.categories if c != category][:4],
         "interest_story": f"我偏爱{category}。我的观看偏好是：{world.viewers.personality}",
         "topic_hooks": [f"{category}里的具体问题", f"{category}里的意外故事"]}
        for i, category in enumerate(interests)
    ]
    raw["constellation_weights"] = {c["constellation_id"]: 1 for c in raw["constellations"]}
    return ViewerPopulationSpec.model_validate(raw)


def compile_world(world: SimpleWorld, output: Path) -> dict[str, dict]:
    """Return all validated documents before touching the output directory."""
    creators = [creator_persona(c, i) for i, c in enumerate(world.creators)]
    population = CreatorPopulation(
        version="simple-creators-v1", expected_count=len(creators),
        category_targets=dict(Counter(c.primary_category for c in creators)), creators=creators,
    )
    viewers = viewer_population(world)
    payload = dict(
        run_id=world.run_id, seed=world.seed, world_anchor_date=str(world.world_anchor_date),
        days=world.days, execution_mode=world.mode, creator_count=len(creators),
        viewer_count=world.viewers.count, homepage_size=world.homepage_size or min(6, len(creators)),
        max_clicks=max(mode.click_cap for mode in viewers.attention_modes),
        publish_interval_days=world.publish_every, initial_videos_per_creator=1,
        output_root=world.output_root, max_concurrency=1, request_start_interval_ms=1000,
        exploit_ratio=0.4 if world.recommendation == "exploratory" else 0.8,
        cold_ratio=0.1, explore_ratio=0.5 if world.recommendation == "exploratory" else 0.1,
        experiment_purpose=world.question,
    )
    result = {"creators.yaml": population.model_dump(mode="json"),
              "viewers.yaml": viewers.model_dump(mode="json")}
    if world.mode == "stub":
        payload.update(
            stub_creators=[dict(name=c.name, category=c.category, goal=c.goal,
                                style=c.personality, topics=c.topics) for c in world.creators],
            stub_viewer_interests=world.viewers.interests or world.categories,
        )
    else:
        creator_aliases = [c.model or world.model for c in world.creators]
        viewer_alias = world.viewers.model or world.model
        aliases = list(dict.fromkeys([*creator_aliases, viewer_alias]))
        models = {}
        for alias in aliases:
            try:
                models[alias] = world.models[alias].runtime_spec()
            except ValueError as exc:
                raise ValueError(f"models.{alias}: {exc}") from exc
        runtime = LLMRuntimeConfig(
            models=models, budget=world.budget,
            reasoning={"creator_turn": False, "population_homepage": False,
                       "population_post_click": False, "reference_viewer": False},
        )
        result["models.yaml"] = runtime.model_dump(mode="json")
        payload.update(
            creator_persona_path=(output / "creators.yaml").resolve(),
            viewer_persona_path=(output / "viewers.yaml").resolve(),
            llm_runtime_path=(output / "models.yaml").resolve(),
            creator_model_aliases=creator_aliases, viewer_model_aliases=[viewer_alias],
        )
        # Freeze prepared dependencies as well as the main run configuration.
        # Editing a source simple-config creates a new experiment, not a silent resume change.
        payload["dependency_sha256"] = {
            field: hashlib.sha256(yaml_text(result[filename]).encode("utf-8")).hexdigest()
            for field, filename in (("creator_persona_path", "creators.yaml"),
                                    ("viewer_persona_path", "viewers.yaml"),
                                    ("llm_runtime_path", "models.yaml"))
        }
    result["world.yaml"] = SimulationConfig.model_validate(payload).model_dump(mode="json")
    return result


def yaml_text(value: dict) -> str:
    return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)


def prepare_world(source: Path, output: Path | None = None, *, check_only: bool = False) -> dict:
    world = SimpleWorld.from_yaml(source)
    output = output or Path("configs/local") / world.run_id
    documents = compile_world(world, output)
    if not check_only:
        if output.exists():
            raise ValueError("output already exists; choose a new directory so existing experiments stay unchanged")
        output.mkdir(parents=True)
        for filename, value in documents.items():
            (output / filename).write_text(yaml_text(value), encoding="utf-8", newline="\n")
        (output / "README.md").write_text(
            "# Prepared world\n\n"
            "The source configuration has been expanded locally; no model was called.\n"
            "Run world.yaml with ailiaili run --config. Paths to persona/model files are local to this preparation.\n"
            "Stub mode uses fixed templates; personality text is stored but is not interpreted by a language model.\n"
            "LLM mode uses the configured model services and budget. Keys are read from the named environment variables at run time.\n"
            "To change a prepared experiment, edit the small source YAML, use a new run_id and a new output directory.\n",
            encoding="utf-8",
        )
    return {"run_id": world.run_id, "mode": world.mode, "creators": len(world.creators),
            "viewers": world.viewers.count, "categories": world.categories,
            "config": str(output / "world.yaml"), "check_only": check_only}
