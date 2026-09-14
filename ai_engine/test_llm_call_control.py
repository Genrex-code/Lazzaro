"""ROOT-021: matriz permanente de deadline, retry, fallback y lifecycle (107 casos)."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
import inspect
import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from google.genai import types

from ai_engine.ai_client import BaseAIClient, LLMCallAudit
from ai_engine.client_factory import build_ai_client
from ai_engine.fallback_ai_client import FallbackAIClient
from ai_engine.gemini_client import GeminiClient
from ai_engine.identity_projection import IdentityContextV1
from ai_engine.llm_call_control import (
    ActiveEventLoopSyncBridgeError,
    LLMCallBudgetV1,
    LLMCallExecutorV1,
    LLMCallResultV1,
    LLMCallTerminalV1,
    LLMFailureClassV1,
    ProviderAttemptReceiptV1,
    ProviderConfigurationError,
    ProviderMalformedResponse,
    RetryDecisionV1,
)
from ai_engine.ollama_client import OllamaClient
from ai_engine.openai_compatible_client import OpenAICompatibleClient
from ai_engine.oracle_client import OracleClient
from ai_engine.persona_engine import PersonaEngine
from ai_engine.prompt_plan import (
    ContextEnvelopeV1,
    PromptPlanV1,
    PromptProjectionUnsupported,
    RequestInstructionV1,
    build_oracle_system_instructions,
)
from ai_engine.reasoning_engine import ReasoningEngine
from ai_engine.decision_outcome import (
    DecisionAttempt,
    DecisionReasonCode,
)
from ai_engine.schemas import OracleResult
from api.schemas import EventoEntrante
from config.settings import Settings
from core.audit_trace import AuditTrace
from core.behavior_core import BehaviorCore
from core.identity_snapshot import IdentitySnapshotProvider, build_static_self_v1
from core.orchestrator import Orchestrator


ROOT = Path(__file__).resolve().parents[1]


class FakeClock:
    def __init__(self, seconds: float = 0.0) -> None:
        self.ns = int(seconds * 1_000_000_000)

    def __call__(self) -> int:
        return self.ns

    def advance(self, seconds: float) -> None:
        self.ns += int(seconds * 1_000_000_000)


class FakeSleeper:
    def __init__(self, clock: FakeClock, *, multiplier: float = 1.0) -> None:
        self.clock = clock
        self.multiplier = multiplier
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.clock.advance(delay * self.multiplier)


class ScriptedProvider:
    def __init__(
        self,
        name: str,
        actions: list[object],
        *,
        clock: FakeClock | None = None,
        cap: float = 45.0,
        attempts: int = 1,
    ) -> None:
        self.provider_name = name
        self.model_name = f"{name}-model"
        self.provider_timeout_seconds = cap
        self.llm_max_attempts = attempts
        self.actions = list(actions)
        self.clock = clock
        self.calls = 0
        self.values: list[object] = []
        self.child_deadlines: list[int] = []

    async def attempt_async(self, *, operation, value, child_budget, generation):
        self.calls += 1
        self.values.append(value)
        self.child_deadlines.append(child_budget.deadline_ns)
        action = self.actions[min(self.calls - 1, len(self.actions) - 1)]
        if isinstance(action, tuple) and action and action[0] == "advance":
            assert self.clock is not None
            self.clock.advance(float(action[1]))
            action = action[2]
        if isinstance(action, BaseException):
            raise action
        if callable(action):
            result = action()
            if inspect.isawaitable(result):
                return await result
            return result
        return action


def _plan() -> PromptPlanV1:
    return PromptPlanV1(
        system=build_oracle_system_instructions(),
        context=ContextEnvelopeV1(),
        request=RequestInstructionV1.current_user("diagnostico"),
    )


def _budget(clock: FakeClock, seconds: float = 45.0) -> LLMCallBudgetV1:
    return LLMCallBudgetV1.from_seconds(seconds, clock=clock)


def _executor(clock: FakeClock, sleeper: FakeSleeper | None = None) -> LLMCallExecutorV1:
    return LLMCallExecutorV1(clock=clock, sleeper=sleeper or FakeSleeper(clock))


def _run(
    providers,
    *,
    clock: FakeClock | None = None,
    budget: LLMCallBudgetV1 | None = None,
    operation: str = "plan",
    value: object | None = None,
    sleeper: FakeSleeper | None = None,
    audit_sink=None,
) -> LLMCallResultV1:
    clock = clock or FakeClock()
    return _executor(clock, sleeper).execute_sync(
        providers,
        operation=operation,
        value=_plan() if value is None else value,
        budget=budget or _budget(clock),
        generation={},
        audit_sink=audit_sink,
    )


def _status_error(status: int, *, retry_after: str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.invalid/v1")
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    response = httpx.Response(status, request=request, headers=headers)
    return httpx.HTTPStatusError("SECRET_BODY", request=request, response=response)


def _raises(expected, callback, *args, **kwargs):
    try:
        callback(*args, **kwargs)
    except expected as error:
        return error
    raise AssertionError(f"Se esperaba {expected.__name__}")


def test_001_budget_stores_absolute_deadline() -> None:
    clock = FakeClock(3)
    assert _budget(clock, 2).deadline_ns == 5_000_000_000


def test_002_budget_is_frozen() -> None:
    budget = LLMCallBudgetV1(1)
    _raises(FrozenInstanceError, setattr, budget, "deadline_ns", 2)


def test_003_budget_uses_slots() -> None:
    assert not hasattr(LLMCallBudgetV1(1), "__dict__")


def test_004_budget_rejects_bool() -> None:
    _raises(ValueError, LLMCallBudgetV1.from_seconds, True)


def test_005_budget_rejects_nan() -> None:
    _raises(ValueError, LLMCallBudgetV1.from_seconds, math.nan)


def test_006_budget_rejects_positive_inf() -> None:
    _raises(ValueError, LLMCallBudgetV1.from_seconds, math.inf)


def test_007_budget_rejects_negative_inf() -> None:
    _raises(ValueError, LLMCallBudgetV1.from_seconds, -math.inf)


def test_008_budget_rejects_zero() -> None:
    _raises(ValueError, LLMCallBudgetV1.from_seconds, 0)


def test_009_budget_rejects_negative() -> None:
    _raises(ValueError, LLMCallBudgetV1.from_seconds, -1)


def test_010_deadline_constructor_rejects_bool() -> None:
    _raises(ValueError, LLMCallBudgetV1, True)


def test_011_remaining_never_negative() -> None:
    clock = FakeClock(2)
    assert LLMCallBudgetV1(1).remaining_ns(clock) == 0


def test_012_child_cap_is_minimum() -> None:
    clock = FakeClock()
    parent = _budget(clock, 45)
    assert parent.derive_cap(15, clock=clock).deadline_ns == 15_000_000_000


def test_013_child_never_mutates_parent() -> None:
    clock = FakeClock()
    parent = _budget(clock)
    original = parent.deadline_ns
    parent.derive_cap(15, clock=clock)
    assert parent.deadline_ns == original


def test_014_sibling_after_fifteen_has_thirty() -> None:
    clock = FakeClock()
    parent = _budget(clock)
    parent.derive_cap(15, clock=clock)
    clock.advance(15)
    sibling = parent.derive_cap(45, clock=clock)
    assert sibling.remaining_seconds(clock) == 30


def test_015_sibling_after_forty_has_five() -> None:
    clock = FakeClock()
    parent = _budget(clock)
    clock.advance(40)
    assert parent.derive_cap(45, clock=clock).remaining_seconds(clock) == 5


def test_016_receipt_is_frozen_and_slotted() -> None:
    receipt = ProviderAttemptReceiptV1("p", None, None, None, None, None, 1, 2)
    assert not hasattr(receipt, "__dict__")
    _raises(FrozenInstanceError, setattr, receipt, "provider", "x")


def test_017_retry_decision_is_frozen_and_slotted() -> None:
    decision = RetryDecisionV1(True, 1, LLMFailureClassV1.TRANSIENT_HTTP)
    assert not hasattr(decision, "__dict__")
    _raises(FrozenInstanceError, setattr, decision, "delay_seconds", 2)


def test_018_result_is_frozen_and_slotted() -> None:
    result = LLMCallResultV1(LLMCallTerminalV1.SUCCESS, "x", "p", None, None, ())
    assert result.succeeded and not hasattr(result, "__dict__")


def test_019_settings_total_budget_default_is_45() -> None:
    assert Settings(_env_file=None).LLM_TOTAL_BUDGET_SECONDS == 45.0


def test_020_settings_rejects_invalid_budget_and_caps() -> None:
    for field in ("LLM_TOTAL_BUDGET_SECONDS", "OLLAMA_TIMEOUT_SECONDS", "ORACLE_TIMEOUT", "OPENAI_COMPAT_TIMEOUT"):
        for value in (True, 0, -1, math.nan, math.inf, -math.inf):
            _raises(Exception, Settings, _env_file=None, **{field: value})
    for value in (True, 0, -1, math.nan, math.inf, -math.inf):
        _raises(
            ValueError,
            OracleClient,
            SimpleNamespace(
                ORACLE_API_URL="https://example.invalid",
                ORACLE_API_KEY="fixture",
                ORACLE_MODEL="fixture",
                ORACLE_TIMEOUT=value,
            ),
        )


def test_021_success_terminal_and_text() -> None:
    result = _run((ScriptedProvider("a", ["ok"]),))
    assert result.terminal is LLMCallTerminalV1.SUCCESS and result.final_text == "ok"


def test_022_success_receipt_contains_no_failure() -> None:
    result = _run((ScriptedProvider("a", ["ok"]),))
    assert result.receipts[0].failure_class is None


def test_023_empty_is_distinct_and_not_retried() -> None:
    provider = ScriptedProvider("a", [""], attempts=4)
    result = _run((provider,))
    assert provider.calls == 1 and result.final_failure is LLMFailureClassV1.EMPTY_RESPONSE


def test_024_malformed_is_nonretryable() -> None:
    provider = ScriptedProvider("a", [ProviderMalformedResponse("SECRET")], attempts=4)
    result = _run((provider,))
    assert provider.calls == 1 and result.final_failure is LLMFailureClassV1.MALFORMED_PROVIDER_RESPONSE


def test_025_projection_unsupported_has_no_receipt() -> None:
    provider = ScriptedProvider("a", [PromptProjectionUnsupported("x")])
    result = _run((provider,))
    assert result.terminal is LLMCallTerminalV1.NO_COMPATIBLE_PROVIDER and not result.receipts


def test_026_projection_unsupported_skips_to_sibling() -> None:
    a = ScriptedProvider("a", [PromptProjectionUnsupported("x")])
    b = ScriptedProvider("b", ["ok"])
    result = _run((a, b))
    assert result.succeeded and b.calls == 1


def test_027_auth_not_retried_same_provider() -> None:
    a = ScriptedProvider("a", [_status_error(401)], attempts=4)
    result = _run((a,))
    assert a.calls == 1 and result.final_failure is LLMFailureClassV1.AUTH_FAILURE


def test_028_forbidden_not_retried_same_provider() -> None:
    a = ScriptedProvider("a", [_status_error(403)], attempts=4)
    _run((a,))
    assert a.calls == 1


def test_029_not_found_not_retried_same_provider() -> None:
    a = ScriptedProvider("a", [_status_error(404)], attempts=4)
    _run((a,))
    assert a.calls == 1


def test_030_conflict_not_retried_same_provider() -> None:
    a = ScriptedProvider("a", [_status_error(409)], attempts=4)
    _run((a,))
    assert a.calls == 1


def test_031_bad_request_can_fallback() -> None:
    result = _run((ScriptedProvider("a", [_status_error(400)]), ScriptedProvider("b", ["ok"])))
    assert result.succeeded and result.final_provider == "b"


def test_032_rate_limit_classified() -> None:
    result = _run((ScriptedProvider("a", [_status_error(429)]),))
    assert result.final_failure is LLMFailureClassV1.RATE_LIMITED


def test_033_too_early_classified_transient() -> None:
    result = _run((ScriptedProvider("a", [_status_error(425)]),))
    assert result.final_failure is LLMFailureClassV1.TRANSIENT_HTTP


def test_034_transient_status_table() -> None:
    for status in (500, 502, 503, 504):
        result = _run((ScriptedProvider("a", [_status_error(status)]),))
        assert result.final_failure is LLMFailureClassV1.TRANSIENT_HTTP


def test_035_connection_failure_classified() -> None:
    request = httpx.Request("POST", "https://example.invalid")
    result = _run((ScriptedProvider("a", [httpx.ConnectError("SECRET", request=request)]),))
    assert result.final_failure is LLMFailureClassV1.CONNECTION_FAILURE


def test_036_transport_timeout_classified() -> None:
    result = _run((ScriptedProvider("a", [httpx.ReadTimeout("x")]),))
    assert result.final_failure is LLMFailureClassV1.TRANSPORT_TIMEOUT


def test_037_unknown_failure_classified() -> None:
    result = _run((ScriptedProvider("a", [RuntimeError("SECRET")]),))
    assert result.final_failure is LLMFailureClassV1.UNKNOWN_FAILURE


def test_038_configuration_failure_can_fallback() -> None:
    a = ScriptedProvider("a", ["never"], cap=0)
    b = ScriptedProvider("b", ["ok"])
    result = _run((a, b))
    assert a.calls == 0 and result.succeeded


def test_039_gemini_attempt_cap_includes_initial() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    provider = ScriptedProvider("a", [TimeoutError("x")], clock=clock, attempts=4)
    result = _run((provider,), clock=clock, sleeper=sleeper)
    assert provider.calls == 4 and result.terminal is LLMCallTerminalV1.RETRY_EXHAUSTED


def test_040_backoff_is_one_two_four() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    provider = ScriptedProvider("a", [TimeoutError("x")], clock=clock, attempts=4)
    _run((provider,), clock=clock, sleeper=sleeper)
    assert sleeper.delays == [1.0, 2.0, 4.0]


def test_041_no_sleep_after_final_attempt() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    provider = ScriptedProvider("a", [TimeoutError("x")], attempts=2)
    _run((provider,), clock=clock, sleeper=sleeper)
    assert sleeper.delays == [1.0]


def test_042_retry_after_delta_is_used() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    provider = ScriptedProvider("a", [_status_error(429, retry_after="3")], attempts=2)
    _run((provider,), clock=clock, sleeper=sleeper)
    assert sleeper.delays == [3.0]


def test_043_retry_after_http_date_is_relative() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    date = datetime.fromtimestamp(1_000_005, tz=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    executor = LLMCallExecutorV1(clock=clock, sleeper=sleeper, wall_clock=lambda: 1_000_000)
    provider = ScriptedProvider("a", [_status_error(429, retry_after=date)], attempts=2)
    executor.execute_sync((provider,), operation="plan", value=_plan(), budget=_budget(clock), generation={})
    assert sleeper.delays == [5.0]


def test_044_free_text_retry_delay_is_ignored() -> None:
    error = RuntimeError("retry_delay seconds:999")
    executor = LLMCallExecutorV1()
    receipt = executor._receipt_for_error("a", None, error, 1, 2)
    assert receipt.retry_after_seconds is None


def test_045_retry_after_not_stored_raw() -> None:
    result = _run((ScriptedProvider("a", [_status_error(429, retry_after="7")]),))
    assert result.receipts[0].retry_after_seconds == 7.0 and "Retry-After" not in repr(result.receipts)


def test_046_delay_at_remaining_is_not_slept() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    a = ScriptedProvider("a", [_status_error(429, retry_after="45")], attempts=2)
    b = ScriptedProvider("b", ["ok"])
    result = _run((a, b), clock=clock, sleeper=sleeper)
    assert not sleeper.delays and result.succeeded


def test_047_deadline_during_backoff_stops_retry() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock, multiplier=50)
    a = ScriptedProvider("a", [TimeoutError("x")], attempts=4)
    result = _run((a,), clock=clock, budget=_budget(clock, 10), sleeper=sleeper)
    assert a.calls == 1 and result.terminal is LLMCallTerminalV1.DEADLINE_EXCEEDED


def test_048_deadline_before_next_provider_stops_fallback() -> None:
    clock = FakeClock()
    a = ScriptedProvider("a", [("advance", 45, TimeoutError("x"))], clock=clock)
    b = ScriptedProvider("b", ["late"])
    result = _run((a, b), clock=clock)
    assert result.terminal is LLMCallTerminalV1.DEADLINE_EXCEEDED and b.calls == 0


def test_049_late_success_is_rejected() -> None:
    clock = FakeClock()
    a = ScriptedProvider("a", [("advance", 45, "late")], clock=clock)
    result = _run((a,), clock=clock)
    assert result.terminal is LLMCallTerminalV1.DEADLINE_EXCEEDED and result.final_text is None


def test_050_provider_cap_expiration_leaves_sibling_remainder() -> None:
    clock = FakeClock()
    a = ScriptedProvider("a", [("advance", 15, TimeoutError("x"))], clock=clock, cap=15)
    b = ScriptedProvider("b", ["ok"], clock=clock, cap=45)
    result = _run((a, b), clock=clock)
    assert result.succeeded and b.child_deadlines[0] == 45_000_000_000


def test_051_route_and_provider_timeout_are_distinct() -> None:
    clock = FakeClock()
    provider_timeout = _run(
        (ScriptedProvider("a", [("advance", 15, TimeoutError("x"))], clock=clock, cap=15),),
        clock=clock,
    )
    clock2 = FakeClock()
    route_timeout = _run(
        (ScriptedProvider("a", [("advance", 45, TimeoutError("x"))], clock=clock2),),
        clock=clock2,
    )
    assert provider_timeout.final_failure is LLMFailureClassV1.TRANSPORT_TIMEOUT
    assert route_timeout.terminal is LLMCallTerminalV1.DEADLINE_EXCEEDED


def test_052_single_retryable_provider_terminal() -> None:
    result = _run((ScriptedProvider("a", [TimeoutError("x")]),))
    assert result.terminal is LLMCallTerminalV1.RETRY_EXHAUSTED


def test_053_single_nonretryable_provider_terminal() -> None:
    result = _run((ScriptedProvider("a", [""]),))
    assert result.terminal is LLMCallTerminalV1.NON_RETRYABLE_FAILURE


def test_054_two_compatible_failures_terminal() -> None:
    result = _run((ScriptedProvider("a", [_status_error(401)]), ScriptedProvider("b", [""])))
    assert result.terminal is LLMCallTerminalV1.ALL_PROVIDERS_FAILED


def test_055_unsupported_retryable_unsupported_mixed_terminal() -> None:
    result = _run((
        ScriptedProvider("a", [PromptProjectionUnsupported("x")]),
        ScriptedProvider("b", [TimeoutError("x")]),
        ScriptedProvider("c", [PromptProjectionUnsupported("x")]),
    ))
    assert result.terminal is LLMCallTerminalV1.RETRY_EXHAUSTED


def test_056_timeout_then_success_is_success() -> None:
    result = _run((ScriptedProvider("a", [TimeoutError("x")]), ScriptedProvider("b", ["ok"])))
    assert result.succeeded and result.final_provider == "b"


def test_057_same_plan_identity_survives_retry_and_fallback() -> None:
    plan = _plan()
    a = ScriptedProvider("a", [TimeoutError("x")], attempts=2)
    b = ScriptedProvider("b", ["ok"])
    result = _run((a, b), value=plan)
    assert result.succeeded and all(value is plan for value in (*a.values, *b.values))


def test_058_receipts_never_contain_model_text() -> None:
    result = _run((ScriptedProvider("a", ["SECRET_MODEL_TEXT"]),))
    assert "SECRET_MODEL_TEXT" not in repr(result.receipts)


def test_059_receipts_never_contain_http_body() -> None:
    result = _run((ScriptedProvider("a", [_status_error(500)]),))
    assert "SECRET_BODY" not in repr(result.receipts)


def test_060_attempt_duration_excludes_backoff() -> None:
    clock = FakeClock()
    sleeper = FakeSleeper(clock)
    a = ScriptedProvider("a", [TimeoutError("x")], attempts=2)
    result = _run((a,), clock=clock, sleeper=sleeper)
    assert all(receipt.finished_ns == receipt.started_ns for receipt in result.receipts)


def test_061_audit_one_event_per_attempt() -> None:
    events: list[LLMCallAudit] = []
    result = _run((ScriptedProvider("a", [TimeoutError("x")], attempts=2),), audit_sink=lambda event: events.append(event) or True)
    assert len(events) == len(result.receipts) == 2


def test_062_audit_fallback_flag_only_on_sibling() -> None:
    events: list[LLMCallAudit] = []
    _run((ScriptedProvider("a", [""]), ScriptedProvider("b", ["ok"])), audit_sink=lambda event: events.append(event) or True)
    assert [event.fallback_used for event in events] == [False, True]


def test_063_audit_failure_cannot_change_success() -> None:
    def broken(_event):
        raise RuntimeError("collector")
    assert _run((ScriptedProvider("a", ["ok"]),), audit_sink=broken).succeeded


def test_064_execute_sync_twice_uses_clean_loops() -> None:
    provider = ScriptedProvider("a", ["ok", "ok"])
    executor = LLMCallExecutorV1()
    for _ in range(2):
        result = executor.execute_sync(
            (provider,), operation="text", value="x",
            budget=LLMCallBudgetV1.from_seconds(1), generation={},
        )
        assert result.succeeded


def test_065_execute_sync_rejects_active_loop() -> None:
    async def scenario():
        executor = LLMCallExecutorV1()
        error = _raises(
            ActiveEventLoopSyncBridgeError,
            executor.execute_sync,
            (ScriptedProvider("a", ["ok"]),),
            operation="text", value="x", budget=LLMCallBudgetV1.from_seconds(1), generation={},
        )
        assert "execute_async" in str(error)
    asyncio.run(scenario())


def test_066_execute_async_works_inside_active_loop() -> None:
    async def scenario():
        result = await LLMCallExecutorV1().execute_async(
            (ScriptedProvider("a", ["ok"]),), operation="text", value="x",
            budget=LLMCallBudgetV1.from_seconds(1), generation={},
        )
        assert result.succeeded
    asyncio.run(scenario())


def test_067_outer_timeout_cancels_transport_and_finalizes() -> None:
    state = {"calls": 0, "cancelled": 0, "finalized": 0, "pending": -1}
    class Blocking:
        provider_name = "blocking"
        model_name = "fixture"
        provider_timeout_seconds = 1
        async def attempt_async(self, **kwargs):
            state["calls"] += 1
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                state["cancelled"] += 1
                raise
            finally:
                state["finalized"] += 1
    async def scenario():
        result = await LLMCallExecutorV1().execute_async(
            (Blocking(),), operation="text", value="x",
            budget=LLMCallBudgetV1.from_seconds(0.01), generation={},
        )
        state["pending"] = len([task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()])
        return result
    result = asyncio.run(scenario())
    assert result.terminal is LLMCallTerminalV1.DEADLINE_EXCEEDED
    assert len(result.receipts) == 1
    assert result.receipts[0].failure_class is LLMFailureClassV1.DEADLINE_EXCEEDED
    assert state == {"calls": 1, "cancelled": 1, "finalized": 1, "pending": 0}


def test_068_no_detached_thread_is_created() -> None:
    source = inspect.getsource(LLMCallExecutorV1.execute_sync)
    assert "Thread" not in source and "asyncio.run" in source


def test_069_openai_async_client_is_loop_scoped() -> None:
    source = inspect.getsource(OpenAICompatibleClient.attempt_async)
    assert "async with httpx.AsyncClient" in source and "self.client = httpx" not in source


def test_070_ollama_async_client_is_loop_scoped() -> None:
    source = inspect.getsource(OllamaClient.attempt_async)
    assert "async with httpx.AsyncClient" in source and "self.client = httpx" not in source


class _FakeGeminiHTTP(httpx.AsyncClient):
    instances: list["_FakeGeminiHTTP"] = []
    def __init__(self, *args, **kwargs):
        super().__init__(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)))
        self.closed_flag = False
        self.__class__.instances.append(self)
    async def aclose(self):
        self.closed_flag = True
        await super().aclose()


class _FakeGeminiSDK:
    factories: list["_FakeGeminiSDK"] = []
    response: object = SimpleNamespace(text="ok")
    error: BaseException | None = None
    wait_forever = False
    calls = 0
    def __init__(self, *args, **kwargs):
        self.http_options = kwargs["http_options"]
        self.closed = False
        self.aio = self
        self.models = self
        self.__class__.factories.append(self)
    async def generate_content(self, **kwargs):
        self.__class__.calls += 1
        if self.wait_forever:
            await asyncio.Event().wait()
        if self.error is not None:
            raise self.error
        return self.response
    async def aclose(self):
        self.closed = True


class _OwnedMockHTTP(httpx.AsyncClient):
    instances: list["_OwnedMockHTTP"] = []
    handler = None
    def __init__(self, *args, **kwargs):
        assert callable(self.__class__.handler)
        super().__init__(
            timeout=kwargs.get("timeout"),
            transport=httpx.MockTransport(self.__class__.handler),
        )
        self.__class__.instances.append(self)


def _gemini_fixture() -> GeminiClient:
    client = object.__new__(GeminiClient)
    client.settings = SimpleNamespace(LLM_TOTAL_BUDGET_SECONDS=45)
    client.api_key = "fixture"
    client.model_name = "fixture-model"
    client.provider_timeout_seconds = 45
    client.generation_config = types.GenerateContentConfig(
        temperature=0.1,
        max_output_tokens=8,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True,
            maximum_remote_calls=None,
        ),
    )
    return client


def _reset_gemini_fakes() -> None:
    _FakeGeminiHTTP.instances = []
    _FakeGeminiSDK.factories = []
    _FakeGeminiSDK.response = SimpleNamespace(text="ok")
    _FakeGeminiSDK.error = None
    _FakeGeminiSDK.wait_forever = False
    _FakeGeminiSDK.calls = 0


def test_071_gemini_forces_httpx_async_path() -> None:
    _reset_gemini_fakes()
    with patch("ai_engine.gemini_client.httpx.AsyncClient", _FakeGeminiHTTP), patch("ai_engine.gemini_client.genai.Client", _FakeGeminiSDK):
        assert asyncio.run(_gemini_fixture().attempt_async(operation="text", value="x", child_budget=LLMCallBudgetV1.from_seconds(1), generation={})) == "ok"
    options = _FakeGeminiSDK.factories[0].http_options
    assert options.httpx_async_client is _FakeGeminiHTTP.instances[0] and options.aiohttp_client is None


def test_072_gemini_sdk_retry_attempts_is_one() -> None:
    _reset_gemini_fakes()
    with patch("ai_engine.gemini_client.httpx.AsyncClient", _FakeGeminiHTTP), patch("ai_engine.gemini_client.genai.Client", _FakeGeminiSDK):
        asyncio.run(_gemini_fixture().attempt_async(operation="text", value="x", child_budget=LLMCallBudgetV1.from_seconds(1), generation={}))
    assert _FakeGeminiSDK.factories[0].http_options.retry_options.attempts == 1


def test_073_gemini_connect_error_is_one_sdk_transport_call() -> None:
    calls = {"count": 0}
    async def handler(request):
        calls["count"] += 1
        raise httpx.ConnectError("SECRET", request=request)
    _OwnedMockHTTP.instances = []
    _OwnedMockHTTP.handler = handler
    with patch("ai_engine.gemini_client.httpx.AsyncClient", _OwnedMockHTTP):
        error = _raises(Exception, asyncio.run, _gemini_fixture().attempt_async(operation="text", value="x", child_budget=LLMCallBudgetV1.from_seconds(1), generation={}))
    assert isinstance(error, httpx.ConnectError)
    assert calls["count"] == 1 and _OwnedMockHTTP.instances[0].is_closed


def test_074_gemini_owned_clients_close_before_loop_exit() -> None:
    _reset_gemini_fakes()
    with patch("ai_engine.gemini_client.httpx.AsyncClient", _FakeGeminiHTTP), patch("ai_engine.gemini_client.genai.Client", _FakeGeminiSDK):
        asyncio.run(_gemini_fixture().attempt_async(operation="text", value="x", child_budget=LLMCallBudgetV1.from_seconds(1), generation={}))
    assert _FakeGeminiHTTP.instances[0].closed_flag and _FakeGeminiSDK.factories[0].closed


def test_075_gemini_submillisecond_budget_prevents_dispatch() -> None:
    _reset_gemini_fakes()
    client = _gemini_fixture()
    budget = LLMCallBudgetV1(__import__("time").perf_counter_ns() + 500_000)
    with patch("ai_engine.gemini_client.httpx.AsyncClient", _FakeGeminiHTTP), patch("ai_engine.gemini_client.genai.Client", _FakeGeminiSDK):
        _raises(TimeoutError, asyncio.run, client.attempt_async(operation="text", value="x", child_budget=budget, generation={}))
    assert _FakeGeminiSDK.calls == 0


def test_076_gemini_timeout_ms_positive_and_bounded() -> None:
    _reset_gemini_fakes()
    with patch("ai_engine.gemini_client.httpx.AsyncClient", _FakeGeminiHTTP), patch("ai_engine.gemini_client.genai.Client", _FakeGeminiSDK):
        asyncio.run(_gemini_fixture().attempt_async(operation="text", value="x", child_budget=LLMCallBudgetV1.from_seconds(1), generation={}))
    timeout_ms = _FakeGeminiSDK.factories[0].http_options.timeout
    assert 1 <= timeout_ms <= 1000


def test_077_gemini_cancellation_closes_and_leaves_no_pending_tasks() -> None:
    state = {"calls": 0, "cancelled": 0, "finalized": 0}
    async def handler(request):
        state["calls"] += 1
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state["cancelled"] += 1
            raise
        finally:
            state["finalized"] += 1
    _OwnedMockHTTP.instances = []
    _OwnedMockHTTP.handler = handler
    async def scenario():
        facade = FallbackAIClient((_gemini_fixture(),))
        result = await facade.execute_text_result_async("x", budget=LLMCallBudgetV1.from_seconds(0.01))
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        return result, pending
    with patch("ai_engine.gemini_client.httpx.AsyncClient", _OwnedMockHTTP):
        result, pending = asyncio.run(scenario())
    assert result.terminal is LLMCallTerminalV1.DEADLINE_EXCEEDED
    assert state == {"calls": 1, "cancelled": 1, "finalized": 1}
    assert _OwnedMockHTTP.instances[0].is_closed and not pending


def test_078_factory_single_provider_returns_facade() -> None:
    settings = Settings(_env_file=None, AI_PROVIDER="gemini", AI_FALLBACK_ORDER="gemini", GEMINI_API_KEY="fixture", LLM_FALLBACKS_ENABLED=False)
    client = build_ai_client(settings)
    assert isinstance(client, FallbackAIClient) and len(client.providers) == 1


def test_079_fallback_client_has_no_provider_scheduler_loop() -> None:
    source = inspect.getsource(FallbackAIClient)
    assert "for index, client in enumerate" not in source and "time.sleep" not in source


def test_080_concrete_providers_declare_one_attempt_except_gemini_policy() -> None:
    assert GeminiClient.llm_max_attempts == 4
    assert OpenAICompatibleClient.llm_max_attempts == OllamaClient.llm_max_attempts == 1


def test_081_ollama_prompt_plan_is_pre_http_unsupported() -> None:
    settings = Settings(_env_file=None, AI_PROVIDER="ollama")
    client = OllamaClient(settings)
    result = FallbackAIClient((client,)).execute_plan_result(_plan(), budget=LLMCallBudgetV1.from_seconds(1))
    assert result.terminal is LLMCallTerminalV1.NO_COMPATIBLE_PROVIDER and not result.receipts


def test_082_openai_malformed_envelope_is_distinct() -> None:
    _raises(ProviderMalformedResponse, OpenAICompatibleClient._extraer_texto_estricto, {})


def test_083_openai_empty_content_is_empty_not_malformed() -> None:
    assert OpenAICompatibleClient._extraer_texto_estricto({"choices": [{"message": {"content": ""}}]}) == ""


def test_084_reasoning_is_wrapped_and_cannot_bypass_executor() -> None:
    raw = ScriptedProvider("fixture", ["{}"])
    engine = ReasoningEngine(raw)
    assert isinstance(engine.ai_client, FallbackAIClient)
    assert ".generate_plan(" not in inspect.getsource(ReasoningEngine.process)


def test_085_persona_primary_and_repair_share_budget_symbol() -> None:
    source = inspect.getsource(Orchestrator._resolver_ruta_persona)
    assert source.count("budget=llm_budget") == 2 and source.count("_nuevo_budget_ruta_llm()") == 1


def test_086_oracle_stages_share_budget_symbol() -> None:
    source = inspect.getsource(Orchestrator._resolver_ruta_oracle) + inspect.getsource(Orchestrator._verbalizar_resultado_oracle)
    assert source.count("budget=llm_budget") >= 4 and "consultar_plan(" not in source


def test_087_oracle_cache_budget_begins_at_verbalization() -> None:
    source = inspect.getsource(Orchestrator._verbalizar_resultado_oracle)
    assert "if llm_budget is None" in source and "_nuevo_budget_ruta_llm()" in source


def test_088_health_children_share_one_total_deadline() -> None:
    clock = FakeClock()
    a = ScriptedProvider("a", [("advance", 15, TimeoutError("x"))], clock=clock, cap=15)
    b = ScriptedProvider("b", ["ok"], clock=clock, cap=45)
    result = _run((a, b), clock=clock, operation="health", value="")
    assert result.succeeded and b.child_deadlines[0] == 45_000_000_000


def test_089_diagnostic_path_is_bounded() -> None:
    provider = ScriptedProvider("a", ["ok"])
    facade = FallbackAIClient((provider,))
    assert facade.generate("diagnostic") == "ok" and provider.calls == 1


def test_090_main_lifespan_awaits_async_health_without_asyncio_run() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "await ai_client.health_check_async()" in source and "asyncio.run" not in source


def test_091_api_and_unity_contracts_have_no_budget_fields() -> None:
    from api.schemas import AccionSaliente, RespuestaAPI
    forbidden = {"deadline", "deadline_ns", "budget", "retry_after_seconds"}
    assert not forbidden.intersection(AccionSaliente.model_fields)
    assert not forbidden.intersection(RespuestaAPI.model_fields)


def test_092_audit_trace_v1_is_frozen_from_llm_receipts() -> None:
    names = {field.name for field in fields(AuditTrace)}
    assert not {"receipts", "deadline_ns", "failure_class", "retry_after_seconds"}.intersection(names)


def test_093_prompt_plan_has_no_timing_or_retry_fields() -> None:
    names = {field.name for field in fields(PromptPlanV1)}
    assert not {"deadline", "budget", "timeout", "retry"}.intersection(names)


def test_094_static_productive_paths_use_controlled_facade() -> None:
    reasoning = inspect.getsource(ReasoningEngine.process)
    persona = inspect.getsource(Orchestrator._resolver_ruta_persona)
    oracle = inspect.getsource(Orchestrator._resolver_ruta_oracle)
    assert "execute_plan_result" in reasoning
    assert "llm_adapter.ejecutar_plan" in persona
    assert "consultar_plan(" not in oracle and "providers=providers" in oracle


def test_095_terminal_precedence_contract_is_complete() -> None:
    assert {member.value for member in LLMCallTerminalV1} == {
        "SUCCESS", "DEADLINE_EXCEEDED", "NO_COMPATIBLE_PROVIDER",
        "RETRY_EXHAUSTED", "NON_RETRYABLE_FAILURE", "ALL_PROVIDERS_FAILED",
    }


class _SyncOnlyProvider(BaseAIClient):
    provider_name = "sync-only"
    model_name = "sync-only-fixture"
    provider_timeout_seconds = 1.0

    def __init__(self) -> None:
        self.sync_calls = 0

    def generate(self, prompt: str, **kwargs) -> str:
        del prompt, kwargs
        self.sync_calls += 1
        raise AssertionError("SYNC_PATH_MUST_NOT_BE_CALLED")


def test_096_sync_only_base_client_fails_closed() -> None:
    provider = _SyncOnlyProvider()
    result = _run((provider,), operation="text", value="fixture")
    assert result.terminal is LLMCallTerminalV1.NON_RETRYABLE_FAILURE
    assert result.final_failure is LLMFailureClassV1.CONFIGURATION_ERROR
    assert len(result.receipts) == 1


def test_097_sync_generate_is_never_invoked_or_recursed() -> None:
    provider = _SyncOnlyProvider()
    result = _run((provider,), operation="text", value="fixture")
    assert provider.sync_calls == 0
    assert len(result.receipts) == 1


def test_098_executor_source_has_no_sync_provider_fallback() -> None:
    source = inspect.getsource(LLMCallExecutorV1._invoke_provider)
    for forbidden in (".generate(", ".generate_plan(", ".health_check(", ".consultar_plan("):
        assert forbidden not in source
    assert "attempt_async" in source


def test_099_gemini_has_no_legacy_sync_client_path() -> None:
    source = inspect.getsource(GeminiClient.attempt_async)
    assert "legacy_client" not in source
    assert "await aio_client.models.generate_content" in source


def test_100_openai_compatible_has_no_legacy_sync_client_path() -> None:
    source = inspect.getsource(OpenAICompatibleClient.attempt_async)
    assert "legacy_client" not in source
    assert "await client.post" in source


def test_101_keyboard_interrupt_propagates_without_retry_or_fallback() -> None:
    primary = ScriptedProvider("primary", [KeyboardInterrupt()])
    sibling = ScriptedProvider("sibling", ["must-not-run"])
    events: list[LLMCallAudit] = []
    _raises(KeyboardInterrupt, _run, (primary, sibling), audit_sink=events.append)
    assert primary.calls == 1 and sibling.calls == 0 and not events


def test_102_system_exit_propagates_without_retry_or_fallback() -> None:
    primary = ScriptedProvider("primary", [SystemExit(7)])
    sibling = ScriptedProvider("sibling", ["must-not-run"])
    events: list[LLMCallAudit] = []
    error = _raises(SystemExit, _run, (primary, sibling), audit_sink=events.append)
    assert error.code == 7
    assert primary.calls == 1 and sibling.calls == 0 and not events


def test_103_external_cancellation_propagates_and_cleans() -> None:
    state = {"calls": 0, "finalized": 0}

    class CancelledProvider(ScriptedProvider):
        async def attempt_async(self, **kwargs):
            del kwargs
            state["calls"] += 1
            try:
                raise asyncio.CancelledError()
            finally:
                state["finalized"] += 1

    async def scenario():
        provider = CancelledProvider("cancelled", [""])
        sibling = ScriptedProvider("sibling", ["must-not-run"])
        events: list[LLMCallAudit] = []
        try:
            await _executor(FakeClock()).execute_async(
                (provider, sibling),
                operation="text",
                value="fixture",
                budget=_budget(FakeClock()),
                generation={},
                audit_sink=events.append,
            )
        except asyncio.CancelledError:
            pending = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task() and not task.done()
            ]
            return provider, sibling, events, pending
        raise AssertionError("CancelledError debía propagarse")

    provider, sibling, events, pending = asyncio.run(scenario())
    assert state == {"calls": 1, "finalized": 1}
    assert provider.calls == 0 and sibling.calls == 0
    assert not events and not pending


class _ExpiredBeforeRepairAdapter:
    def __init__(self, clock: FakeClock, budget: LLMCallBudgetV1) -> None:
        self.clock = clock
        self.budget = budget
        self.repair_provider = ScriptedProvider("repair", ["must-not-run"])
        self.primary_calls = 0
        self.repair_control_calls = 0

    def ejecutar_plan(self, plan, **kwargs) -> str:
        assert kwargs["budget"] is self.budget
        self.primary_calls += 1
        self.clock.advance(45)
        return "SECRET_INVALID_PRIMARY_OUTPUT"

    def ejecutar_plan_result(self, plan, **kwargs) -> LLMCallResultV1:
        assert kwargs["budget"] is self.budget
        self.repair_control_calls += 1
        return _executor(self.clock).execute_sync(
            (self.repair_provider,),
            operation="plan",
            value=plan,
            budget=self.budget,
            generation={},
        )


def _expired_repair_scenario(*, oracle: bool):
    clock = FakeClock()
    budget = _budget(clock)
    adapter = _ExpiredBeforeRepairAdapter(clock, budget)
    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.llm_adapter = adapter
    orchestrator._nuevo_budget_ruta_llm = lambda: budget
    identity = IdentityContextV1.from_snapshot(
        IdentitySnapshotProvider(build_static_self_v1(), BehaviorCore()).capture()
    )
    event = EventoEntrante(
        origen="terminal",
        tipo_evento="comando_usuario",
        id_transaccion="root-021-expired-repair",
        detalles={"mensaje": "hola", "solicita_decision": True},
    )
    if oracle:
        result = orchestrator._verbalizar_resultado_oracle(
            event,
            {},
            "",
            "exploradora",
            OracleResult(
                pregunta_original="hola",
                contenido_tecnico="resultado tecnico",
                fuente="api",
            ),
            identity_context=identity,
            llm_budget=budget,
        )
    else:
        result = orchestrator._resolver_ruta_persona(
            event,
            {},
            "",
            "exploradora",
            identity_context=identity,
        )
    return result, adapter


def test_104_persona_expired_before_repair_dispatches_zero_provider_calls() -> None:
    _, adapter = _expired_repair_scenario(oracle=False)
    assert adapter.primary_calls == 1 and adapter.repair_control_calls == 1
    assert adapter.repair_provider.calls == 0


def test_105_persona_expired_repair_outcome_remains_first_attempt() -> None:
    result, _ = _expired_repair_scenario(oracle=False)
    assert result.outcome.attempt is DecisionAttempt.FIRST
    assert result.outcome.retry_count == 0
    assert DecisionReasonCode.RETRY_REPAIRED not in result.outcome.reason_codes


def test_106_oracle_expired_before_repair_dispatches_zero_provider_calls() -> None:
    _, adapter = _expired_repair_scenario(oracle=True)
    assert adapter.primary_calls == 1 and adapter.repair_control_calls == 1
    assert adapter.repair_provider.calls == 0


def test_107_oracle_expired_repair_outcome_remains_first_attempt() -> None:
    result, _ = _expired_repair_scenario(oracle=True)
    assert result.outcome.attempt is DecisionAttempt.FIRST
    assert result.outcome.retry_count == 0
    assert DecisionReasonCode.RETRY_REPAIRED not in result.outcome.reason_codes


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
    print(f"[OK] ROOT-021 LLM call control: {len(tests)}/{len(tests)} PASS")


if __name__ == "__main__":
    main()
