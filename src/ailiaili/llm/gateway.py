from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from ailiaili.llm.config import LLMBudget, ModelPrice, ModelSpec
from ailiaili.llm.provider import ChatProvider, ProviderError, ProviderResult
from ailiaili.persistence.database import Database
from ailiaili.persistence.models import (
    LLMCallRow,
    LLMAttemptRow,
    RunRow,
    utc_now,
)


OutputModel = TypeVar("OutputModel", bound=BaseModel)
MILLION = Decimal(1_000_000)


class BudgetExceeded(RuntimeError):
    pass


class ModelCallExhausted(RuntimeError):
    pass


class IndeterminateAttempt(RuntimeError):
    pass


class TailDiscardRequested(RuntimeError):
    """The stage no longer needs an ordinary tail call to retry."""


class TailDiscardSignal:
    """Cooperative cancellation shared by one stage and its model calls.

    Python cannot safely kill a thread inside an HTTP request.  The signal
    instead prevents work that has not been dispatched yet and suppresses any
    retry after the current request reaches a terminal provider response.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    @property
    def requested(self) -> bool:
        return self._event.is_set()


class ModelGateway:
    """One guarded path for model calls, evidence, retries, and cost."""

    def __init__(
        self,
        *,
        database: Database,
        run_dir: Path,
        budget: LLMBudget,
        provider: ChatProvider,
        evidence_mode: str = "full",
    ):
        if evidence_mode not in {"full", "compact"}:
            raise ValueError("evidence_mode must be 'full' or 'compact'")
        self.database = database
        self.run_dir = run_dir
        self.budget = budget
        self.provider = provider
        self.evidence_mode = evidence_mode
        # Reservations must be serialized even though provider requests may run
        # concurrently; otherwise two calls can both pass the same budget check.
        self._budget_lock = threading.Lock()
        self._budget_spent_cache: dict[tuple[str, str], Decimal] = {}
        # Evidence is append-only and sharded by simulation day.  One short
        # critical section is much cheaper than creating two files and one
        # directory for every request while retaining the complete raw record.
        self._evidence_lock = threading.Lock()
        self.database.create_schema()

    def invoke_json(
        self,
        *,
        run_id: str,
        simulation_day: int,
        task_type: str,
        agent_role: str,
        agent_id: str,
        cohort_role: str,
        workload_scope: str,
        model_spec: ModelSpec,
        prompt_version: str,
        idempotency_key: str,
        messages: list[dict[str, str]],
        output_model: type[OutputModel],
        max_output_tokens: int,
        temperature: float = 0.7,
        seed: int | None = None,
        reasoning_enabled: bool | None = None,
        output_validator: Callable[[OutputModel], None] | None = None,
        tail_discard_signal: TailDiscardSignal | None = None,
    ) -> OutputModel:
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if tail_discard_signal is not None and tail_discard_signal.requested:
            raise TailDiscardRequested(
                "ordinary tail call was discarded before bookkeeping"
            )
        input_snapshot = {
            "messages": messages,
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
            "seed": seed,
            "reasoning_enabled": reasoning_enabled,
            "output_schema": output_model.model_json_schema(),
        }
        input_hash = hashlib.sha256(
            json.dumps(
                input_snapshot,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        persisted_input_snapshot = input_snapshot
        if agent_role == "viewer":
            # Full prompts already live once in append-only request evidence.
            # Repeating every 40-card homepage plus its JSON schema inside the
            # database made the 30-day rehearsal exceed 1 GB. The DB keeps the
            # hash and a compact audit envelope; evidence remains replayable.
            schema_json = json.dumps(
                input_snapshot["output_schema"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            persisted_input_snapshot = {
                "storage": "compact-viewer-v1",
                "message_count": len(messages),
                "message_characters": sum(
                    len(str(message.get("content", "")))
                    for message in messages
                ),
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "seed": seed,
                "reasoning_enabled": reasoning_enabled,
                "output_schema_sha256": hashlib.sha256(
                    schema_json.encode("utf-8")
                ).hexdigest(),
            }

        call = self._get_or_create_call(
            run_id=run_id,
            simulation_day=simulation_day,
            task_type=task_type,
            agent_role=agent_role,
            agent_id=agent_id,
            cohort_role=cohort_role,
            workload_scope=workload_scope,
            model_spec=model_spec,
            prompt_version=prompt_version,
            input_hash=input_hash,
            idempotency_key=idempotency_key,
            input_snapshot=persisted_input_snapshot,
        )
        if call.input_hash != input_hash:
            raise ValueError(
                "idempotency_key was already used with different model input"
            )
        if call.status == "succeeded":
            return output_model.model_validate(call.result_json)
        retry_authorized = call.status == "retry_authorized"
        if call.status == "exhausted":
            raise ModelCallExhausted(
                f"logical call {call.call_id} already exhausted its attempts"
            )

        with self.database.session() as session:
            attempts = session.scalars(
                select(LLMAttemptRow)
                .where(LLMAttemptRow.call_id == call.call_id)
                .order_by(LLMAttemptRow.attempt_number)
            ).all()
        if any(attempt.status == "running" for attempt in attempts):
            raise IndeterminateAttempt(
                "a previously dispatched attempt has no terminal result; "
                "automatic replay could double-charge"
            )

        attempt_messages = [dict(message) for message in messages]
        if attempts and self._needs_output_repair(attempts[-1].error_code):
            attempt_messages.append(
                self._repair_message(attempts[-1].error_message or "")
            )

        first_attempt = len(attempts) + 1
        attempt_limit = (
            len(attempts) + self.budget.max_attempts_per_call
            if retry_authorized
            else self.budget.max_attempts_per_call
        )
        # A process can die after the final failed attempt is committed but
        # before the logical call is marked exhausted. Close that tiny crash
        # window deterministically instead of falling through an empty loop.
        if first_attempt > attempt_limit:
            self._mark_call_exhausted(call.call_id)
            raise ModelCallExhausted(
                f"logical call {call.call_id} exhausted {len(attempts)} attempts"
            )
        for attempt_number in range(
            first_attempt, attempt_limit + 1
        ):
            if tail_discard_signal is not None and tail_discard_signal.requested:
                self._mark_call_exhausted(call.call_id)
                raise TailDiscardRequested(
                    "ordinary tail call was discarded before dispatch"
                )
            provider_seed = retry_provider_seed(seed, attempt_number)
            reservation = conservative_request_cost(
                messages=attempt_messages,
                max_output_tokens=max_output_tokens,
                pricing=model_spec.pricing,
            )
            self._reserve_budget(
                call_id=call.call_id,
                run_id=run_id,
                attempt_number=attempt_number,
                model_spec=model_spec,
                reservation=reservation,
            )
            request_ref = self._write_request_evidence(
                simulation_day=simulation_day,
                call_id=call.call_id,
                attempt_number=attempt_number,
                model_spec=model_spec,
                messages=attempt_messages,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                seed=provider_seed,
                reasoning_enabled=reasoning_enabled,
                compact=(
                    self.evidence_mode == "compact"
                    and agent_role == "viewer"
                    and cohort_role != "reference"
                ),
            )
            self._set_request_ref(call.call_id, attempt_number, request_ref)

            if tail_discard_signal is not None and tail_discard_signal.requested:
                self._finish_failed_attempt(
                    call_id=call.call_id,
                    attempt_number=attempt_number,
                    result=None,
                    model_spec=model_spec,
                    latency_ms=0,
                    response_ref=None,
                    error_code="operational_tail_discard",
                    error_message=(
                        "Ordinary tail call was discarded before provider dispatch."
                    ),
                )
                self._mark_call_exhausted(call.call_id)
                raise TailDiscardRequested(
                    "ordinary tail call was discarded before provider dispatch"
                )

            started = time.perf_counter()
            result: ProviderResult | None = None
            response_ref: str | None = None
            try:
                result = self.provider.complete(
                    spec=model_spec,
                    messages=attempt_messages,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    seed=provider_seed,
                    reasoning_enabled=reasoning_enabled,
                )
                latency_ms = round((time.perf_counter() - started) * 1000)
                response_ref = self._write_response_evidence(
                    simulation_day=simulation_day,
                    call_id=call.call_id,
                    attempt_number=attempt_number,
                    raw=result.raw,
                    fallback_content=result.content,
                    compact=(
                        self.evidence_mode == "compact"
                        and agent_role == "viewer"
                        and cohort_role != "reference"
                    ),
                )
                parsed = parse_model_json(result.content)
                accepted = output_model.model_validate(parsed)
                if output_validator is not None:
                    output_validator(accepted)
            except (ProviderError, ValueError, ValidationError) as error:
                latency_ms = round((time.perf_counter() - started) * 1000)
                error_code = getattr(error, "code", error.__class__.__name__)
                error_message = compact_error(error)
                self._finish_failed_attempt(
                    call_id=call.call_id,
                    attempt_number=attempt_number,
                    result=result,
                    model_spec=model_spec,
                    latency_ms=latency_ms,
                    response_ref=response_ref,
                    error_code=str(error_code),
                    error_message=error_message,
                )
                if (
                    tail_discard_signal is not None
                    and tail_discard_signal.requested
                ):
                    self._mark_call_exhausted(call.call_id)
                    raise TailDiscardRequested(
                        "ordinary tail call was discarded after its current "
                        "provider attempt"
                    ) from error
                if attempt_number >= attempt_limit:
                    self._mark_call_exhausted(call.call_id)
                    raise ModelCallExhausted(
                        f"logical call {call.call_id} exhausted "
                        f"{attempt_number} attempts"
                    ) from error
                delay = retry_delay_seconds(error, attempt_number)
                if delay:
                    time.sleep(delay)
                attempt_messages = [dict(message) for message in messages]
                if self._needs_output_repair(str(error_code)):
                    attempt_messages.append(self._repair_message(error_message))
                continue

            self._accept_attempt(
                call_id=call.call_id,
                attempt_number=attempt_number,
                result=result,
                model_spec=model_spec,
                latency_ms=latency_ms,
                response_ref=response_ref,
                accepted=accepted,
            )
            if tail_discard_signal is not None and tail_discard_signal.requested:
                raise TailDiscardRequested(
                    "ordinary tail response arrived after the stage cutoff"
                )
            return accepted

        raise AssertionError("attempt loop ended without a terminal result")

    def spent(self, run_id: str, currency: str | None = None) -> Decimal:
        with self.database.session() as session:
            query = select(func.sum(LLMAttemptRow.estimated_cost)).where(
                LLMAttemptRow.run_id == run_id
            )
            if currency is not None:
                query = query.where(LLMAttemptRow.currency == currency)
            value = session.scalar(query)
        return Decimal(value or 0)

    def _get_or_create_call(self, **values: Any) -> LLMCallRow:
        run_id = str(values["run_id"])
        idempotency_key = str(values["idempotency_key"])
        with self.database.session() as session, session.begin():
            if session.get(RunRow, run_id) is None:
                raise ValueError(f"run does not exist: {run_id}")
            existing = session.scalar(
                select(LLMCallRow).where(
                    LLMCallRow.run_id == run_id,
                    LLMCallRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
            call = LLMCallRow(
                call_id=f"llm_{uuid.uuid4().hex}",
                run_id=run_id,
                simulation_day=int(values["simulation_day"]),
                task_type=str(values["task_type"]),
                agent_role=str(values["agent_role"]),
                agent_id=str(values["agent_id"]),
                cohort_role=str(values["cohort_role"]),
                workload_scope=str(values["workload_scope"]),
                provider=values["model_spec"].provider,
                requested_model=values["model_spec"].model_id,
                prompt_version=str(values["prompt_version"]),
                input_hash=str(values["input_hash"]),
                idempotency_key=idempotency_key,
                status="pending",
                input_snapshot_json=values["input_snapshot"],
            )
            session.add(call)
            session.flush()
            return call

    def _reserve_budget(
        self,
        *,
        call_id: str,
        run_id: str,
        attempt_number: int,
        model_spec: ModelSpec,
        reservation: Decimal,
    ) -> None:
        currency = model_spec.pricing.currency
        per_call_limit = self.budget.max_cost_per_call_for(currency)
        hard_limit = self.budget.hard_limit_for(currency)
        with self._budget_lock:
            if reservation > per_call_limit:
                self._mark_budget_paused(call_id)
                raise BudgetExceeded(
                    f"conservative call cost {reservation} exceeds per-call limit "
                    f"{per_call_limit} {currency}"
                )
            cache_key = (run_id, currency)
            spent = self._budget_spent_cache.get(cache_key)
            if spent is None:
                spent = self.spent(run_id, currency)
                self._budget_spent_cache[cache_key] = spent
            if spent + reservation > hard_limit:
                self._mark_budget_paused(call_id)
                raise BudgetExceeded(
                    f"budget would exceed {hard_limit} {currency}"
                )
            attempt_id = f"{call_id}_a{attempt_number:02d}"
            with self.database.session() as session, session.begin():
                call = session.get(LLMCallRow, call_id)
                assert call is not None
                call.status = "running"
                session.add(
                    LLMAttemptRow(
                        attempt_id=attempt_id,
                        call_id=call_id,
                        run_id=run_id,
                        attempt_number=attempt_number,
                        provider=model_spec.provider,
                        requested_model=model_spec.model_id,
                        status="running",
                        estimated_cost=reservation,
                        currency=currency,
                        cost_is_estimate=True,
                    )
                )
            self._budget_spent_cache[cache_key] = spent + reservation

    def _set_request_ref(
        self, call_id: str, attempt_number: int, request_ref: str
    ) -> None:
        with self.database.session() as session, session.begin():
            attempt = self._attempt(session, call_id, attempt_number)
            attempt.request_ref = request_ref

    def _finish_failed_attempt(
        self,
        *,
        call_id: str,
        attempt_number: int,
        result: ProviderResult | None,
        model_spec: ModelSpec,
        latency_ms: int,
        response_ref: str | None,
        error_code: str,
        error_message: str,
    ) -> None:
        with self.database.session() as session, session.begin():
            attempt = self._attempt(session, call_id, attempt_number)
            before_currency = attempt.currency
            before_cost = Decimal(attempt.estimated_cost or 0)
            attempt.status = "failed"
            attempt.latency_ms = latency_ms
            attempt.response_ref = response_ref
            attempt.error_code = error_code[:80]
            attempt.error_message = error_message[:2000]
            attempt.finished_at = utc_now()
            if result is not None:
                self._apply_result_usage(attempt, result, model_spec)
            run_id = attempt.run_id
            after_currency = attempt.currency
            after_cost = Decimal(attempt.estimated_cost or 0)
        self._reconcile_budget_cache(
            run_id=run_id,
            before_currency=before_currency,
            before_cost=before_cost,
            after_currency=after_currency,
            after_cost=after_cost,
        )

    def _accept_attempt(
        self,
        *,
        call_id: str,
        attempt_number: int,
        result: ProviderResult,
        model_spec: ModelSpec,
        latency_ms: int,
        response_ref: str,
        accepted: BaseModel,
    ) -> None:
        with self.database.session() as session, session.begin():
            attempt = self._attempt(session, call_id, attempt_number)
            before_currency = attempt.currency
            before_cost = Decimal(attempt.estimated_cost or 0)
            attempt.status = "succeeded"
            attempt.latency_ms = latency_ms
            attempt.response_ref = response_ref
            attempt.finished_at = utc_now()
            self._apply_result_usage(attempt, result, model_spec)
            call = session.get(LLMCallRow, call_id)
            assert call is not None
            call.status = "succeeded"
            call.accepted_attempt_id = attempt.attempt_id
            call.result_json = accepted.model_dump(mode="json")
            call.completed_at = utc_now()
            run_id = attempt.run_id
            after_currency = attempt.currency
            after_cost = Decimal(attempt.estimated_cost or 0)
        self._reconcile_budget_cache(
            run_id=run_id,
            before_currency=before_currency,
            before_cost=before_cost,
            after_currency=after_currency,
            after_cost=after_cost,
        )

    def _reconcile_budget_cache(
        self,
        *,
        run_id: str,
        before_currency: str,
        before_cost: Decimal,
        after_currency: str,
        after_cost: Decimal,
    ) -> None:
        """Replace a conservative reservation with the provider's final bill."""

        if before_currency == after_currency and before_cost == after_cost:
            return
        with self._budget_lock:
            before_key = (run_id, before_currency)
            # The reservation path always initializes this key.  The fallback
            # protects diagnostics that reconstruct a gateway around old rows.
            before_total = self._budget_spent_cache.get(before_key)
            if before_total is None:
                before_total = self.spent(run_id, before_currency)
                # spent() already contains the post-commit amount.
                if before_currency == after_currency:
                    self._budget_spent_cache[before_key] = before_total
                    return
            self._budget_spent_cache[before_key] = max(
                Decimal(0), before_total - before_cost
            )

            after_key = (run_id, after_currency)
            if after_key == before_key:
                self._budget_spent_cache[after_key] += after_cost
                return
            after_total = self._budget_spent_cache.get(after_key)
            if after_total is None:
                # This currency was not reserved.  The committed database sum
                # already includes after_cost, so do not add it a second time.
                self._budget_spent_cache[after_key] = self.spent(
                    run_id, after_currency
                )
            else:
                self._budget_spent_cache[after_key] = after_total + after_cost

    def _apply_result_usage(
        self,
        attempt: LLMAttemptRow,
        result: ProviderResult,
        model_spec: ModelSpec,
    ) -> None:
        pricing = model_spec.pricing
        usage = result.usage
        attempt.provider_request_id = result.request_id
        attempt.resolved_model = result.resolved_model
        resolved_provider = result.raw.get("provider", model_spec.provider)
        attempt.resolved_provider = (
            str(resolved_provider)[:80] if resolved_provider is not None else None
        )
        attempt.input_tokens = usage.input_tokens
        attempt.cached_input_tokens = usage.cached_input_tokens
        attempt.output_tokens = usage.output_tokens
        attempt.reasoning_tokens = usage.reasoning_tokens
        # Provider usage payloads are small and sometimes contain accounting
        # details beyond the normalized columns. Keep them even in compact
        # branches; the material saving comes from repeated 40-card prompts.
        attempt.usage_raw_json = usage.raw
        if usage.reported_cost is not None:
            attempt.estimated_cost = usage.reported_cost
            attempt.currency = usage.cost_currency or pricing.currency
            attempt.cost_is_estimate = False
            return
        fallback = usage_cost(usage, pricing)
        if fallback is not None:
            attempt.estimated_cost = fallback
            # Exact token counts do not make a locally configured rate an
            # actual bill. Keep the source distinction honest.
            attempt.cost_is_estimate = True

    def _mark_call_exhausted(self, call_id: str) -> None:
        with self.database.session() as session, session.begin():
            call = session.get(LLMCallRow, call_id)
            assert call is not None
            call.status = "exhausted"
            call.completed_at = utc_now()

    def _mark_budget_paused(self, call_id: str) -> None:
        with self.database.session() as session, session.begin():
            call = session.get(LLMCallRow, call_id)
            assert call is not None
            call.status = "budget_paused"

    def _write_request_evidence(
        self,
        *,
        simulation_day: int,
        call_id: str,
        attempt_number: int,
        model_spec: ModelSpec,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        temperature: float,
        seed: int | None,
        reasoning_enabled: bool | None,
        compact: bool = False,
    ) -> str:
        if compact:
            canonical_messages = json.dumps(
                messages,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            payload = {
                "storage": "compact-evidence-v1",
                "provider": model_spec.provider,
                "model": model_spec.model_id,
                "message_count": len(messages),
                "message_characters": sum(
                    len(str(message.get("content", ""))) for message in messages
                ),
                "messages_sha256": hashlib.sha256(
                    canonical_messages.encode("utf-8")
                ).hexdigest(),
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "seed": seed,
                "reasoning_enabled": reasoning_enabled,
                "response_format": (
                    "json_object" if model_spec.supports_json_mode else None
                ),
                "provider_order": model_spec.provider_order,
                "allow_provider_fallbacks": model_spec.allow_provider_fallbacks,
            }
        else:
            payload = {
                "provider": model_spec.provider,
                "model": model_spec.model_id,
                "messages": messages,
                "max_output_tokens": max_output_tokens,
                "temperature": temperature,
                "seed": seed,
                "reasoning_enabled": reasoning_enabled,
                "response_format": "json_object"
                if model_spec.supports_json_mode
                else None,
                "provider_order": model_spec.provider_order,
                "allow_provider_fallbacks": model_spec.allow_provider_fallbacks,
            }
        return self._write_evidence(
            simulation_day, call_id, attempt_number, "request", payload
        )

    def _write_response_evidence(
        self,
        *,
        simulation_day: int,
        call_id: str,
        attempt_number: int,
        raw: dict[str, Any],
        fallback_content: str | None = None,
        compact: bool = False,
    ) -> str:
        payload = raw
        if compact:
            choices = raw.get("choices") if isinstance(raw, dict) else None
            content: str | None = None
            finish_reason: object = None
            if isinstance(choices, list) and choices:
                choice = choices[0]
                if isinstance(choice, dict):
                    message = choice.get("message")
                    if isinstance(message, dict) and isinstance(
                        message.get("content"), str
                    ):
                        content = message["content"]
                    finish_reason = choice.get("finish_reason")
            if content is None and isinstance(raw.get("content"), str):
                # Some compatible providers and test adapters expose accepted
                # content at the top level instead of an OpenAI choices list.
                content = raw["content"]
            if content is None:
                content = fallback_content
            payload = {
                "storage": "compact-evidence-v1",
                "id": raw.get("id"),
                "model": raw.get("model"),
                "provider": raw.get("provider"),
                "usage": raw.get("usage"),
                "finish_reason": finish_reason,
                "content": content,
            }
        return self._write_evidence(
            simulation_day, call_id, attempt_number, "response", payload
        )

    def _write_evidence(
        self,
        simulation_day: int,
        call_id: str,
        attempt_number: int,
        kind: str,
        payload: dict[str, Any],
    ) -> str:
        directory = self.run_dir / "evidence" / "llm" / f"day_{simulation_day:03d}"
        path = directory / f"{kind}.jsonl"
        record_id = f"{call_id}:a{attempt_number:02d}:{kind}"
        envelope = {
            "record_id": record_id,
            "call_id": call_id,
            "attempt_number": attempt_number,
            "kind": kind,
            "payload": payload,
        }
        line = json.dumps(
            envelope,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._evidence_lock:
            directory.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line)
                stream.write("\n")
        relative = str(path.relative_to(self.run_dir)).replace("\\", "/")
        return f"{relative}#{record_id}"

    @staticmethod
    def _repair_message(error_message: str) -> dict[str, str]:
        return {
            "role": "user",
            "content": (
                "上一次返回无法通过结构校验："
                f"{error_message[:500]}。请重新读取原始输入，不要复用错误中指出的"
                "非法值；只返回修正后的 JSON，不要解释。"
            ),
        }

    @staticmethod
    def _needs_output_repair(error_code: str | None) -> bool:
        """Only model-output defects should change a retry's prompt."""

        return error_code in {
            "ValueError",
            "JSONDecodeError",
            "ValidationError",
        }

    @staticmethod
    def _attempt(session: Any, call_id: str, attempt_number: int) -> LLMAttemptRow:
        attempt = session.scalar(
            select(LLMAttemptRow).where(
                LLMAttemptRow.call_id == call_id,
                LLMAttemptRow.attempt_number == attempt_number,
            )
        )
        assert attempt is not None
        return attempt


def read_evidence_ref(run_dir: Path, reference: str) -> dict[str, Any]:
    """Read both legacy one-file evidence and the high-throughput JSONL format."""

    relative_path, separator, record_id = reference.partition("#")
    path = run_dir / relative_path
    if not separator:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"evidence is not a JSON object: {reference}")
        return value

    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if record_id not in line:
                continue
            envelope = json.loads(line)
            if envelope.get("record_id") == record_id:
                payload = envelope.get("payload")
                if not isinstance(payload, dict):
                    raise ValueError(f"evidence payload is invalid: {reference}")
                return payload
    raise FileNotFoundError(f"evidence record not found: {reference}")


