from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, model_validator


class ModelPrice(BaseModel):
    """Operator-supplied price; the application never searches for a replacement."""

    currency: Literal["USD", "CNY"] = "USD"
    input_per_million: Decimal = Field(ge=0)
    cached_input_per_million: Decimal | None = Field(default=None, ge=0)
    output_per_million: Decimal = Field(ge=0)
    note: str = "operator supplied"


class ModelSpec(BaseModel):
    provider: Literal["openrouter", "volcengine_ark", "openai_compatible"] = "openrouter"
    model_id: str = Field(min_length=1)
    api_key_env: str | None = "OPENROUTER_API_KEY"
    endpoint: str = "https://openrouter.ai/api/v1/chat/completions"
    supports_json_mode: bool = True
    # Generic Chat Completions routes differ in the accepted token cap and sampling fields.
    token_limit_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    supports_temperature: bool = True
    provider_order: list[str] = Field(default_factory=list)
    allow_provider_fallbacks: bool = True
    # Optional route-specific traffic shaping.  The simulation still owns the
    # global fan-out, while a fragile upstream route can receive a gentler
    # launch rate without making healthy models wait for it.
    max_in_flight: int | None = Field(default=None, ge=1, le=256)
    request_start_interval_ms: float = Field(default=0, ge=0, le=10_000)
    request_start_batch_size: int = Field(default=1, ge=1, le=256)
    # Creator prompts need a generous envelope for economical reasoning models,
    # while premium output-priced models can opt into a smaller, still useful
    # production limit without shrinking every other model.
    creator_max_output_tokens: int = Field(default=10_000, ge=512, le=10_000)
    pricing: ModelPrice

    @model_validator(mode="after")
    def validate_connection(self) -> "ModelSpec":
        endpoint = urlsplit(self.endpoint)
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
            raise ValueError("endpoint must be a complete http(s) Chat Completions URL")
        if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
            raise ValueError("endpoint must not contain credentials, query parameters or fragments")
        if self.api_key_env is None and self.provider != "openai_compatible":
            raise ValueError("only openai_compatible may explicitly disable API key authentication")
        if self.api_key_env is not None and not self.api_key_env.isidentifier():
            raise ValueError("api_key_env must name an environment variable, not contain a key")
        return self


class LLMBudget(BaseModel):
    """Small-run guardrails, not an accounting or billing system."""

    hard_limit: Decimal = Field(default=Decimal("0.10"), gt=0)
    max_cost_per_call: Decimal = Field(default=Decimal("0.02"), gt=0)
    hard_limit_by_currency: dict[Literal["USD", "CNY"], Decimal] = Field(
        default_factory=dict
    )
    max_cost_per_call_by_currency: dict[
        Literal["USD", "CNY"], Decimal
    ] = Field(default_factory=dict)
    max_attempts_per_call: int = Field(default=3, ge=1, le=5)
    timeout_seconds: float = Field(default=60.0, gt=0, le=300)

    @model_validator(mode="after")
    def validate_limits(self) -> "LLMBudget":
        if self.max_cost_per_call > self.hard_limit:
            raise ValueError("max_cost_per_call cannot exceed hard_limit")
        for currency, hard_limit in self.hard_limit_by_currency.items():
            if hard_limit <= 0:
                raise ValueError(f"{currency} hard limit must be positive")
            per_call = self.max_cost_per_call_by_currency.get(
                currency, self.max_cost_per_call
            )
            if per_call > hard_limit:
                raise ValueError(
                    f"{currency} max_cost_per_call cannot exceed hard_limit"
                )
        if any(value <= 0 for value in self.max_cost_per_call_by_currency.values()):
            raise ValueError("per-currency call limits must be positive")
        return self

    def hard_limit_for(self, currency: Literal["USD", "CNY"]) -> Decimal:
        return self.hard_limit_by_currency.get(currency, self.hard_limit)

    def max_cost_per_call_for(
        self, currency: Literal["USD", "CNY"]
    ) -> Decimal:
        return self.max_cost_per_call_by_currency.get(
            currency, self.max_cost_per_call
        )


class ReasoningPolicy(BaseModel):
    """Role defaults frozen with a run; individual diagnostics may override them."""

    creator_turn: bool = True
    population_homepage: bool = False
    population_post_click: bool = False
    reference_viewer: bool = False
    diagnostic_compare: bool = True


class LLMRuntimeConfig(BaseModel):
    models: dict[str, ModelSpec]
    budget: LLMBudget = Field(default_factory=LLMBudget)
    reasoning: ReasoningPolicy = Field(default_factory=ReasoningPolicy)

    @classmethod
    def from_yaml(cls, path: Path) -> "LLMRuntimeConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)

    def model(self, alias: str) -> ModelSpec:
        try:
            return self.models[alias]
        except KeyError as error:
            raise ValueError(f"unknown model alias: {alias}") from error
