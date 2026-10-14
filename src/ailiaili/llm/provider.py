from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import httpx

from ailiaili.llm.config import ModelSpec


class ProviderError(RuntimeError):
    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        retry_after_seconds: float | None = None,
    ):
        super().__init__(message)
        if code is not None:
            self.code = code
        self.retry_after_seconds = retry_after_seconds


class MissingCredential(ProviderError):
    code = "missing_credential"


@dataclass(frozen=True)
class ProviderUsage:
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    reported_cost: Decimal | None = None
    cost_currency: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderResult:
    content: str
    request_id: str | None
    resolved_model: str | None
    usage: ProviderUsage
    raw: dict[str, Any]


@dataclass
class _ModelTrafficState:
    dispatch_lock: threading.Lock = field(default_factory=threading.Lock)
    next_dispatch_at: float = 0.0
    dispatches_in_batch: int = 0
    in_flight: threading.BoundedSemaphore | None = None


class ChatProvider(Protocol):
    def complete(
        self,
        *,
        spec: ModelSpec,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        temperature: float,
        seed: int | None,
        reasoning_enabled: bool | None,
    ) -> ProviderResult: ...


class OpenRouterProvider:
    """Minimal non-streaming OpenRouter adapter. Authorization is never persisted."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        max_connections: int = 200,
        max_keepalive_connections: int | None = None,
        request_start_interval_ms: float = 0,
        request_start_batch_size: int = 1,
        client: httpx.Client | None = None,
    ):
        if max_connections < 1:
            raise ValueError("max_connections must be positive")
        if request_start_interval_ms < 0:
            raise ValueError("request_start_interval_ms cannot be negative")
        if request_start_batch_size < 1:
            raise ValueError("request_start_batch_size must be positive")
        self.timeout_seconds = timeout_seconds
        self.request_start_interval_seconds = request_start_interval_ms / 1000
        self.request_start_batch_size = request_start_batch_size
        self._dispatch_lock = threading.Lock()
        self._next_dispatch_at = 0.0
        self._dispatches_in_batch = 0
        self._network_lock = threading.Lock()
        self._network_active = 0
        self._network_peak = 0
        self._model_states_lock = threading.Lock()
        self._model_states: dict[tuple[object, ...], _ModelTrafficState] = {}
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=timeout_seconds,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=(
                    max_keepalive_connections
                    if max_keepalive_connections is not None
                    else max_connections
                ),
            ),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OpenRouterProvider":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _pace_request_start(self) -> None:
        interval = self.request_start_interval_seconds
        if interval <= 0:
            return
        with self._dispatch_lock:
            now = time.perf_counter()
            # Reserve one position in a virtual launch batch.  Concurrent
            # callers may reserve future batches immediately, then sleep
            # outside the lock.  Long idle gaps reset the schedule instead of
            # accumulating an unlimited burst allowance.
            if (
                self._next_dispatch_at == 0
                or now >= self._next_dispatch_at + interval
            ):
                self._next_dispatch_at = now
                self._dispatches_in_batch = 0
            if self._dispatches_in_batch >= self.request_start_batch_size:
                self._next_dispatch_at += interval
                self._dispatches_in_batch = 0
            dispatch_at = self._next_dispatch_at
            self._dispatches_in_batch += 1
        delay = dispatch_at - now
        if delay > 0:
            time.sleep(delay)

    def _model_traffic_state(self, spec: ModelSpec) -> _ModelTrafficState:
        key: tuple[object, ...] = (
            spec.model_id,
            tuple(spec.provider_order),
            spec.max_in_flight,
            spec.request_start_interval_ms,
            spec.request_start_batch_size,
        )
        with self._model_states_lock:
            state = self._model_states.get(key)
            if state is None:
                state = _ModelTrafficState(
                    in_flight=(
                        threading.BoundedSemaphore(spec.max_in_flight)
                        if spec.max_in_flight is not None
                        else None
                    )
                )
                self._model_states[key] = state
            return state

    def _enter_model_request(self, spec: ModelSpec) -> _ModelTrafficState:
        state = self._model_traffic_state(spec)
        if state.in_flight is not None:
            state.in_flight.acquire()
        interval = spec.request_start_interval_ms / 1000
        if interval > 0:
            with state.dispatch_lock:
                now = time.perf_counter()
                if (
                    state.next_dispatch_at == 0
                    or now >= state.next_dispatch_at + interval
                ):
                    state.next_dispatch_at = now
                    state.dispatches_in_batch = 0
                if state.dispatches_in_batch >= spec.request_start_batch_size:
                    state.next_dispatch_at += interval
                    state.dispatches_in_batch = 0
                dispatch_at = state.next_dispatch_at
                state.dispatches_in_batch += 1
            delay = dispatch_at - now
            if delay > 0:
                time.sleep(delay)
        return state

    @staticmethod
    def _exit_model_request(state: _ModelTrafficState) -> None:
        if state.in_flight is not None:
            state.in_flight.release()

    @property
    def network_peak_in_flight(self) -> int:
        """Peak requests actually inside the HTTP transport."""

        with self._network_lock:
            return self._network_peak

    def reset_network_peak(self) -> None:
        with self._network_lock:
            if self._network_active:
                raise RuntimeError("cannot reset while HTTP requests are active")
            self._network_peak = 0

    def _enter_network_request(self) -> None:
        with self._network_lock:
            self._network_active += 1
            self._network_peak = max(self._network_peak, self._network_active)

    def _exit_network_request(self) -> None:
        with self._network_lock:
            self._network_active -= 1

    def complete(
        self,
        *,
        spec: ModelSpec,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        temperature: float,
        seed: int | None,
        reasoning_enabled: bool | None,
    ) -> ProviderResult:
        api_key = os.environ.get(spec.api_key_env)
        if not api_key:
            raise MissingCredential(
                f"environment variable {spec.api_key_env} is not set"
            )

        payload: dict[str, Any] = {
            "model": spec.model_id,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if seed is not None:
            payload["seed"] = seed
        if spec.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}
        if spec.provider_order:
            payload["provider"] = {
                "order": spec.provider_order,
                "allow_fallbacks": spec.allow_provider_fallbacks,
            }
        if reasoning_enabled is not None:
            payload["reasoning"] = {"enabled": reasoning_enabled}

        model_state = self._enter_model_request(spec)
        try:
            self._pace_request_start()
            # A shared Client is intentionally used here.  The old one-shot
            # httpx.post path rebuilt TCP/TLS state for every logical action
            # and erased most of the benefit of high viewer concurrency.
            self._enter_network_request()
            try:
                response = self._client.post(
                    spec.endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "X-OpenRouter-Title": "AiliAili",
                    },
                    json=payload,
                )
            finally:
                self._exit_network_request()
        except httpx.TimeoutException as error:
            raise ProviderError("OpenRouter request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderError("OpenRouter request could not be sent") from error
        finally:
            self._exit_model_request(model_state)

        if response.status_code >= 400:
            # Provider bodies are useful for diagnosis, but cap them and never
            # include request headers, which contain the credential.
            detail = response.text[:1000].replace("\n", " ")
            error = ProviderError(
                f"OpenRouter returned HTTP {response.status_code}: {detail}",
                code=f"http_{response.status_code}",
                retry_after_seconds=_retry_after_seconds(
                    response.headers.get("retry-after")
                ),
            )
            raise error

        try:
            raw = response.json()
        except ValueError as error:
            raise ProviderError(
                "OpenRouter returned a non-JSON success response"
            ) from error

        # OpenRouter can occasionally return an upstream provider failure as
        # HTTP 200 with only {"error": {"code": 503, ...}}.  Treat this as the
        # retryable upstream status it represents instead of misclassifying it
        # as a malformed success envelope and immediately retrying in a burst.
        embedded_error = raw.get("error") if isinstance(raw, dict) else None
        if isinstance(embedded_error, dict):
            embedded_code = embedded_error.get("code")
            embedded_message = str(
                embedded_error.get("message") or "unknown upstream error"
            )[:500]
            try:
                status_code = int(embedded_code)
            except (TypeError, ValueError):
                status_code = 0
            code = (
                f"http_{status_code}"
                if 400 <= status_code <= 599
                else "provider_error"
            )
            raise ProviderError(
                "OpenRouter returned an embedded upstream error "
                f"({embedded_code}): {embedded_message}",
                code=code,
            )

        try:
            choices = raw["choices"]
            content = choices[0]["message"]["content"]
            # Empty content is a valid provider envelope when a reasoning model
            # spends its entire output allowance before reaching the answer.
            # Return it so the gateway can preserve usage and raw evidence,
            # then fail business validation in the normal retry path.
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise ValueError("assistant content is not text")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ProviderError("OpenRouter returned an invalid response envelope") from error

        usage_raw = raw.get("usage") or {}
        prompt_details = usage_raw.get("prompt_tokens_details") or {}
        completion_details = usage_raw.get("completion_tokens_details") or {}
        return ProviderResult(
            content=content,
            request_id=raw.get("id"),
            resolved_model=raw.get("model"),
            usage=ProviderUsage(
                input_tokens=_optional_int(usage_raw.get("prompt_tokens")),
                cached_input_tokens=_optional_int(
                    prompt_details.get("cached_tokens")
                ),
                output_tokens=_optional_int(usage_raw.get("completion_tokens")),
                reasoning_tokens=_optional_int(
                    completion_details.get("reasoning_tokens")
                ),
                # OpenRouter's usage.cost is the authoritative wallet charge.
                # Token prices remain useful only for preflight reservation or
                # as an explicitly marked fallback when this field is absent.
                reported_cost=_optional_decimal(usage_raw.get("cost")),
                cost_currency="USD" if usage_raw.get("cost") is not None else None,
                raw=usage_raw,
            ),
            raw=raw,
        )


class VolcengineArkProvider(OpenRouterProvider):
    """OpenAI-compatible Volcengine Ark adapter for Creator calls.

    Ark deliberately has its own adapter instead of pretending to be an
    OpenRouter route: it uses a different thinking field, has no OpenRouter
    provider-routing object, and does not report an authoritative wallet cost
    in the Chat Completions response. The shared gateway still owns retries,
    structured-output validation, evidence, and the cost ledger.
    """

    label = "Volcengine Ark"

    def complete(
        self,
        *,
        spec: ModelSpec,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        temperature: float,
        seed: int | None,
        reasoning_enabled: bool | None,
    ) -> ProviderResult:
        api_key = os.environ.get(spec.api_key_env) if spec.api_key_env else None
        if spec.api_key_env is not None and not api_key:
            raise MissingCredential(
                f"environment variable {spec.api_key_env} is not set"
            )

        generic = spec.provider == "openai_compatible"
        payload: dict[str, Any] = {
            "model": spec.model_id,
            "messages": messages,
            (spec.token_limit_parameter if generic else "max_tokens"): max_output_tokens,
            "stream": False,
        }
        if not generic or spec.supports_temperature:
            payload["temperature"] = temperature
        # Ark's Chat Completions API uses `thinking.type`, not OpenRouter's
        # `reasoning.enabled`. Creator calls explicitly enable it.
        if not generic and reasoning_enabled is not None:
            payload["thinking"] = {
                "type": "enabled" if reasoning_enabled else "disabled"
            }
        if spec.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        model_state = self._enter_model_request(spec)
        try:
            self._pace_request_start()
            self._enter_network_request()
            try:
                response = self._client.post(
                    spec.endpoint,
                    headers={"Content-Type": "application/json",
                             **({"Authorization": f"Bearer {api_key}"} if api_key else {})},
                    json=payload,
                )
            finally:
                self._exit_network_request()
        except httpx.TimeoutException as error:
            raise ProviderError(f"{self.label} request timed out") from error
        except httpx.HTTPError as error:
            raise ProviderError(f"{self.label} request could not be sent") from error
        finally:
            self._exit_model_request(model_state)

        if response.status_code >= 400:
            detail = response.text[:1000].replace("\n", " ")
            raise ProviderError(
                f"{self.label} returned HTTP {response.status_code}: {detail}",
                code=f"http_{response.status_code}",
                retry_after_seconds=_retry_after_seconds(
                    response.headers.get("retry-after")
                ),
            )

        try:
            raw = response.json()
            content = raw["choices"][0]["message"]["content"]
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise ValueError("assistant content is not text")
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ProviderError(
                f"{self.label} returned an invalid response envelope"
            ) from error

        usage_raw = raw.get("usage") or {}
        prompt_details = usage_raw.get("prompt_tokens_details") or {}
        completion_details = usage_raw.get("completion_tokens_details") or {}
        return ProviderResult(
            content=content,
            request_id=raw.get("id"),
            resolved_model=raw.get("model"),
            usage=ProviderUsage(
                input_tokens=_optional_int(usage_raw.get("prompt_tokens")),
                cached_input_tokens=_optional_int(
                    prompt_details.get("cached_tokens")
                ),
                output_tokens=_optional_int(usage_raw.get("completion_tokens")),
                reasoning_tokens=_optional_int(
                    completion_details.get("reasoning_tokens")
                ),
                # Ark does not currently return a wallet charge here. The
                # gateway records the operator-supplied CNY token estimate and
                # marks it as an estimate instead of inventing an exact bill.
                reported_cost=None,
                cost_currency=None,
                raw=usage_raw,
            ),
            raw=raw,
        )


class OpenAICompatibleProvider(VolcengineArkProvider):
    """Basic Chat Completions adapter; no provider-specific reasoning/routing fields."""

    label = "OpenAI-compatible service"


class RoutingChatProvider:
    """Route one logical gateway across the configured Chat Completions services."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        max_connections: int = 200,
        max_keepalive_connections: int | None = None,
        request_start_interval_ms: float = 0,
        request_start_batch_size: int = 1,
        openrouter: OpenRouterProvider | None = None,
        volcengine_ark: VolcengineArkProvider | None = None,
        openai_compatible: OpenAICompatibleProvider | None = None,
    ):
        common = {
            "timeout_seconds": timeout_seconds,
            "max_connections": max_connections,
            "max_keepalive_connections": max_keepalive_connections,
            "request_start_interval_ms": request_start_interval_ms,
            "request_start_batch_size": request_start_batch_size,
        }
        self.openrouter = openrouter or OpenRouterProvider(**common)
        self.volcengine_ark = volcengine_ark or VolcengineArkProvider(**common)
        self.openai_compatible = openai_compatible or OpenAICompatibleProvider(**common)

    def complete(
        self,
        *,
        spec: ModelSpec,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        temperature: float,
        seed: int | None,
        reasoning_enabled: bool | None,
    ) -> ProviderResult:
        if spec.provider == "openrouter":
            selected: ChatProvider = self.openrouter
        elif spec.provider == "volcengine_ark":
            selected = self.volcengine_ark
        elif spec.provider == "openai_compatible":
            selected = self.openai_compatible
        else:  # pragma: no cover - ModelSpec rejects this before runtime.
            raise ProviderError(f"unsupported provider: {spec.provider}")
        return selected.complete(
            spec=spec,
            messages=messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            seed=seed,
            reasoning_enabled=reasoning_enabled,
        )

    def close(self) -> None:
        self.openrouter.close()
        self.volcengine_ark.close()
        self.openai_compatible.close()


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return parsed


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        # HTTP-date values are uncommon on this endpoint. A bounded default in
        # the gateway is safer than adding wall-clock parsing to the adapter.
        return None
