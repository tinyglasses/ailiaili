from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest

from ailiaili.llm.config import ModelPrice, ModelSpec
from ailiaili.llm.provider import (
    OpenRouterProvider,
    ProviderError,
    RoutingChatProvider,
    VolcengineArkProvider,
)


def test_openrouter_promotes_embedded_503_to_retryable_provider_error(
    monkeypatch,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"error": {"message": "Overloaded", "code": 503}},
        )

    monkeypatch.setenv("OPENROUTER_API_KEY", "temporary-test-key")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(client=client)
    spec = ModelSpec(
        model_id="anthropic/claude-sonnet-5",
        pricing=ModelPrice(
            currency="USD",
            input_per_million=Decimal("2"),
            output_per_million=Decimal("10"),
        ),
    )

    with pytest.raises(ProviderError) as captured:
        provider.complete(
            spec=spec,
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=16,
            temperature=0,
            seed=1,
            reasoning_enabled=True,
        )
    client.close()

    assert captured.value.code == "http_503"
    assert "Overloaded" in str(captured.value)


def test_request_start_pacer_smooths_a_concurrent_burst(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "temporary-test-key")
    starts: list[float] = []
    lock = threading.Lock()

    def handler(_: httpx.Request) -> httpx.Response:
        with lock:
            starts.append(time.perf_counter())
        return httpx.Response(
            200,
            json={
                "id": "probe",
                "model": "test/model",
                "provider": "test-route",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 3,
                    "cost": 0.000001,
                },
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(
        client=client,
        max_connections=4,
        request_start_interval_ms=20,
    )
    spec = ModelSpec(
        model_id="test/model",
        endpoint="https://example.test/chat",
        pricing=ModelPrice(
            currency="USD",
            input_per_million=Decimal("0.01"),
            output_per_million=Decimal("0.01"),
        ),
    )

    def invoke(_: int) -> None:
        provider.complete(
            spec=spec,
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=16,
            temperature=0,
            seed=1,
            reasoning_enabled=False,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(invoke, range(4)))
    client.close()

    ordered = sorted(starts)
    assert len(ordered) == 4
    # Leave scheduler tolerance while proving calls were not released together.
    assert all(right - left >= 0.015 for left, right in zip(ordered, ordered[1:]))


def test_request_start_pacer_releases_small_batches(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "temporary-test-key")
    starts: list[float] = []
    lock = threading.Lock()

    def handler(_: httpx.Request) -> httpx.Response:
        with lock:
            starts.append(time.perf_counter())
        return httpx.Response(
            200,
            json={
                "id": "probe",
                "model": "test/model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(
        client=client,
        max_connections=12,
        request_start_interval_ms=50,
        request_start_batch_size=5,
    )
    spec = ModelSpec(
        model_id="test/model",
        endpoint="https://example.test/chat",
        pricing=ModelPrice(
            currency="USD",
            input_per_million=Decimal("0.01"),
            output_per_million=Decimal("0.01"),
        ),
    )

    def invoke(_: int) -> None:
        provider.complete(
            spec=spec,
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=16,
            temperature=0,
            seed=1,
            reasoning_enabled=False,
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        list(executor.map(invoke, range(12)))
    client.close()

    ordered = sorted(starts)
    assert len(ordered) == 12
    # Five launch immediately, five around +50 ms, and the final two around
    # +100 ms.  Scheduler tolerance keeps this robust on busy CI hosts.
    assert ordered[5] - ordered[0] >= 0.035
    assert ordered[10] - ordered[0] >= 0.080
    assert provider.network_peak_in_flight >= 1


def test_model_specific_pacer_does_not_delay_another_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "temporary-test-key")
    starts: list[tuple[str, float]] = []
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        model = json.loads(request.content)["model"]
        with lock:
            starts.append((model, time.perf_counter()))
        return httpx.Response(
            200,
            json={
                "id": "probe",
                "model": model,
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenRouterProvider(client=client, max_connections=8)
    price = ModelPrice(
        currency="USD",
        input_per_million=Decimal("0.01"),
        output_per_million=Decimal("0.01"),
    )
    slow = ModelSpec(
        model_id="test/slow",
        endpoint="https://example.test/chat",
        request_start_interval_ms=50,
        request_start_batch_size=1,
        pricing=price,
    )
    fast = ModelSpec(
        model_id="test/fast",
        endpoint="https://example.test/chat",
        pricing=price,
    )

    def invoke(spec: ModelSpec) -> None:
        provider.complete(
            spec=spec,
            messages=[{"role": "user", "content": "hello"}],
            max_output_tokens=16,
            temperature=0,
            seed=1,
            reasoning_enabled=False,
        )

    specs = [slow, slow, slow, slow, fast, fast]
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(invoke, specs))
    client.close()

    slow_starts = sorted(t for model, t in starts if model == "test/slow")
    fast_starts = sorted(t for model, t in starts if model == "test/fast")
    assert all(
        right - left >= 0.035
        for left, right in zip(slow_starts, slow_starts[1:])
    )
    assert fast_starts[-1] - fast_starts[0] < 0.035


def test_volcengine_ark_uses_thinking_contract_without_openrouter_fields(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured["headers"] = dict(request.headers)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "ark-probe",
                "model": "doubao-seed-2-1-turbo-260628",
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {
                    "prompt_tokens": 24,
                    "completion_tokens": 9,
                    "prompt_tokens_details": {"cached_tokens": 7},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            },
        )

    monkeypatch.setenv("ARK_API_KEY", "temporary-test-key")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = VolcengineArkProvider(client=client)
    spec = ModelSpec(
        provider="volcengine_ark",
        model_id="doubao-seed-2-1-turbo-260628",
        api_key_env="ARK_API_KEY",
        endpoint="https://ark.test/api/v3/chat/completions",
        pricing=ModelPrice(
            currency="CNY",
            input_per_million=Decimal("3"),
            cached_input_per_million=Decimal("0.6"),
            output_per_million=Decimal("15"),
        ),
    )

    result = provider.complete(
        spec=spec,
        messages=[{"role": "user", "content": "hello"}],
        max_output_tokens=2048,
        temperature=0.8,
        seed=7,
        reasoning_enabled=True,
    )
    client.close()

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert "reasoning" not in payload
    assert "provider" not in payload
    assert "seed" not in payload
    assert result.usage.cached_input_tokens == 7
    assert result.usage.reasoning_tokens == 3
    assert result.usage.reported_cost is None


def test_routing_provider_selects_provider_from_model_spec(monkeypatch) -> None:
    class FakeRoute:
        def __init__(self, label: str):
            self.label = label
            self.calls = 0

        def complete(self, **_: object):
            self.calls += 1
            return self.label

        def close(self) -> None:
            pass

    openrouter = FakeRoute("openrouter")
    ark = FakeRoute("ark")
    router = RoutingChatProvider(
        openrouter=openrouter,  # type: ignore[arg-type]
        volcengine_ark=ark,  # type: ignore[arg-type]
    )
    price = ModelPrice(
        currency="USD",
        input_per_million=Decimal("0.1"),
        output_per_million=Decimal("0.2"),
    )
    common = {
        "messages": [{"role": "user", "content": "hello"}],
        "max_output_tokens": 16,
        "temperature": 0,
        "seed": 1,
        "reasoning_enabled": False,
    }

    assert router.complete(
        spec=ModelSpec(model_id="or/model", pricing=price), **common
    ) == "openrouter"
    assert router.complete(
        spec=ModelSpec(
            provider="volcengine_ark",
            model_id="ark/model",
            pricing=price,
        ),
        **common,
    ) == "ark"
    assert openrouter.calls == 1
    assert ark.calls == 1