def conservative_request_cost(
    *,
    messages: list[dict[str, str]],
    max_output_tokens: int,
    pricing: ModelPrice,
) -> Decimal:
    # Raw UTF-8 bytes are not tokens: treating every byte as one token
    # over-reserves mixed Chinese prompts by roughly 2.7x and can stop a
    # bounded premium call before it is dispatched.  Across all formally
    # admitted providers, the smallest observed bytes/token ratio is about
    # 2.69 (Sonnet); 2.5 therefore retains a conservative margin while no
    # longer multiplying Chinese input by three.  The fixed allowance covers
    # chat-envelope/tokenizer overhead. Cache is never assumed preflight.
    serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    utf8_bytes = len(serialized.encode("utf-8"))
    estimated_input_tokens = math.ceil(utf8_bytes / 2.5) + 256
    return (
        Decimal(estimated_input_tokens) * pricing.input_per_million
        + Decimal(max_output_tokens) * pricing.output_per_million
    ) / MILLION


def retry_provider_seed(seed: int | None, attempt_number: int) -> int | None:
    if seed is None:
        return None
    return (seed + attempt_number - 1) % (2**31)


def usage_cost(usage: Any, pricing: ModelPrice) -> Decimal | None:
    if usage.input_tokens is None or usage.output_tokens is None:
        return None
    cached = min(usage.cached_input_tokens or 0, usage.input_tokens)
    uncached = usage.input_tokens - cached
    cached_rate = (
        pricing.cached_input_per_million
        if pricing.cached_input_per_million is not None
        else pricing.input_per_million
    )
    return (
        Decimal(uncached) * pricing.input_per_million
        + Decimal(cached) * cached_rate
        + Decimal(usage.output_tokens) * pricing.output_per_million
    ) / MILLION


