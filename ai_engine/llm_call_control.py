"""Control acotado de deadlines, retries y fallback para llamadas LLM.

Este modulo no conoce prompts ni contratos cognitivos. Su unica autoridad es
calendarizar intentos de proveedores bajo un deadline monotónico absoluto y
devolver metadata tipada sin texto sensible por intento.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import math
import time
from typing import Any, Literal

import httpx

from ai_engine.ai_client import LLMCallAudit
from ai_engine.prompt_plan import PromptProjectionUnsupported
from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure


logger = get_logger(__name__)


LLMOperation = Literal["plan", "text", "health"]
MonotonicClock = Callable[[], int]
AsyncSleeper = Callable[[float], Awaitable[None]]


def require_finite_positive(value: object, *, field_name: str) -> float:
    """Valida segundos finitos positivos sin aceptar bool como numero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} debe ser un numero finito positivo.")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field_name} debe ser un numero finito positivo.")
    return normalized


@dataclass(frozen=True, slots=True)
class LLMCallBudgetV1:
    """Deadline absoluto e inmutable expresado con reloj monotónico."""

    deadline_ns: int

    def __post_init__(self) -> None:
        if isinstance(self.deadline_ns, bool) or not isinstance(self.deadline_ns, int):
            raise ValueError("deadline_ns debe ser un entero monotónico positivo.")
        if self.deadline_ns <= 0:
            raise ValueError("deadline_ns debe ser un entero monotónico positivo.")

    @classmethod
    def from_seconds(
        cls,
        seconds: object,
        *,
        clock: MonotonicClock = time.perf_counter_ns,
    ) -> "LLMCallBudgetV1":
        normalized = require_finite_positive(seconds, field_name="seconds")
        now_ns = int(clock())
        duration_ns = max(1, int(normalized * 1_000_000_000))
        return cls(deadline_ns=now_ns + duration_ns)

    def remaining_ns(
        self,
        clock: MonotonicClock = time.perf_counter_ns,
    ) -> int:
        return max(0, self.deadline_ns - int(clock()))

    def remaining_seconds(
        self,
        clock: MonotonicClock = time.perf_counter_ns,
    ) -> float:
        return self.remaining_ns(clock) / 1_000_000_000

    def derive_cap(
        self,
        cap_seconds: object,
        *,
        clock: MonotonicClock = time.perf_counter_ns,
    ) -> "LLMCallBudgetV1":
        cap = require_finite_positive(cap_seconds, field_name="cap_seconds")
        now_ns = int(clock())
        capped_deadline = now_ns + max(1, int(cap * 1_000_000_000))
        return LLMCallBudgetV1(min(self.deadline_ns, capped_deadline))


class LLMFailureClassV1(str, Enum):
    TRANSPORT_TIMEOUT = "TRANSPORT_TIMEOUT"
    CONNECTION_FAILURE = "CONNECTION_FAILURE"
    RATE_LIMITED = "RATE_LIMITED"
    TRANSIENT_HTTP = "TRANSIENT_HTTP"
    NON_RETRYABLE_CLIENT = "NON_RETRYABLE_CLIENT"
    AUTH_FAILURE = "AUTH_FAILURE"
    MALFORMED_PROVIDER_RESPONSE = "MALFORMED_PROVIDER_RESPONSE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    PROJECTION_UNSUPPORTED = "PROJECTION_UNSUPPORTED"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"


class LLMCallTerminalV1(str, Enum):
    SUCCESS = "SUCCESS"
    DEADLINE_EXCEEDED = "DEADLINE_EXCEEDED"
    NO_COMPATIBLE_PROVIDER = "NO_COMPATIBLE_PROVIDER"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
    NON_RETRYABLE_FAILURE = "NON_RETRYABLE_FAILURE"
    ALL_PROVIDERS_FAILED = "ALL_PROVIDERS_FAILED"


@dataclass(frozen=True, slots=True)
class ProviderAttemptReceiptV1:
    provider: str
    model_id: str | None
    failure_class: LLMFailureClassV1 | None
    error_type: str | None
    http_status: int | None
    retry_after_seconds: float | None
    started_ns: int
    finished_ns: int


@dataclass(frozen=True, slots=True)
class RetryDecisionV1:
    should_retry: bool
    delay_seconds: float
    failure_class: LLMFailureClassV1


@dataclass(frozen=True, slots=True)
class LLMCallResultV1:
    terminal: LLMCallTerminalV1
    final_text: str | None
    final_provider: str | None
    final_model_id: str | None
    final_failure: LLMFailureClassV1 | None
    receipts: tuple[ProviderAttemptReceiptV1, ...]

    @property
    def succeeded(self) -> bool:
        return self.terminal is LLMCallTerminalV1.SUCCESS


