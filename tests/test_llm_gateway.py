from __future__ import annotations

import json
import threading
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from ailiaili.llm.config import LLMBudget, ModelPrice, ModelSpec
from ailiaili.llm.gateway import (
    BudgetExceeded,
    ModelCallExhausted,
    ModelGateway,
    conservative_request_cost,
    read_evidence_ref,
)
from ailiaili.llm.provider import ProviderError, ProviderResult, ProviderUsage
from ailiaili.persistence.database import Database
from ailiaili.persistence.models import LLMCallRow, LLMAttemptRow, RunRow


class TinyOutput(BaseModel):
    ok: bool
    message: str


class FakeProvider:
    def __init__(self, responses: Sequence[ProviderResult | Exception]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(self, **kwargs: Any) -> ProviderResult:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ConcurrentFakeProvider:
    def __init__(self, *, delay_seconds: float = 0.2) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def complete(self, **_: Any) -> ProviderResult:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay_seconds)
        with self._lock:
            self.active -= 1
        return result(
            '{"ok":true,"message":"parallel"}',
            reported_cost="0.000001",
        )


def model_spec(
    *,
    input_rate: str = "1",
    cached_rate: str = "0.1",
    output_rate: str = "2",
    currency: str = "USD",
    model_id: str = "test/model",
) -> ModelSpec:
    return ModelSpec(
        model_id=model_id,
        pricing=ModelPrice(
            currency=currency,
            input_per_million=Decimal(input_rate),
            cached_input_per_million=Decimal(cached_rate),
            output_per_million=Decimal(output_rate),
        ),
    )


def result(
    content: str,
    *,
    input_tokens: int | None = 100,
    cached_tokens: int | None = 20,
    output_tokens: int | None = 10,
    reported_cost: str | None = None,
    raw_provider: str | None = "FakeRoute",
) -> ProviderResult:
    usage_raw = {}
    if input_tokens is not None and output_tokens is not None:
        usage_raw = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens or 0},
        }
        if reported_cost is not None:
            usage_raw["cost"] = float(reported_cost)
    return ProviderResult(
        content=content,
        request_id="request-test",
        resolved_model="test/model-resolved",
        usage=ProviderUsage(
            input_tokens=input_tokens,
            cached_input_tokens=cached_tokens,
            output_tokens=output_tokens,
            reported_cost=(
                Decimal(reported_cost) if reported_cost is not None else None
            ),
            cost_currency="USD" if reported_cost is not None else None,
            raw=usage_raw,
        ),
        raw={
            "id": "request-test",
            "content": content,
            "usage": usage_raw,
            **({"provider": raw_provider} if raw_provider is not None else {}),
        },
    )


def gateway(
    tmp_path: Path,
    provider: FakeProvider,
    *,
    hard_limit: str = "1",
    max_cost: str = "0.5",
    max_attempts: int = 3,
    evidence_mode: str = "full",
) -> tuple[ModelGateway, Database, Path]:
    run_dir = tmp_path / "run"
    database = Database(run_dir / "ailiaili.db")
    database.create_schema()
    with database.session() as session, session.begin():
        session.add(
            RunRow(
                run_id="test-run",
                seed=7,
                status="running",
                config_json={},
                last_completed_day=0,
            )
        )
    return (
        ModelGateway(
            database=database,
            run_dir=run_dir,
            budget=LLMBudget(
                hard_limit=Decimal(hard_limit),
                max_cost_per_call=Decimal(max_cost),
                max_attempts_per_call=max_attempts,
            ),
            provider=provider,
            evidence_mode=evidence_mode,
        ),
        database,
        run_dir,
    )


def invoke(
    model_gateway: ModelGateway,
    spec: ModelSpec,
    *,
    idempotency_key: str = "smoke-once",
) -> TinyOutput:
    return model_gateway.invoke_json(
        run_id="test-run",
        simulation_day=0,
        task_type="diagnostic_pk",
        agent_role="diagnostic",
        agent_id="smoke",
        cohort_role="reference",
        workload_scope="diagnostic",
        model_spec=spec,
        prompt_version="smoke-v1",
        idempotency_key=idempotency_key,
        messages=[
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Say hello."},
        ],
        output_model=TinyOutput,
        max_output_tokens=64,
        temperature=0,
        seed=7,
    )


