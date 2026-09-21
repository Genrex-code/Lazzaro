"""Pruebas permanentes ROOT-023 para readiness efectiva y generacional."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from ai_engine.llm_call_control import (
    LLMCallResultV1,
    LLMCallTerminalV1,
    LLMFailureClassV1,
    ProviderAttemptReceiptV1,
)
from core.runtime_capabilities import (
    CapabilityName,
    CapabilityReasonCode,
    CapabilityStatus,
    RuntimeCapabilityHolder,
    RuntimeOverall,
    RuntimeVerification,
)
from memory.memory_write_types import (
    MemoryWriteIssueCode,
    MemoryWriteKind,
    MemoryWriteReceipt,
    MemoryWriteStatus,
)
from memory.retrieval_types import (
    MemoryRetrievalReceipt,
    RetrievalHealth,
    StoreRetrievalReceipt,
)


def _attempt(
    provider: str,
    failure: LLMFailureClassV1 | None,
) -> ProviderAttemptReceiptV1:
    return ProviderAttemptReceiptV1(
        provider=provider,
        model_id=None,
        failure_class=failure,
        error_type=None,
        http_status=None,
        retry_after_seconds=None,
        started_ns=1,
        finished_ns=2,
    )


def _llm_success(
    provider: str = "primary",
    receipts: tuple[ProviderAttemptReceiptV1, ...] = (),
) -> LLMCallResultV1:
    return LLMCallResultV1(
        terminal=LLMCallTerminalV1.SUCCESS,
        final_text="ok",
        final_provider=provider,
        final_model_id=None,
        final_failure=None,
        receipts=receipts,
    )


def _llm_failure(
    terminal: LLMCallTerminalV1,
    failure: LLMFailureClassV1,
    receipts: tuple[ProviderAttemptReceiptV1, ...] | None = None,
) -> LLMCallResultV1:
    return LLMCallResultV1(
        terminal=terminal,
        final_text=None,
        final_provider=None,
        final_model_id=None,
        final_failure=failure,
        receipts=receipts or (_attempt("primary", failure),),
    )


def _read(health: RetrievalHealth) -> MemoryRetrievalReceipt:
    source_health = (
        RetrievalHealth.FAILED
        if health is RetrievalHealth.FAILED
        else health
    )
    source = StoreRetrievalReceipt(health=source_health)
    return MemoryRetrievalReceipt(
        health=health,
        results=(),
        episodic=source,
        semantic=source,
    )


def _write(
    status: MemoryWriteStatus,
    *,
    durable: bool = False,
    reconciliation_required: bool = False,
    reason_codes: tuple[MemoryWriteIssueCode, ...] = (),
) -> MemoryWriteReceipt:
    return MemoryWriteReceipt(
        operation_id="op",
        idempotency_key="key",
        operation_kind=MemoryWriteKind.EPISODIC,
        status=status,
        durable=durable,
        reconciliation_required=reconciliation_required,
        reason_codes=reason_codes,
    )


def _status(sink, name: CapabilityName) -> CapabilityStatus:
    return sink.snapshot().capability(name).status


def _ready_sink():
    holder = RuntimeCapabilityHolder()
    sink = holder.begin_generation()
    sink.mark_orchestration_available()
    return holder, sink


def test_001_ready_partial_keeps_unknown_visible() -> None:
    _, sink = _ready_sink()
    snapshot = sink.snapshot()
    assert snapshot.overall is RuntimeOverall.READY
    assert snapshot.verification is RuntimeVerification.PARTIAL
    assert _status(sink, CapabilityName.LLM_PLAN) is CapabilityStatus.UNKNOWN


def test_002_ready_complete_requires_all_capability_evidence() -> None:
    _, sink = _ready_sink()
    sink.observe_llm("plan", _llm_success())
    sink.observe_memory_read(_read(RetrievalHealth.OK))
    sink.observe_memory_write(_write(MemoryWriteStatus.CREATED, durable=True))
    snapshot = sink.snapshot()
    assert snapshot.overall is RuntimeOverall.READY
    assert snapshot.verification is RuntimeVerification.COMPLETE


def test_003_degraded_partial_and_complete_are_distinct() -> None:
    _, sink = _ready_sink()
    sink.observe_memory_read(_read(RetrievalHealth.PARTIAL))
    assert sink.snapshot().overall is RuntimeOverall.DEGRADED
    assert sink.snapshot().verification is RuntimeVerification.PARTIAL
    sink.observe_llm("plan", _llm_success())
    sink.observe_memory_write(_write(MemoryWriteStatus.AMBIGUOUS))
    assert sink.snapshot().verification is RuntimeVerification.COMPLETE
    assert sink.snapshot().overall is RuntimeOverall.DEGRADED


def test_004_startup_incomplete_and_shutdown_are_unavailable() -> None:
    holder = RuntimeCapabilityHolder()
    assert holder.snapshot().overall is RuntimeOverall.UNAVAILABLE
    sink = holder.begin_generation()
    sink.mark_orchestration_available()
    assert holder.close_generation(sink.generation_id)
    assert holder.snapshot().overall is RuntimeOverall.UNAVAILABLE
    assert not holder.snapshot().generation_open
    assert (
        holder.snapshot().capability(CapabilityName.ORCHESTRATION).reason_code
        is CapabilityReasonCode.SHUTDOWN
    )


def test_005_unknown_transitions_cover_success_partial_and_terminal_failure() -> None:
    _, available = _ready_sink()
    available.observe_memory_read(_read(RetrievalHealth.OK_EMPTY))
    assert _status(available, CapabilityName.MEMORY_READ) is CapabilityStatus.AVAILABLE

    _, degraded = _ready_sink()
    degraded.observe_memory_read(_read(RetrievalHealth.PARTIAL))
    assert _status(degraded, CapabilityName.MEMORY_READ) is CapabilityStatus.DEGRADED

    _, unavailable = _ready_sink()
    unavailable.observe_memory_read(_read(RetrievalHealth.FAILED))
    assert _status(unavailable, CapabilityName.MEMORY_READ) is CapabilityStatus.UNAVAILABLE


def test_006_transient_llm_failure_preserves_available() -> None:
    _, sink = _ready_sink()
    sink.observe_llm("plan", _llm_success())
    before = sink.snapshot()
    sink.observe_llm(
        "plan",
        _llm_failure(
            LLMCallTerminalV1.RETRY_EXHAUSTED,
            LLMFailureClassV1.TRANSPORT_TIMEOUT,
        ),
    )
    after = sink.snapshot()
    assert _status(sink, CapabilityName.LLM_PLAN) is CapabilityStatus.AVAILABLE
    assert after.revision == before.revision


def test_007_available_degrades_and_recovers_from_memory_receipts() -> None:
    _, sink = _ready_sink()
    sink.observe_memory_read(_read(RetrievalHealth.OK))
    sink.observe_memory_read(_read(RetrievalHealth.PARTIAL))
    assert _status(sink, CapabilityName.MEMORY_READ) is CapabilityStatus.DEGRADED
    sink.observe_memory_read(_read(RetrievalHealth.OK_EMPTY))
    assert _status(sink, CapabilityName.MEMORY_READ) is CapabilityStatus.AVAILABLE


def test_008_available_becomes_unavailable_and_recovers_same_generation() -> None:
    _, sink = _ready_sink()
    sink.observe_memory_read(_read(RetrievalHealth.OK))
    sink.observe_memory_read(_read(RetrievalHealth.FAILED))
    assert _status(sink, CapabilityName.MEMORY_READ) is CapabilityStatus.UNAVAILABLE
    sink.observe_memory_read(_read(RetrievalHealth.OK))
    assert _status(sink, CapabilityName.MEMORY_READ) is CapabilityStatus.AVAILABLE


def test_009_old_and_closed_generation_updates_are_rejected() -> None:
    holder = RuntimeCapabilityHolder()
    old = holder.begin_generation()
    current = holder.begin_generation()
    assert not old.observe_memory_read(_read(RetrievalHealth.OK))
    assert _status(current, CapabilityName.MEMORY_READ) is CapabilityStatus.UNKNOWN
    assert holder.close_generation(current.generation_id)
    assert not current.observe_memory_read(_read(RetrievalHealth.OK))


def test_010_primary_failure_with_compatible_fallback_is_available() -> None:
    _, sink = _ready_sink()
    result = _llm_success(
        "fallback",
        receipts=(
            _attempt("primary", LLMFailureClassV1.CONNECTION_FAILURE),
            _attempt("fallback", None),
        ),
    )
    sink.observe_llm("plan", result)
    assert _status(sink, CapabilityName.LLM_PLAN) is CapabilityStatus.AVAILABLE
    assert sink.snapshot().overall is RuntimeOverall.READY


def test_011_all_providers_transient_failure_is_request_scoped() -> None:
    _, sink = _ready_sink()
    failure = _llm_failure(
        LLMCallTerminalV1.ALL_PROVIDERS_FAILED,
        LLMFailureClassV1.CONNECTION_FAILURE,
        receipts=(
            _attempt("one", LLMFailureClassV1.CONNECTION_FAILURE),
            _attempt("two", LLMFailureClassV1.TRANSPORT_TIMEOUT),
        ),
    )
    sink.observe_llm("plan", failure)
    assert _status(sink, CapabilityName.LLM_PLAN) is CapabilityStatus.UNKNOWN
    sink.observe_llm("plan", _llm_success())
    sink.observe_llm("plan", failure)
    assert _status(sink, CapabilityName.LLM_PLAN) is CapabilityStatus.AVAILABLE


def test_012_no_compatible_provider_only_disables_plan() -> None:
    _, sink = _ready_sink()
    sink.observe_llm("health", _llm_success("ollama"))
    sink.observe_llm(
        "plan",
        _llm_failure(
            LLMCallTerminalV1.NO_COMPATIBLE_PROVIDER,
            LLMFailureClassV1.PROJECTION_UNSUPPORTED,
        ),
    )
    assert _status(sink, CapabilityName.LLM_GENERATION) is CapabilityStatus.AVAILABLE
    assert _status(sink, CapabilityName.LLM_PLAN) is CapabilityStatus.UNAVAILABLE
    assert sink.snapshot().overall is RuntimeOverall.DEGRADED


def test_013_health_success_does_not_claim_plan_success() -> None:
    _, sink = _ready_sink()
    sink.observe_llm("health", _llm_success())
    assert _status(sink, CapabilityName.LLM_GENERATION) is CapabilityStatus.AVAILABLE
    assert _status(sink, CapabilityName.LLM_PLAN) is CapabilityStatus.UNKNOWN


def test_014_memory_read_mapping_is_exact() -> None:
    expected = {
        RetrievalHealth.OK: CapabilityStatus.AVAILABLE,
        RetrievalHealth.OK_EMPTY: CapabilityStatus.AVAILABLE,
        RetrievalHealth.PARTIAL: CapabilityStatus.DEGRADED,
        RetrievalHealth.FAILED: CapabilityStatus.UNAVAILABLE,
    }
    for health, status in expected.items():
        _, sink = _ready_sink()
        sink.observe_memory_read(_read(health))
        assert _status(sink, CapabilityName.MEMORY_READ) is status


def test_015_memory_write_mapping_and_recovery_are_exact() -> None:
    successful = (
        _write(MemoryWriteStatus.CREATED, durable=True),
        _write(MemoryWriteStatus.ALREADY_EXISTS, durable=True),
    )
    for receipt in successful:
        _, sink = _ready_sink()
        sink.observe_memory_write(receipt)
        assert _status(sink, CapabilityName.MEMORY_WRITE) is CapabilityStatus.AVAILABLE

    degraded = (
        _write(MemoryWriteStatus.FAILED),
        _write(MemoryWriteStatus.AMBIGUOUS),
        _write(
            MemoryWriteStatus.CREATED,
            durable=True,
            reconciliation_required=True,
        ),
    )
    for receipt in degraded:
        _, sink = _ready_sink()
        sink.observe_memory_write(receipt)
        assert _status(sink, CapabilityName.MEMORY_WRITE) is CapabilityStatus.DEGRADED
        sink.observe_memory_write(_write(MemoryWriteStatus.CREATED, durable=True))
        assert _status(sink, CapabilityName.MEMORY_WRITE) is CapabilityStatus.AVAILABLE


def test_016_invalid_intent_and_idempotency_conflict_do_not_change_health() -> None:
    for issue in (
        MemoryWriteIssueCode.INVALID_INTENT,
        MemoryWriteIssueCode.IDEMPOTENCY_CONFLICT,
    ):
        _, sink = _ready_sink()
        before = sink.snapshot()
        sink.observe_memory_write(
            _write(MemoryWriteStatus.FAILED, reason_codes=(issue,))
        )
        assert _status(sink, CapabilityName.MEMORY_WRITE) is CapabilityStatus.UNKNOWN
        assert sink.snapshot().revision == before.revision


def test_017_composition_distinguishes_constructed_from_usable() -> None:
    _, sink = _ready_sink()
    sink.observe_memory_composition(backend_available=False, ledger_open=True)
    assert _status(sink, CapabilityName.MEMORY_READ) is CapabilityStatus.UNAVAILABLE
    assert _status(sink, CapabilityName.MEMORY_WRITE) is CapabilityStatus.UNAVAILABLE

    _, ledger_sink = _ready_sink()
    ledger_sink.observe_memory_composition(backend_available=True, ledger_open=False)
    assert _status(ledger_sink, CapabilityName.MEMORY_READ) is CapabilityStatus.UNKNOWN
    assert _status(ledger_sink, CapabilityName.MEMORY_WRITE) is CapabilityStatus.UNAVAILABLE


def test_018_health_failure_is_degraded_but_logging_failure_is_not_a_capability() -> None:
    _, sink = _ready_sink()
    sink.observe_llm_health_boolean(False)
    assert _status(sink, CapabilityName.LLM_GENERATION) is CapabilityStatus.DEGRADED
    snapshot = sink.snapshot()
    try:
        raise RuntimeError("synthetic logging failure")
    except RuntimeError:
        pass
    assert sink.snapshot() == snapshot


def test_019_structural_provider_failure_is_unavailable_and_success_recovers() -> None:
    _, sink = _ready_sink()
    failure = _llm_failure(
        LLMCallTerminalV1.ALL_PROVIDERS_FAILED,
        LLMFailureClassV1.AUTH_FAILURE,
        receipts=(
            _attempt("one", LLMFailureClassV1.AUTH_FAILURE),
            _attempt("two", LLMFailureClassV1.CONFIGURATION_ERROR),
        ),
    )
    sink.observe_llm("text", failure)
    assert _status(sink, CapabilityName.LLM_GENERATION) is CapabilityStatus.UNAVAILABLE
    sink.observe_llm("text", _llm_success("two"))
    assert _status(sink, CapabilityName.LLM_GENERATION) is CapabilityStatus.AVAILABLE


def test_019a_deadline_after_structural_receipt_does_not_prove_exhaustion() -> None:
    deadline_after_auth = _llm_failure(
        LLMCallTerminalV1.DEADLINE_EXCEEDED,
        LLMFailureClassV1.DEADLINE_EXCEEDED,
        receipts=(_attempt("primary", LLMFailureClassV1.AUTH_FAILURE),),
    )
    _, unknown = _ready_sink()
    unknown.observe_llm("plan", deadline_after_auth)
    assert _status(unknown, CapabilityName.LLM_PLAN) is CapabilityStatus.UNKNOWN

    _, available = _ready_sink()
    available.observe_llm("plan", _llm_success())
    deadline_after_configuration = _llm_failure(
        LLMCallTerminalV1.DEADLINE_EXCEEDED,
        LLMFailureClassV1.DEADLINE_EXCEEDED,
        receipts=(
            _attempt("primary", LLMFailureClassV1.CONFIGURATION_ERROR),
        ),
    )
    available.observe_llm("plan", deadline_after_configuration)
    assert _status(available, CapabilityName.LLM_PLAN) is CapabilityStatus.AVAILABLE


def test_020_memory_manager_publishes_existing_receipt_without_second_search() -> None:
    from memory.memory_manager import MemoryManager

    _, sink = _ready_sink()
    manager = MemoryManager.__new__(MemoryManager)
    calls = []

    class Retriever:
        def buscar_con_recibo(self, **kwargs):
            calls.append(kwargs)
            return _read(RetrievalHealth.OK_EMPTY)

    manager.retriever = Retriever()
    manager.set_capability_observer(sink)
    receipt = manager.buscar_recuerdos_con_recibo("hola")
    assert receipt.health is RetrievalHealth.OK_EMPTY
    assert len(calls) == 1
    assert _status(sink, CapabilityName.MEMORY_READ) is CapabilityStatus.AVAILABLE


def test_021_two_lifespan_generations_close_and_reject_late_evidence() -> None:
    import main

    holder = RuntimeCapabilityHolder()
    app = main.create_app()
    app.state.runtime_capabilities = holder
    captured_sinks = []

    class Resource:
        def close(self):
            pass

    class Memory(Resource):
        def __init__(self, **_kwargs):
            self.memory_write_ledger = SimpleNamespace(is_open=True)
            self._observer = None

        def set_capability_observer(self, observer):
            self._observer = observer
            captured_sinks.append(observer)

    class AI(Resource):
        def set_capability_observer(self, observer):
            self._observer = observer

        async def health_check_result_async(self):
            result = _llm_success()
            self._observer.observe_llm("health", result)
            return result

    settings = SimpleNamespace(
        CHROMA_DB_PATH="unused",
        MEMORY_WRITE_LEDGER_PATH=":memory:",
        ACTION_LEDGER_PATH=":memory:",
    )
    chroma = SimpleNamespace(disponible=True, close=lambda: None)
    orchestrator = SimpleNamespace(close=lambda: None)

    async def exercise():
        generation_ids = []
        for _ in range(2):
            async with main.lifespan(app):
                snapshot = holder.snapshot()
                generation_ids.append(snapshot.generation_id)
                assert snapshot.overall is RuntimeOverall.READY
                assert snapshot.verification is RuntimeVerification.PARTIAL
            assert holder.snapshot().overall is RuntimeOverall.UNAVAILABLE
        return generation_ids

    with ExitStack() as stack:
        stack.enter_context(patch.object(main, "Settings", lambda: settings))
        stack.enter_context(patch.object(main, "BehaviorCore", lambda: object()))
        stack.enter_context(patch.object(main, "build_static_self_v1", lambda: object()))
        stack.enter_context(patch.object(main, "IdentitySnapshotProvider", lambda *_: object()))
        stack.enter_context(patch.object(main, "ChromaClient", lambda **_: chroma))
        stack.enter_context(patch.object(main, "MemoryManager", Memory))
        stack.enter_context(patch.object(main, "describe_ai_backend", lambda _: "fake"))
        stack.enter_context(patch.object(main, "build_ai_client", lambda _: AI()))
        stack.enter_context(patch.object(main, "ReasoningEngine", lambda **_: object()))
        stack.enter_context(patch.object(main.ToolRegistry, "listar_nombres", lambda: []))
        stack.enter_context(patch.object(main, "Orchestrator", lambda **_: orchestrator))
        generation_ids = asyncio.run(exercise())

    assert generation_ids[0] != generation_ids[1]
    assert len(captured_sinks) == 2
    assert not captured_sinks[0].observe_memory_read(_read(RetrievalHealth.OK))
    assert not captured_sinks[1].observe_memory_read(_read(RetrievalHealth.OK))


def test_022_startup_failure_closes_generation() -> None:
    import main

    app = main.create_app()
    holder = app.state.runtime_capabilities

    async def exercise():
        try:
            async with main.lifespan(app):
                raise AssertionError("unreachable")
        except RuntimeError as error:
            assert str(error) == "startup failed"
        else:
            raise AssertionError("startup failure was swallowed")

    with patch.object(main, "Settings", side_effect=RuntimeError("startup failed")):
        asyncio.run(exercise())
    assert holder.snapshot().overall is RuntimeOverall.UNAVAILABLE
    assert not holder.snapshot().generation_open
    assert (
        holder.snapshot().capability(CapabilityName.ORCHESTRATION).reason_code
        is CapabilityReasonCode.STARTUP_INCOMPLETE
    )


def test_023_nominal_startup_claim_was_removed() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert "Sistemas nominales" not in source
    assert "readiness=%s verification=%s" in source


def test_024_fallback_client_publishes_result_and_observer_is_fail_soft() -> None:
    from ai_engine.fallback_ai_client import FallbackAIClient

    client = FallbackAIClient((SimpleNamespace(provider_name="fake"),))
    _, sink = _ready_sink()
    client.set_capability_observer(sink)
    result = _llm_success("fake")
    client._publish_capability_evidence("plan", result)
    assert _status(sink, CapabilityName.LLM_PLAN) is CapabilityStatus.AVAILABLE

    class BrokenObserver:
        def observe_llm(self, *_args):
            raise RuntimeError("observer failure")

    client.set_capability_observer(BrokenObserver())
    assert client._publish_capability_evidence("plan", result) is None