class ProviderMalformedResponse(RuntimeError):
    """El transporte respondió, pero el envelope del proveedor no es utilizable."""


class ProviderConfigurationError(RuntimeError):
    """La configuración local impide intentar el proveedor."""


class RouteDeadlineExceeded(TimeoutError):
    """El deadline de ruta venció antes de poder aceptar una respuesta."""


class ActiveEventLoopSyncBridgeError(RuntimeError):
    """El bridge sync fue invocado desde un hilo que ya posee un event loop."""


@dataclass(frozen=True, slots=True)
class _AttemptValue:
    text: str


class LLMCallExecutorV1:
    """Unico dueño de provider iteration, retries, sleeps y terminalización."""

    _RETRYABLE = {
        LLMFailureClassV1.TRANSPORT_TIMEOUT,
        LLMFailureClassV1.CONNECTION_FAILURE,
        LLMFailureClassV1.RATE_LIMITED,
        LLMFailureClassV1.TRANSIENT_HTTP,
    }

    def __init__(
        self,
        *,
        clock: MonotonicClock = time.perf_counter_ns,
        sleeper: AsyncSleeper = asyncio.sleep,
        jitter: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._sleeper = sleeper
        self._jitter = jitter or (lambda: 0.0)
        self._wall_clock = wall_clock

    async def execute_async(
        self,
        providers: Iterable[object],
        *,
        operation: LLMOperation,
        value: object,
        budget: LLMCallBudgetV1,
        generation: Mapping[str, Any] | None = None,
        audit_sink: Callable[[LLMCallAudit], bool] | None = None,
    ) -> LLMCallResultV1:
        if operation not in {"plan", "text", "health"}:
            raise ValueError("operation LLM no soportada.")
        if not isinstance(budget, LLMCallBudgetV1):
            raise TypeError("budget debe ser LLMCallBudgetV1.")
        provider_list = tuple(provider for provider in providers if provider is not None)
        receipts: list[ProviderAttemptReceiptV1] = []
        active_attempt: dict[str, object] = {}
        remaining = budget.remaining_seconds(self._clock)
        if remaining <= 0:
            return self._deadline_result(receipts)
        try:
            async with asyncio.timeout(remaining):
                return await self._execute_under_deadline(
                    provider_list,
                    operation=operation,
                    value=value,
                    budget=budget,
                    generation=dict(generation or {}),
                    receipts=receipts,
                    audit_sink=audit_sink,
                    active_attempt=active_attempt,
                )
        except TimeoutError:
            if active_attempt:
                receipt = ProviderAttemptReceiptV1(
                    provider=str(active_attempt["provider"]),
                    model_id=active_attempt.get("model_id"),  # type: ignore[arg-type]
                    failure_class=LLMFailureClassV1.DEADLINE_EXCEEDED,
                    error_type="DeadlineExceeded",
                    http_status=None,
                    retry_after_seconds=None,
                    started_ns=int(active_attempt["started_ns"]),
                    finished_ns=max(int(active_attempt["started_ns"]), int(self._clock())),
                )
                receipts.append(receipt)
                self._audit(
                    receipt,
                    bool(active_attempt.get("fallback_used", False)),
                    audit_sink,
                )
                active_attempt.clear()
            return self._deadline_result(receipts)

    def execute_sync(self, *args: Any, **kwargs: Any) -> LLMCallResultV1:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.execute_async(*args, **kwargs))
        raise ActiveEventLoopSyncBridgeError(
            "execute_sync no puede ejecutarse dentro de un event loop activo; use execute_async."
        )

    async def _execute_under_deadline(
        self,
        providers: tuple[object, ...],
        *,
        operation: LLMOperation,
        value: object,
        budget: LLMCallBudgetV1,
        generation: dict[str, Any],
        receipts: list[ProviderAttemptReceiptV1],
        audit_sink: Callable[[LLMCallAudit], bool] | None,
        active_attempt: dict[str, object],
    ) -> LLMCallResultV1:
        compatible_attempted = 0
        last_failure: LLMFailureClassV1 | None = None
        last_provider: str | None = None
        last_model: str | None = None

        for provider_index, provider in enumerate(providers):
            if budget.remaining_ns(self._clock) <= 0:
                return self._deadline_result(receipts)
            provider_name = self._provider_name(provider)
            model_id = self._model_id(provider)
            try:
                provider_cap = self._provider_cap(provider, budget)
                provider_budget = budget.derive_cap(provider_cap, clock=self._clock)
            except Exception as error:
                compatible_attempted += 1
                receipt = self._receipt_for_error(
                    provider_name,
                    model_id,
                    error,
                    self._clock(),
                    self._clock(),
                    forced=LLMFailureClassV1.CONFIGURATION_ERROR,
                )
                receipts.append(receipt)
                self._audit(receipt, provider_index > 0, audit_sink)
                last_failure = receipt.failure_class
                last_provider = provider_name
                last_model = model_id
                continue

            max_attempts = self._max_attempts(provider)
            provider_was_compatible = False
            for attempt_index in range(max_attempts):
                if budget.remaining_ns(self._clock) <= 0:
                    return self._deadline_result(receipts)
                if provider_budget.remaining_ns(self._clock) <= 0:
                    break

                started_ns = int(self._clock())
                active_attempt.update(
                    provider=provider_name,
                    model_id=model_id,
                    started_ns=started_ns,
                    fallback_used=provider_index > 0,
                )
                error: Exception | None = None
                text = ""
                try:
                    attempt_remaining = provider_budget.remaining_seconds(self._clock)
                    if attempt_remaining <= 0:
                        break
                    async with asyncio.timeout(attempt_remaining):
                        result = await self._invoke_provider(
                            provider,
                            operation=operation,
                            value=value,
                            child_budget=provider_budget,
                            generation=generation,
                        )
                    text = result.text
                except PromptProjectionUnsupported:
                    # Incompatibilidad de proyección no es intento de transporte ni audit event.
                    provider_was_compatible = False
                    active_attempt.clear()
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as caught:
                    error = caught

                finished_ns = int(self._clock())
                if finished_ns >= budget.deadline_ns:
                    receipt = ProviderAttemptReceiptV1(
                        provider=provider_name,
                        model_id=model_id,
                        failure_class=LLMFailureClassV1.DEADLINE_EXCEEDED,
                        error_type="DeadlineExceeded",
                        http_status=None,
                        retry_after_seconds=None,
                        started_ns=started_ns,
                        finished_ns=max(started_ns, finished_ns),
                    )
                    receipts.append(receipt)
                    self._audit(receipt, provider_index > 0, audit_sink)
                    active_attempt.clear()
                    return self._deadline_result(receipts)

                provider_was_compatible = True
                if error is None and text.strip():
                    receipt = ProviderAttemptReceiptV1(
                        provider=provider_name,
                        model_id=model_id,
                        failure_class=None,
                        error_type=None,
                        http_status=None,
                        retry_after_seconds=None,
                        started_ns=started_ns,
                        finished_ns=finished_ns,
                    )
                    receipts.append(receipt)
                    self._audit(receipt, provider_index > 0, audit_sink)
                    active_attempt.clear()
                    return LLMCallResultV1(
                        terminal=LLMCallTerminalV1.SUCCESS,
                        final_text=text.strip(),
                        final_provider=provider_name,
                        final_model_id=model_id,
                        final_failure=None,
                        receipts=tuple(receipts),
                    )

                if error is None:
                    receipt = ProviderAttemptReceiptV1(
                        provider=provider_name,
                        model_id=model_id,
                        failure_class=LLMFailureClassV1.EMPTY_RESPONSE,
                        error_type="EmptyResponse",
                        http_status=None,
                        retry_after_seconds=None,
                        started_ns=started_ns,
                        finished_ns=finished_ns,
                    )
                else:
                    receipt = self._receipt_for_error(
                        provider_name,
                        model_id,
                        error,
                        started_ns,
                        finished_ns,
                    )
                receipts.append(receipt)
                self._audit(receipt, provider_index > 0, audit_sink)
                active_attempt.clear()
                last_failure = receipt.failure_class
                last_provider = provider_name
                last_model = model_id

                if receipt.failure_class is LLMFailureClassV1.DEADLINE_EXCEEDED:
                    return self._deadline_result(receipts)

                retry = self.retry_decision(
                    receipt,
                    attempt_index=attempt_index,
                    max_attempts=max_attempts,
                )
                if not retry.should_retry:
                    break
                remaining_for_retry = min(
                    budget.remaining_seconds(self._clock),
                    provider_budget.remaining_seconds(self._clock),
                )
                # Nunca dormir hasta o mas alla del límite. El siguiente proveedor
                # todavía puede usar el remanente del budget padre.
                if remaining_for_retry <= 0 or retry.delay_seconds >= remaining_for_retry:
                    break
                if retry.delay_seconds > 0:
                    await self._sleeper(retry.delay_seconds)
                if budget.remaining_ns(self._clock) <= 0:
                    return self._deadline_result(receipts)

            if provider_was_compatible:
                compatible_attempted += 1

        if budget.remaining_ns(self._clock) <= 0:
            return self._deadline_result(receipts)
        if compatible_attempted == 0:
            return LLMCallResultV1(
                terminal=LLMCallTerminalV1.NO_COMPATIBLE_PROVIDER,
                final_text=None,
                final_provider=None,
                final_model_id=None,
                final_failure=LLMFailureClassV1.PROJECTION_UNSUPPORTED,
                receipts=tuple(receipts),
            )
        if compatible_attempted == 1:
            terminal = (
                LLMCallTerminalV1.RETRY_EXHAUSTED
                if last_failure in self._RETRYABLE
                else LLMCallTerminalV1.NON_RETRYABLE_FAILURE
            )
        else:
            terminal = LLMCallTerminalV1.ALL_PROVIDERS_FAILED
        return LLMCallResultV1(
            terminal=terminal,
            final_text=None,
            final_provider=last_provider,
            final_model_id=last_model,
            final_failure=last_failure,
            receipts=tuple(receipts),
        )

    async def _invoke_provider(
        self,
        provider: object,
        *,
        operation: LLMOperation,
        value: object,
        child_budget: LLMCallBudgetV1,
        generation: dict[str, Any],
    ) -> _AttemptValue:
        attempt = getattr(provider, "attempt_async", None)
        if not callable(attempt):
            raise ProviderConfigurationError(
                "Provider does not implement bounded async attempt"
            )
        result = await attempt(
            operation=operation,
            value=value,
            child_budget=child_budget,
            generation=generation,
        )
        if isinstance(result, bool):
            return _AttemptValue("ok" if result else "")
        return _AttemptValue(result if isinstance(result, str) else "")

    def retry_decision(
        self,
        receipt: ProviderAttemptReceiptV1,
        *,
        attempt_index: int,
        max_attempts: int,
    ) -> RetryDecisionV1:
        failure = receipt.failure_class or LLMFailureClassV1.UNKNOWN_FAILURE
        eligible = failure in self._RETRYABLE and attempt_index + 1 < max_attempts
        if not eligible:
            return RetryDecisionV1(False, 0.0, failure)
        base = float(2**attempt_index)
        jitter = self._safe_nonnegative(self._jitter())
        delay = receipt.retry_after_seconds
        if delay is None:
            delay = base + jitter
        return RetryDecisionV1(True, self._safe_nonnegative(delay), failure)

    def _receipt_for_error(
        self,
        provider: str,
        model_id: str | None,
        error: BaseException,
        started_ns: int,
        finished_ns: int,
        *,
        forced: LLMFailureClassV1 | None = None,
    ) -> ProviderAttemptReceiptV1:
        status = self._http_status(error)
        failure = forced or self._classify(error, status)
        try:
            log_safe_boundary_failure(
                logger,
                component="llm_executor.attempt",
                code="UPSTREAM_PROVIDER_FAILED",
                error=error,
                http_status=status,
            )
        except Exception:
            pass
        return ProviderAttemptReceiptV1(
            provider=provider,
            model_id=model_id,
            failure_class=failure,
            error_type=self._stable_error_type(error, failure),
            http_status=status,
            retry_after_seconds=self._retry_after_seconds(error),
            started_ns=int(started_ns),
            finished_ns=max(int(started_ns), int(finished_ns)),
        )

    @staticmethod
    def _classify(error: BaseException, status: int | None) -> LLMFailureClassV1:
        if isinstance(error, RouteDeadlineExceeded):
            return LLMFailureClassV1.DEADLINE_EXCEEDED
        if isinstance(error, ProviderMalformedResponse):
            return LLMFailureClassV1.MALFORMED_PROVIDER_RESPONSE
        if isinstance(error, ProviderConfigurationError):
            return LLMFailureClassV1.CONFIGURATION_ERROR
        if isinstance(error, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
            return LLMFailureClassV1.TRANSPORT_TIMEOUT
        if isinstance(error, (httpx.ConnectError, httpx.NetworkError, ConnectionError)):
            return LLMFailureClassV1.CONNECTION_FAILURE
        if status in {401, 403}:
            return LLMFailureClassV1.AUTH_FAILURE
        if status == 429:
            return LLMFailureClassV1.RATE_LIMITED
        if status in {425, 500, 502, 503, 504}:
            return LLMFailureClassV1.TRANSIENT_HTTP
        if status in {400, 404, 409} or (status is not None and 400 <= status < 500):
            return LLMFailureClassV1.NON_RETRYABLE_CLIENT
        return LLMFailureClassV1.UNKNOWN_FAILURE

    @staticmethod
    def _stable_error_type(
        error: BaseException,
        failure: LLMFailureClassV1,
    ) -> str:
        if failure is LLMFailureClassV1.TRANSPORT_TIMEOUT:
            return "TimeoutError"
        name = type(error).__name__
        return name if name else "LLMError"

    @staticmethod
    def _http_status(error: BaseException) -> int | None:
        response = getattr(error, "response", None)
        candidates = (
            getattr(response, "status_code", None),
            getattr(error, "status_code", None),
            getattr(error, "code", None),
        )
        for candidate in candidates:
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                return candidate
        return None

    def _retry_after_seconds(self, error: BaseException) -> float | None:
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        raw: object | None = None
        if headers is not None:
            try:
                raw = headers.get("Retry-After")
            except Exception:
                raw = None
        if raw is None:
            for field in ("retry_after_seconds", "retry_after"):
                candidate = getattr(error, field, None)
                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                    if math.isfinite(float(candidate)) and float(candidate) >= 0:
                        return float(candidate)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
            return value if math.isfinite(value) and value >= 0 else None
        if not isinstance(raw, str):
            return None
        stripped = raw.strip()
        try:
            numeric = float(stripped)
        except ValueError:
            numeric = None
        if numeric is not None:
            return numeric if math.isfinite(numeric) and numeric >= 0 else None
        try:
            parsed = parsedate_to_datetime(stripped)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            delay = parsed.timestamp() - float(self._wall_clock())
            return max(0.0, delay) if math.isfinite(delay) else None
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _safe_nonnegative(value: object) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        normalized = float(value)
        return normalized if math.isfinite(normalized) and normalized >= 0 else 0.0

    def _provider_cap(self, provider: object, budget: LLMCallBudgetV1) -> float:
        for attribute in ("provider_timeout_seconds", "timeout_seconds", "timeout"):
            if hasattr(provider, attribute):
                return require_finite_positive(
                    getattr(provider, attribute),
                    field_name=f"{self._provider_name(provider)}.{attribute}",
                )
        remaining = budget.remaining_seconds(self._clock)
        return require_finite_positive(remaining, field_name="route_remaining")

    @staticmethod
    def _max_attempts(provider: object) -> int:
        value = getattr(provider, "llm_max_attempts", 1)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return 1
        return min(value, 16)

    @staticmethod
    def _provider_name(provider: object) -> str:
        value = getattr(provider, "provider_name", provider.__class__.__name__)
        return str(value).strip() or provider.__class__.__name__

    @staticmethod
    def _model_id(provider: object) -> str | None:
        value = getattr(provider, "model_name", None)
        return str(value).strip() if value is not None and str(value).strip() else None

    @staticmethod
    def _audit(
        receipt: ProviderAttemptReceiptV1,
        fallback_used: bool,
        sink: Callable[[LLMCallAudit], bool] | None,
    ) -> None:
        if not callable(sink):
            return
        failure = receipt.failure_class
        status = "ok"
        if failure is LLMFailureClassV1.EMPTY_RESPONSE:
            status = "empty"
        elif failure in {
            LLMFailureClassV1.TRANSPORT_TIMEOUT,
            LLMFailureClassV1.DEADLINE_EXCEEDED,
        }:
            status = "timeout"
        elif failure is not None:
            status = "error"
        try:
            sink(
                LLMCallAudit(
                    provider=receipt.provider,
                    model_id=receipt.model_id,
                    fallback_used=fallback_used,
                    status=status,
                    error_type=receipt.error_type,
                    duration_ms=round(
                        max(0, receipt.finished_ns - receipt.started_ns) / 1_000_000,
                        3,
                    ),
                    started_ns=receipt.started_ns,
                )
            )
        except Exception:
            # La telemetría nunca gobierna el resultado funcional.
            return

    @staticmethod
    def _deadline_result(
        receipts: list[ProviderAttemptReceiptV1],
    ) -> LLMCallResultV1:
        return LLMCallResultV1(
            terminal=LLMCallTerminalV1.DEADLINE_EXCEEDED,
            final_text=None,
            final_provider=None,
            final_model_id=None,
            final_failure=LLMFailureClassV1.DEADLINE_EXCEEDED,
            receipts=tuple(receipts),
        )


__all__ = [
    "ActiveEventLoopSyncBridgeError",
    "LLMCallBudgetV1",
    "LLMCallExecutorV1",
    "LLMCallResultV1",
    "LLMCallTerminalV1",
    "LLMFailureClassV1",
    "ProviderAttemptReceiptV1",
    "ProviderConfigurationError",
    "ProviderMalformedResponse",
    "RetryDecisionV1",
    "RouteDeadlineExceeded",
    "require_finite_positive",
]