def parse_model_json(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().lower() in ("```", "```json"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Some otherwise schema-perfect models append one explanatory sentence
    # after the requested object.  Preserve that raw response in evidence, but
    # accept the first complete JSON value and let Pydantic validate its exact
    # business shape.  Prefix prose remains invalid so this is tolerance, not
    # a best-effort guess at what the model meant.
    value, _ = json.JSONDecoder().raw_decode(text)
    return value


def compact_error(error: Exception) -> str:
    if isinstance(error, ValidationError):
        parts = []
        for item in error.errors()[:5]:
            location = ".".join(str(part) for part in item["loc"])
            parts.append(f"{location}: {item['msg']}")
        return "; ".join(parts)
    if isinstance(error, json.JSONDecodeError):
        return f"invalid JSON at line {error.lineno}, column {error.colno}"
    return str(error)[:1000]


def retry_delay_seconds(error: Exception, attempt_number: int) -> float:
    """Back off for explicit upstream rate limits and transient outages.

    Schema repair should remain immediate, while 429/5xx responses should not
    be retried in the same burst that triggered them. Provider Retry-After is
    honored but capped so one optional task cannot stall a Run indefinitely.
    """

    retryable_codes = {
        "http_429",
        "http_500",
        "http_502",
        "http_503",
        "http_504",
        "http_529",
    }
    if not isinstance(error, ProviderError) or error.code not in retryable_codes:
        return 0.0
    explicit = error.retry_after_seconds
    if explicit is not None:
        return min(explicit, 15.0)
    return min(float(2 ** (attempt_number - 1)), 8.0)
