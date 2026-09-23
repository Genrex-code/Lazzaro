"""ROOT-026: fallo de Triage con autorizacion independiente del canal."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ai_engine.intent_types import IntentType, Route
from ai_engine.triage_contracts import (
    TriageDecisionV1,
    TriageFallbackStatusV1,
    TriageModelStatusV1,
    TriageOutcomeV1,
    TriageReasonCodeV1,
    TriageSourceV1,
)
from ai_engine.triage_router import TriageRouter
from api.schemas import EventoEntrante
from core.action_ledger import (
    ActionLedger,
    ActionTransitionEvent,
    TransactionCompletionStatus,
    TurnProcessingError,
    TurnReconciliationRequiredError,
    TurnUoWStatus,
)
from core.audit_trace import AuditTrace
from core.orchestrator import Orchestrator
from core.triage_boundary import (
    TriageBoundaryFailureCodeV1,
    TriageBoundaryFailureV1,
    safe_triage_error_type,
    triage_evaluation_failure,
    validate_triage_legacy_result,
)
from terminal_client.test_chat import _orquestador_aislado


ORIGINS = ("terminal", "unity", "sandbox", "roblox", "otro")
CANARY = "ROOT026_CANARY_SECRET"


class RecordingWriter:
    def __init__(self) -> None:
        self.traces: list[AuditTrace] = []

    def write(self, trace: AuditTrace) -> bool:
        self.traces.append(trace)
        return True


class RaisingWriter:
    def __init__(self) -> None:
        self.attempted_stages: list[str] = []

    def write(self, trace: AuditTrace) -> bool:
        self.attempted_stages.append(trace.stage)
        raise OSError("synthetic writer outage")


class StaticTriage:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.calls = 0

    def route_event(self, evento: dict, estado_agente: dict) -> object:
        del evento, estado_agente
        self.calls += 1
        return self.payload


class RaisingTriage:
    def __init__(self, message: str = "synthetic triage failure") -> None:
        self.message = message
        self.calls = 0

    def route_event(self, evento: dict, estado_agente: dict) -> dict:
        del evento, estado_agente
        self.calls += 1
        raise RuntimeError(self.message)


def _event(
    origin: str,
    transaction_id: str,
    *,
    event_type: str = "comando_usuario",
    details: dict | None = None,
) -> EventoEntrante:
    payload = dict(details or {})
    if event_type == "comando_usuario":
        payload.setdefault("mensaje", "hola")
        payload.setdefault("solicita_decision", True)
    return EventoEntrante(
        origen=origin,
        tipo_evento=event_type,
        id_transaccion=transaction_id,
        detalles=payload,
    )


def _decision(kind: str) -> TriageDecisionV1:
    if kind == "FAILED":
        return TriageDecisionV1(
            outcome=TriageOutcomeV1.FAILED,
            source=None,
            semantic_intent=None,
            route=None,
            reason_code=TriageReasonCodeV1.MODEL_ERROR,
            model_status=TriageModelStatusV1.ERROR,
            fallback_status=TriageFallbackStatusV1.NO_MATCH,
            human_reason="Fallo tecnico contenido.",
        )
    if kind == "UNCERTAIN":
        return TriageDecisionV1(
            outcome=TriageOutcomeV1.UNCERTAIN,
            source=None,
            semantic_intent=None,
            route=None,
            reason_code=TriageReasonCodeV1.NO_ACCEPTABLE_CANDIDATE,
            model_status=TriageModelStatusV1.LOW_CONFIDENCE,
            fallback_status=TriageFallbackStatusV1.NO_MATCH,
            raw_label="SOCIAL",
            human_reason="No hubo candidato aceptable.",
        )
    if kind == "DEGRADED":
        return TriageDecisionV1(
            outcome=TriageOutcomeV1.DEGRADED,
            source=TriageSourceV1.FALLBACK,
            semantic_intent=IntentType.SOCIAL,
            route=Route.PERSONA,
            reason_code=TriageReasonCodeV1.FALLBACK_ACCEPTED,
            model_status=TriageModelStatusV1.ERROR,
            fallback_status=TriageFallbackStatusV1.ACCEPTED,
            human_reason="Fallback utilizable.",
        )
    if kind == "UNKNOWN":
        return TriageDecisionV1(
            outcome=TriageOutcomeV1.DECIDED,
            source=TriageSourceV1.MODEL,
            semantic_intent=IntentType.UNKNOWN,
            route=Route.PERSONA,
            reason_code=TriageReasonCodeV1.MODEL_ACCEPTED,
            model_status=TriageModelStatusV1.ACCEPTED,
            fallback_status=TriageFallbackStatusV1.NOT_ATTEMPTED,
            raw_label="UNKNOWN",
            human_reason="UNKNOWN semantico aceptado.",
        )
    return TriageDecisionV1(
        outcome=TriageOutcomeV1.DECIDED,
        source=TriageSourceV1.MODEL,
        semantic_intent=IntentType.SOCIAL,
        route=Route.PERSONA,
        reason_code=TriageReasonCodeV1.MODEL_ACCEPTED,
        model_status=TriageModelStatusV1.ACCEPTED,
        fallback_status=TriageFallbackStatusV1.NOT_ATTEMPTED,
        raw_label="SOCIAL",
        human_reason="Decision valida.",
    )


def _legacy(kind: str, confidence: float = 0.5) -> dict:
    return _decision(kind).to_legacy_dict(confidence=confidence)


def _forbid_downstream(orchestrator: Orchestrator) -> dict[str, int]:
    counters: dict[str, int] = {}

    def forbidden(name: str):
        counters[name] = 0

        def call(*args, **kwargs):
            del args, kwargs
            counters[name] += 1
            raise AssertionError(f"ROOT-026 no debe alcanzar {name}")

        return call

    for name in (
        "_adaptar_triage_para_terminal",
        "_obtener_contexto_memoria_completa",
        "_guardar_evento_en_memoria",
        "_herramientas_para_evento",
        "_resolver_guardado_conversacional",
        "_resolver_ruta_persona",
        "_resolver_ruta_motor",
        "_resolver_ruta_oracle",
        "_gestionar_ejecucion",
        "_registrar_alias_candidato_pendiente",
        "_registrar_alias_aprendido_si_corresponde",
        "_aprobar_alias_pendiente_por_id",
        "_persistir_identidad_accion",
        "_registrar_estado_identidad",
        "_agregar_notificacion_si_corresponde",
        "_cachear_transaccion",
    ):
        setattr(orchestrator, name, forbidden(name))

    class ForbiddenReasoning:
        process = staticmethod(forbidden("reasoning.process"))

    orchestrator.reasoning = ForbiddenReasoning()
    return counters


def _assert_denied_turn(
    orchestrator: Orchestrator,
    event: EventoEntrante,
    writer: RecordingWriter,
    expected_failure: str,
) -> TurnProcessingError:
    with pytest.raises(TurnProcessingError) as raised:
        orchestrator.procesar_evento(event)
    error = raised.value
    assert error.component == "triage"
    assert error.failure_class == expected_failure
    assert orchestrator.action_ledger.get_turn_uow_status(
        event.id_transaccion
    ) is TurnUoWStatus.ABORTED_CLEAN
    assert [trace.stage for trace in writer.traces] == [
        "event_start",
        "triage",
        "error",
        "event_end",
    ]
    assert len({trace.trace_id for trace in writer.traces}) == 1
    triage = writer.traces[1]
    assert triage.triage["intent"] is None
    assert triage.triage["route"] is None
    assert triage.triage["effective_route"] is None
    assert writer.traces[2].error == {
        "type": expected_failure,
        "component": "triage",
    }
    return error


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 2])
def test_boundary_contract_requires_exact_integer_version(version: object) -> None:
    with pytest.raises(ValidationError):
        TriageBoundaryFailureV1(
            contract_version=version,
            code=TriageBoundaryFailureCodeV1.TRIAGE_EVALUATION_ERROR,
            error_type="RuntimeError",
        )


def test_boundary_contract_is_frozen_forbids_extras_and_bounds_error_type() -> None:
    failure = TriageBoundaryFailureV1(
        code=TriageBoundaryFailureCodeV1.TRIAGE_CONTRACT_ERROR,
        error_type="InvalidClassification",
    )
    with pytest.raises(ValidationError):
        failure.error_type = "Changed"  # type: ignore[misc]
    for invalid in ("", "bad-name", "9Bad", "x" * 97):
        with pytest.raises(ValidationError):
            TriageBoundaryFailureV1(
                code=TriageBoundaryFailureCodeV1.TRIAGE_CONTRACT_ERROR,
                error_type=invalid,
            )
    with pytest.raises(ValidationError):
        TriageBoundaryFailureV1.model_validate(
            {
                "code": "TRIAGE_CONTRACT_ERROR",
                "error_type": "InvalidTriageResult",
                "route": "persona",
            }
        )


def test_unsafe_exception_type_falls_back_without_secondary_failure() -> None:
    unsafe_error = type("Unsafe-Error", (Exception,), {})()
    assert safe_triage_error_type(unsafe_error) == "UnknownError"
    failure = triage_evaluation_failure(unsafe_error)
    assert failure.error_type == "UnknownError"


def test_structural_projection_is_independent_from_confidence() -> None:
    decision = _decision("DECIDED")
    low = decision.to_legacy_dict(confidence=0.0)
    high = decision.to_legacy_dict(confidence=987.654)
    fields = ("intent", "route", "requires_memory", "requires_oracle")
    assert {field: low[field] for field in fields} == {
        field: high[field] for field in fields
    }
    assert validate_triage_legacy_result(low) == decision
    assert validate_triage_legacy_result(high) == decision


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        (["not", "a", "dict"], "InvalidTriageResult"),
        ({}, "MissingClassification"),
        ({"classification": {"outcome": "BROKEN"}}, "InvalidClassification"),
    ],
)
def test_invalid_legacy_results_are_typed_contract_failures(
    payload: object,
    error_type: str,
) -> None:
    result = validate_triage_legacy_result(payload)
    assert isinstance(result, TriageBoundaryFailureV1)
    assert result.code is TriageBoundaryFailureCodeV1.TRIAGE_CONTRACT_ERROR
    assert result.error_type == error_type


def test_contradictory_legacy_projection_is_rejected() -> None:
    payload = _legacy("DECIDED")
    payload["route"] = "motor"
    result = validate_triage_legacy_result(payload)
    assert isinstance(result, TriageBoundaryFailureV1)
    assert result.error_type == "ContradictoryLegacyProjection"


@pytest.mark.parametrize("origin", ORIGINS)
def test_evaluation_failure_denies_every_channel_before_downstream(origin: str) -> None:
    orchestrator, route_calls = _orquestador_aislado("persona")
    triage = RaisingTriage()
    orchestrator.triage_router = triage
    writer = RecordingWriter()
    orchestrator.audit_writer = writer
    counters = _forbid_downstream(orchestrator)
    event = _event(origin, f"root026-evaluation-{origin}")

    _assert_denied_turn(
        orchestrator,
        event,
        writer,
        "TRIAGE_EVALUATION_ERROR",
    )

    assert triage.calls == 1
    assert not any(counters.values())
    assert not any(route_calls.values())
    assert orchestrator.memory.experiencias == []
    assert orchestrator.memory.acciones == []
    assert orchestrator.memory.turnos == []
    assert orchestrator.identity_state.eventos == []
    assert list(orchestrator._notificaciones) == []
    assert orchestrator._alias_candidates_pendientes == {}
    assert event.id_transaccion not in orchestrator._transacciones_procesadas


@pytest.mark.parametrize("origin", ORIGINS)
def test_contained_failed_denies_every_channel_without_losing_semantic_contract(
    origin: str,
) -> None:
    orchestrator, route_calls = _orquestador_aislado("persona")
    decision = _decision("FAILED")
    triage = StaticTriage(decision.to_legacy_dict(confidence=0.5))
    orchestrator.triage_router = triage
    writer = RecordingWriter()
    orchestrator.audit_writer = writer
    counters = _forbid_downstream(orchestrator)
    event = _event(origin, f"root026-contained-{origin}")

    _assert_denied_turn(orchestrator, event, writer, "MODEL_ERROR")

    assert triage.calls == 1
    assert validate_triage_legacy_result(triage.payload) == decision
    assert not any(counters.values())
    assert not any(route_calls.values())


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"classification": {"outcome": "BROKEN"}},
        {**_legacy("DECIDED"), "route": "motor"},
    ],
)
def test_contract_failures_use_same_denied_pipeline(payload: object) -> None:
    orchestrator, _ = _orquestador_aislado("persona")
    triage = StaticTriage(payload)
    orchestrator.triage_router = triage
    writer = RecordingWriter()
    orchestrator.audit_writer = writer
    counters = _forbid_downstream(orchestrator)
    event = _event("terminal", f"root026-contract-{id(payload)}")

    _assert_denied_turn(orchestrator, event, writer, "TRIAGE_CONTRACT_ERROR")

    assert triage.calls == 1
    assert not any(counters.values())


@pytest.mark.parametrize("kind", ["DECIDED", "DEGRADED", "UNKNOWN", "UNCERTAIN"])
def test_nonfailure_root025_outcomes_remain_authorized(kind: str) -> None:
    orchestrator, calls = _orquestador_aislado("persona")
    triage = StaticTriage(_legacy(kind))
    orchestrator.triage_router = triage
    result = orchestrator.procesar_evento(
        _event("unity", f"root026-preserved-{kind.lower()}")
    )

    assert result is not None and result.nombre == "hablar"
    assert triage.calls == 1
    assert calls["persona"] == 1
    assert orchestrator.action_ledger.get_turn_uow_status(
        f"root026-preserved-{kind.lower()}"
    ) is TurnUoWStatus.COMMITTED


def _prepare_outcome_action(ledger: ActionLedger) -> None:
    transaction_id = "root026-outcome-origin"
    action_id = "root026-outcome-action"
    ledger.claim_transaction(
        transaction_id,
        origin="unity",
        event_type="comando_usuario",
    )
    receipt = ledger.record_action(
        action_id=action_id,
        transaction_id=transaction_id,
        action_name="hablar",
        parameters={"texto_a_comunicar": "hola"},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
        decision_valid=True,
    )
    assert receipt.action is not None
    ledger.transition_action(
        action_id,
        ActionTransitionEvent.AUTO_AUTHORIZE,
        receipt_id="root026-authorize",
    )
    ledger.transition_action(
        action_id,
        ActionTransitionEvent.DISPATCH,
        receipt_id="root026-dispatch",
    )
    ledger.transition_action(
        action_id,
        ActionTransitionEvent.START_EXECUTION,
        receipt_id="root026-start",
    )
    completed = ledger.complete_transaction(
        transaction_id,
        {
            "id_accion": action_id,
            "nombre": "hablar",
            "parametros": {"texto_a_comunicar": "hola"},
            "requiere_confirmacion": False,
            "estado_ejecucion": "dispatched",
            "id_transaccion_origen": transaction_id,
        },
    )
    assert completed is TransactionCompletionStatus.COMPLETED


def test_outcome_effect_preserves_partial_and_reconciliation_mapping() -> None:
    ledger = ActionLedger(":memory:")
    _prepare_outcome_action(ledger)
    orchestrator, _ = _orquestador_aislado("internal")
    orchestrator.action_ledger = ledger
    triage = RaisingTriage()
    orchestrator.triage_router = triage
    writer = RecordingWriter()
    orchestrator.audit_writer = writer
    event = _event(
        "unity",
        "root026-outcome-turn",
        event_type="resultado_accion",
        details={
            "id_accion": "root026-outcome-action",
            "accion": "hablar",
            "exito": True,
            "mensaje_resultado": "entregado",
        },
    )

    with pytest.raises(TurnReconciliationRequiredError):
        orchestrator.procesar_evento(event)

    assert triage.calls == 1
    assert ledger.get_turn_uow_status(event.id_transaccion) is TurnUoWStatus.PARTIAL
    assert [trace.stage for trace in writer.traces] == [
        "event_start",
        "triage",
        "error",
        "event_end",
    ]
    outcome = ledger.get_outcome("root026-outcome-action")
    assert outcome is not None and outcome.consumed_at is None
    assert orchestrator.memory.experiencias == []


def test_started_effect_preserves_reconciliation_precedence() -> None:
    orchestrator, _ = _orquestador_aislado("persona")
    writer = RecordingWriter()
    orchestrator.audit_writer = writer
    transaction_id = "root026-started-turn"

    class StartedThenFails:
        calls = 0

        def route_event(self, evento: dict, estado_agente: dict) -> dict:
            del estado_agente
            self.calls += 1
            orchestrator.action_ledger.start_turn_effect(
                evento["id_transaccion"],
                "event:synthetic_started",
                effect_kind="event",
                phase="event",
                required=False,
            )
            raise RuntimeError("synthetic after started")

    triage = StartedThenFails()
    orchestrator.triage_router = triage

    with pytest.raises(TurnReconciliationRequiredError):
        orchestrator.procesar_evento(_event("unity", transaction_id))

    assert triage.calls == 1
    assert orchestrator.action_ledger.get_turn_uow_status(
        transaction_id
    ) is TurnUoWStatus.RECONCILIATION_REQUIRED
    assert [trace.stage for trace in writer.traces] == [
        "event_start",
        "triage",
        "error",
        "event_end",
    ]


def test_audit_writer_failure_remains_fail_open_for_triage_policy() -> None:
    orchestrator, _ = _orquestador_aislado("persona")
    orchestrator.triage_router = RaisingTriage()
    writer = RaisingWriter()
    orchestrator.audit_writer = writer

    with pytest.raises(TurnProcessingError) as raised:
        orchestrator.procesar_evento(
            _event("terminal", "root026-writer-fail-open")
        )

    assert raised.value.failure_class == "TRIAGE_EVALUATION_ERROR"
    assert writer.attempted_stages == [
        "event_start",
        "triage",
        "error",
        "event_end",
    ]


def test_exception_message_is_absent_from_logs_and_audit(caplog) -> None:
    orchestrator, _ = _orquestador_aislado("persona")
    orchestrator.triage_router = RaisingTriage(CANARY)
    writer = RecordingWriter()
    orchestrator.audit_writer = writer

    with caplog.at_level(logging.INFO):
        with pytest.raises(TurnProcessingError):
            orchestrator.procesar_evento(
                _event("terminal", "root026-canary-privacy")
            )

    human_logs = "\n".join(record.getMessage() for record in caplog.records)
    audit = "\n".join(trace.to_json() for trace in writer.traces)
    assert CANARY not in human_logs
    assert CANARY not in audit


def test_valid_hard_event_contract_is_unchanged() -> None:
    decision = TriageRouter().classify_event(
        {"tipo_evento": "alerta_dolor_fisico", "detalles": {}},
        {},
    )
    assert decision.outcome is TriageOutcomeV1.DECIDED
    assert decision.semantic_intent is IntentType.CORE_SYSTEM
    assert decision.route is Route.MOTOR