def test_success_is_costed_and_idempotently_reused(tmp_path: Path) -> None:
    provider = FakeProvider([result('{"ok":true,"message":"hello"}')])
    model_gateway, database, run_dir = gateway(tmp_path, provider)
    spec = model_spec()

    first = invoke(model_gateway, spec)
    second = invoke(model_gateway, spec)

    assert first == second == TinyOutput(ok=True, message="hello")
    assert len(provider.calls) == 1
    assert provider.calls[0]["reasoning_enabled"] is None
    assert model_gateway.spent("test-run") == Decimal("0.000102000")
    with database.session() as session:
        call = session.scalar(select(LLMCallRow))
        attempt = session.scalar(select(LLMAttemptRow))
    assert call is not None and call.status == "succeeded"
    assert attempt is not None and attempt.cost_is_estimate is True
    assert attempt.cached_input_tokens == 20
    assert attempt.resolved_provider == "FakeRoute"
    request_payload = read_evidence_ref(run_dir, str(attempt.request_ref))
    request_text = json.dumps(request_payload)
    assert "Authorization" not in request_text
    assert "Bearer" not in request_text


def test_compact_evidence_omits_population_viewer_prompt_but_keeps_result(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [result('{"ok":true,"message":"compact viewer choice"}')]
    )
    model_gateway, database, run_dir = gateway(
        tmp_path,
        provider,
        evidence_mode="compact",
    )
    accepted = model_gateway.invoke_json(
        run_id="test-run",
        simulation_day=1,
        task_type="homepage_choice",
        agent_role="viewer",
        agent_id="viewer-1",
        cohort_role="population",
        workload_scope="main_world",
        model_spec=model_spec(),
        prompt_version="compact-test-v1",
        idempotency_key="compact-viewer-1",
        messages=[
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "A long 40-card feed would be here."},
        ],
        output_model=TinyOutput,
        max_output_tokens=64,
        temperature=0,
        seed=1,
    )

    assert accepted.message == "compact viewer choice"
    with database.session() as session:
        attempt = session.scalar(select(LLMAttemptRow))
    assert attempt is not None
    request = read_evidence_ref(run_dir, str(attempt.request_ref))
    response = read_evidence_ref(run_dir, str(attempt.response_ref))
    assert request["storage"] == "compact-evidence-v1"
    assert "messages" not in request
    assert request["message_count"] == 2
    assert response["storage"] == "compact-evidence-v1"
    assert response["content"] == '{"ok":true,"message":"compact viewer choice"}'
    assert attempt.usage_raw_json["prompt_tokens"] == 100


def test_compact_evidence_falls_back_to_accepted_provider_content(
    tmp_path: Path,
) -> None:
    provider_result = result('{"ok":true,"message":"provider-only content"}')
    # A few OpenAI-compatible adapters return usage and identifiers in `raw`
    # but expose the accepted text only through ProviderResult.content.
    provider_result = ProviderResult(
        content=provider_result.content,
        request_id=provider_result.request_id,
        resolved_model=provider_result.resolved_model,
        usage=provider_result.usage,
        raw={
            "id": "request-without-content",
            "model": "test/model-resolved",
            "usage": provider_result.usage.raw,
        },
    )
    provider = FakeProvider([provider_result])
    model_gateway, database, run_dir = gateway(
        tmp_path,
        provider,
        evidence_mode="compact",
    )

    model_gateway.invoke_json(
        run_id="test-run",
        simulation_day=1,
        task_type="homepage_choice",
        agent_role="viewer",
        agent_id="viewer-2",
        cohort_role="population",
        workload_scope="main_world",
        model_spec=model_spec(),
        prompt_version="compact-test-v1",
        idempotency_key="compact-viewer-provider-only",
        messages=[{"role": "user", "content": "Choose."}],
        output_model=TinyOutput,
        max_output_tokens=64,
        temperature=0,
        seed=2,
    )

    with database.session() as session:
        attempt = session.scalar(select(LLMAttemptRow))
    assert attempt is not None
    response = read_evidence_ref(run_dir, str(attempt.response_ref))
    assert response["content"] == '{"ok":true,"message":"provider-only content"}'


def test_two_hundred_concurrent_calls_share_budget_and_sharded_evidence(
    tmp_path: Path,
) -> None:
    provider = ConcurrentFakeProvider()
    run_dir = tmp_path / "concurrent"
    database = Database(run_dir / "ailiaili.db")
    database.create_schema()
    with database.session() as session, session.begin():
        session.add(
            RunRow(
                run_id="parallel-run",
                seed=7,
                status="running",
                config_json={},
                last_completed_day=0,
            )
        )
    model_gateway = ModelGateway(
        database=database,
        run_dir=run_dir,
        budget=LLMBudget(
            hard_limit=Decimal("1"),
            max_cost_per_call=Decimal("0.1"),
            max_attempts_per_call=1,
        ),
        provider=provider,
    )
    spec = model_spec(input_rate="0.01", output_rate="0.01")

    def one(index: int) -> TinyOutput:
        return model_gateway.invoke_json(
            run_id="parallel-run",
            simulation_day=1,
            task_type="parallel_test",
            agent_role="viewer",
            agent_id=f"viewer-{index}",
            cohort_role="population",
            workload_scope="diagnostic",
            model_spec=spec,
            prompt_version="parallel-v1",
            idempotency_key=f"parallel-{index}",
            messages=[
                {"role": "system", "content": "Return JSON."},
                {"role": "user", "content": f"Request {index}."},
            ],
            output_model=TinyOutput,
            max_output_tokens=64,
            temperature=0,
            seed=index,
        )

    with ThreadPoolExecutor(max_workers=200) as executor:
        outputs = list(executor.map(one, range(200)))

    assert len(outputs) == 200
    assert provider.calls == 200
    # The gateway's durable pre-dispatch bookkeeping intentionally meters the
    # burst, but the old eight-worker ceiling must no longer exist.
    assert provider.max_active > 16
    assert model_gateway.spent("parallel-run", "USD") == Decimal("0.000200000")
    request_shard = run_dir / "evidence" / "llm" / "day_001" / "request.jsonl"
    response_shard = run_dir / "evidence" / "llm" / "day_001" / "response.jsonl"
    assert len(request_shard.read_text(encoding="utf-8").splitlines()) == 200
    assert len(response_shard.read_text(encoding="utf-8").splitlines()) == 200
    assert not any(
        item.is_dir()
        for item in (run_dir / "evidence" / "llm" / "day_001").iterdir()
    )


def test_invalid_json_is_recorded_then_retried_with_feedback(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            result("not-json", input_tokens=50, cached_tokens=0, output_tokens=4),
            result("```json\n{\"ok\": true, \"message\": \"fixed\"}\n```"),
        ]
    )
    model_gateway, database, _ = gateway(tmp_path, provider)

    accepted = invoke(model_gateway, model_spec())

    assert accepted.message == "fixed"
    assert len(provider.calls) == 2
    assert [call["seed"] for call in provider.calls] == [7, 8]
    assert "上一次返回无法通过结构校验" in provider.calls[1]["messages"][-1][
        "content"
    ]
    assert "不要复用错误中指出的非法值" in provider.calls[1]["messages"][-1][
        "content"
    ]
    with database.session() as session:
        attempts = session.scalars(
            select(LLMAttemptRow).order_by(LLMAttemptRow.attempt_number)
        ).all()
    assert [attempt.status for attempt in attempts] == ["failed", "succeeded"]
    assert all(attempt.estimated_cost > 0 for attempt in attempts)
    assert attempts[0].response_ref is not None


def test_valid_json_with_trailing_explanation_is_accepted(tmp_path: Path) -> None:
    provider = FakeProvider(
        [result('{"ok":true,"message":"kept"}\n额外的一句解释。')]
    )
    model_gateway, _, _ = gateway(tmp_path, provider)

    accepted = invoke(model_gateway, model_spec())

    assert accepted.message == "kept"
    assert len(provider.calls) == 1


def test_provider_reported_cost_overrides_local_token_price(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            result(
                '{"ok":true,"message":"provider truth"}',
                reported_cost="0.00000731",
            )
        ]
    )
    model_gateway, database, _ = gateway(tmp_path, provider)

    accepted = invoke(model_gateway, model_spec())

    assert accepted.message == "provider truth"
    assert model_gateway.spent("test-run", "USD") == Decimal("0.000007310")
    with database.session() as session:
        attempt = session.scalar(select(LLMAttemptRow))
    assert attempt is not None
    assert attempt.cost_is_estimate is False
    assert attempt.currency == "USD"


def test_provider_without_wallet_cost_keeps_local_currency_estimate(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        [result('{"ok":true,"message":"token estimate"}', raw_provider=None)]
    )
    model_gateway, database, _ = gateway(tmp_path, provider)
    cny_spec = model_spec().model_copy(
        update={
            "provider": "volcengine_ark",
            "pricing": ModelPrice(
                currency="CNY",
                input_per_million=Decimal("3"),
                cached_input_per_million=Decimal("0.6"),
                output_per_million=Decimal("15"),
            ),
        }
    )

    accepted = invoke(model_gateway, cny_spec)

    assert accepted.message == "token estimate"
    with database.session() as session:
        attempt = session.scalar(select(LLMAttemptRow))
    assert attempt is not None
    assert attempt.cost_is_estimate is True
    assert attempt.currency == "CNY"
    assert attempt.resolved_provider == "volcengine_ark"
    assert attempt.estimated_cost > 0


def test_provider_failures_exhaust_without_fake_result(tmp_path: Path) -> None:
    provider = FakeProvider(
        [ProviderError("temporary failure"), ProviderError("still failing")]
    )
    model_gateway, database, _ = gateway(
        tmp_path, provider, max_attempts=2
    )

    with pytest.raises(ModelCallExhausted):
        invoke(model_gateway, model_spec())

    with database.session() as session:
        call = session.scalar(select(LLMCallRow))
        attempts = session.scalars(select(LLMAttemptRow)).all()
    assert call is not None and call.status == "exhausted"
    assert len(attempts) == 2
    assert all(attempt.status == "failed" for attempt in attempts)
    assert all(attempt.cost_is_estimate for attempt in attempts)
    assert model_gateway.spent("test-run") > 0
    assert all(len(call["messages"]) == 2 for call in provider.calls)


def test_rate_limit_retries_use_a_short_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[float] = []
    monkeypatch.setattr("ailiaili.llm.gateway.time.sleep", delays.append)
    provider = FakeProvider(
        [
            ProviderError("slow down", code="http_429"),
            result('{"ok":true,"message":"recovered"}'),
        ]
    )
    model_gateway, _, _ = gateway(tmp_path, provider)

    accepted = invoke(model_gateway, model_spec())

    assert all(len(call["messages"]) == 2 for call in provider.calls)

    assert accepted.message == "recovered"
    assert delays == [1.0]


def test_upstream_overload_retries_use_a_short_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[float] = []
    monkeypatch.setattr("ailiaili.llm.gateway.time.sleep", delays.append)
    provider = FakeProvider(
        [
            ProviderError("embedded overload", code="http_503"),
            result('{"ok":true,"message":"recovered"}'),
        ]
    )
    model_gateway, _, _ = gateway(tmp_path, provider)

    accepted = invoke(model_gateway, model_spec())

    assert accepted.message == "recovered"
    assert delays == [1.0]


def test_empty_content_preserves_usage_and_response_evidence(tmp_path: Path) -> None:
    provider = FakeProvider([result("", input_tokens=70, output_tokens=128)])
    model_gateway, database, _ = gateway(tmp_path, provider, max_attempts=1)

    with pytest.raises(ModelCallExhausted):
        invoke(model_gateway, model_spec())

    with database.session() as session:
        attempt = session.scalar(select(LLMAttemptRow))
    assert attempt is not None and attempt.status == "failed"
    assert attempt.input_tokens == 70
    assert attempt.output_tokens == 128
    assert attempt.response_ref is not None
    assert attempt.cost_is_estimate is True


def test_budget_pause_happens_before_provider_dispatch(tmp_path: Path) -> None:
    provider = FakeProvider([])
    model_gateway, database, _ = gateway(
        tmp_path,
        provider,
        hard_limit="0.01",
        max_cost="0.01",
    )

    with pytest.raises(BudgetExceeded):
        invoke(
            model_gateway,
            model_spec(input_rate="1000", cached_rate="1000", output_rate="1000"),
        )

    assert provider.calls == []
    with database.session() as session:
        call = session.scalar(select(LLMCallRow))
        attempts = session.scalars(select(LLMAttemptRow)).all()
    assert call is not None and call.status == "budget_paused"
    assert attempts == []


def test_preflight_cost_estimates_tokens_instead_of_raw_utf8_bytes() -> None:
    pricing = ModelPrice(
        currency="USD",
        input_per_million=Decimal("2"),
        cached_input_per_million=Decimal("0.2"),
        output_per_million=Decimal("10"),
    )
    messages = [{"role": "user", "content": "中文观察包" * 2000}]

    cost = conservative_request_cost(
        messages=messages,
        max_output_tokens=2800,
        pricing=pricing,
    )

    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    raw_byte_cost = (
        Decimal(len(serialized.encode("utf-8")) + 256) * Decimal("2")
        + Decimal(2800) * Decimal("10")
    ) / Decimal(1_000_000)
    # The reservation remains above the simple bytes/2.5 estimate because of
    # envelope headroom, but it must no longer pretend every UTF-8 byte is a
    # complete provider token.
    assert cost < raw_byte_cost
    assert cost > Decimal("0.037")


def test_budget_limits_are_enforced_per_currency(tmp_path: Path) -> None:
    provider = FakeProvider(
        [
            result('{"ok":true,"message":"usd"}'),
            result('{"ok":true,"message":"cny"}'),
        ]
    )
    run_dir = tmp_path / "multi-currency"
    database = Database(run_dir / "ailiaili.db")
    database.create_schema()
    with database.session() as session, session.begin():
        session.add(
            RunRow(
                run_id="test-run",
                seed=7,
                status="running",
                config_json={},
                last_completed_day=0,
            )
        )
    model_gateway = ModelGateway(
        database=database,
        run_dir=run_dir,
        budget=LLMBudget(
            hard_limit=Decimal("1"),
            max_cost_per_call=Decimal("0.5"),
            hard_limit_by_currency={
                "USD": Decimal("0.0005"),
                "CNY": Decimal("0.0005"),
            },
            max_cost_per_call_by_currency={
                "USD": Decimal("0.0005"),
                "CNY": Decimal("0.0005"),
            },
        ),
        provider=provider,
    )
    usd = model_spec(currency="USD", model_id="test/usd")
    cny = model_spec(currency="CNY", model_id="test/cny")

    assert invoke(model_gateway, usd, idempotency_key="usd-1").message == "usd"
    assert invoke(model_gateway, cny, idempotency_key="cny-1").message == "cny"
    with pytest.raises(BudgetExceeded):
        invoke(model_gateway, usd, idempotency_key="usd-2")

    assert len(provider.calls) == 2
    assert model_gateway.spent("test-run", "USD") == Decimal("0.000102000")
    assert model_gateway.spent("test-run", "CNY") == Decimal("0.000102000")
