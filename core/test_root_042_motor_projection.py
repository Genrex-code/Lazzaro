from __future__ import annotations

from dataclasses import replace

import pytest

from ai_engine.decision_outcome import (
    DecisionDisposition,
    DecisionReasonCode,
)
from ai_engine.motor_resolution import InteractionType, MotorResolution
from ai_engine.motor_schemas import (
    MotorActionAdapterV1,
    MotorActionContractError,
)
from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.action_ledger import ActionRecord, ActionState
from core.orchestrator import Orchestrator
from core.perception_authority import (
    PerceptionAuthorityEvaluator,
    TrustedParameterPathV1,
)
from tools.executor import ActionExecutor


class _NoLedgerDispatch:
    def __init__(self) -> None:
        self.calls = 0

    def record_action(self, **_kwargs):
        self.calls += 1
        raise AssertionError("invalid Motor structure reached ActionLedger")


def _event(transaction_id: str = "root-042") -> EventoEntrante:
    return EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles={},
    )


def test_invalid_motor_structure_becomes_invalid_parameters_and_no_outgoing() -> None:
    orchestrator = object.__new__(Orchestrator)
    raw = ActionResponse(
        pensamiento="",
        accion="navegar_a_posicion",
        parametros={"nombre_objeto": "Caja"},
        razon="input no confiable",
    )

    rejected, canonical = orchestrator._canonicalizar_action_response_motor(raw)

    assert canonical is None
    assert rejected.valido is False
    assert rejected.accion == "none"
    assert rejected.parametros == {}
    assert rejected.outcome.disposition is DecisionDisposition.INVALID
    assert DecisionReasonCode.INVALID_PARAMETERS in rejected.outcome.reason_codes
    assert rejected.metadata["motor_contract_failure"]["reason_code"] == "REQUIRED_FIELD_MISSING"
    with pytest.raises(MotorActionContractError):
        orchestrator._a_accion_saliente(raw, "autonoma")


def test_invalid_motor_stops_before_root_029_semantic_evaluator(monkeypatch) -> None:
    calls = 0

    def unexpected_evaluate(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("ROOT-029 must not inspect malformed Motor input")

    monkeypatch.setattr(PerceptionAuthorityEvaluator, "evaluate", unexpected_evaluate)
    orchestrator = object.__new__(Orchestrator)
    orchestrator.perception_authority_evaluator = PerceptionAuthorityEvaluator()
    invalid = ActionResponse(
        pensamiento="",
        accion="interactuar_objeto_3d",
        parametros={"nombre_objeto": "Panel"},
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        _event(),
        invalid,
        action_id="invalid-action",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
    )

    assert calls == 0
    assert reference is None
    assert governed.valido is False
    assert governed.outcome.disposition is DecisionDisposition.INVALID
    assert DecisionReasonCode.INVALID_PARAMETERS in governed.outcome.reason_codes


def test_invalid_nonperception_action_cannot_use_not_applicable_as_bypass(monkeypatch) -> None:
    calls = 0

    def unexpected_evaluate(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("invalid non-perception action reached authority")

    monkeypatch.setattr(PerceptionAuthorityEvaluator, "evaluate", unexpected_evaluate)
    orchestrator = object.__new__(Orchestrator)
    orchestrator.perception_authority_evaluator = PerceptionAuthorityEvaluator()
    invalid_cancel = ActionResponse(
        pensamiento="",
        accion="cancelar_tarea",
        parametros={"cancelar_movimiento": "false"},
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        _event(),
        invalid_cancel,
        action_id="invalid-cancel",
        trusted_parameter_path=TrustedParameterPathV1.UNKNOWN,
    )

    assert calls == 0
    assert reference is None
    assert governed.valido is False
    assert governed.accion == "none"


def test_invalid_motor_structure_never_reaches_ledger_identity() -> None:
    orchestrator = object.__new__(Orchestrator)
    ledger = _NoLedgerDispatch()
    orchestrator.action_ledger = ledger
    invalid = AccionSaliente(
        id_accion="invalid-ledger-action",
        nombre="navegar_a_posicion",
        parametros={"nombre_objeto": "Caja"},
    )

    with pytest.raises(MotorActionContractError):
        orchestrator._persistir_identidad_accion(
            _event("invalid-ledger"),
            invalid,
            task_instance_id=None,
        )
    assert ledger.calls == 0


def test_invalid_motor_structure_has_zero_physical_dispatch() -> None:
    invalid = AccionSaliente(
        id_accion="invalid-dispatch-action",
        nombre="aplicar_fuerza",
        parametros={"nombre_objeto": "Caja", "fuerza": 1.0},
    )

    result = ActionExecutor.ejecutar(invalid)

    assert result.exito is False
    assert "reason_code=REQUIRED_FIELD_MISSING" in result.error


def test_ledger_replay_is_legacy_input_and_fails_closed() -> None:
    invalid = ActionRecord(
        action_id="stored-invalid",
        transaction_id="tx-stored-invalid",
        action_name="navegar_a_posicion",
        parameters={"nombre_objeto": "Caja"},
        requires_confirmation=False,
        task_instance_id=None,
        intended_client="unity",
        outcome_expected=True,
        created_at="2026-01-01T00:00:00+00:00",
        action_state=ActionState.CREATED,
    )
    valid = replace(
        invalid,
        action_id="stored-valid",
        action_name="navegar_a_posicion",
        parameters={"x": "1", "y": "2", "z": "3"},
    )

    with pytest.raises(MotorActionContractError):
        Orchestrator._accion_desde_record(invalid)
    restored = Orchestrator._accion_desde_record(valid)
    assert restored.parametros == {"x": 1.0, "y": 2.0, "z": 3.0}


@pytest.mark.parametrize("interaction", list(InteractionType))
def test_root_028_subtype_is_identical_through_canonical_outgoing_projection(
    interaction: InteractionType,
) -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    resolution = MotorResolution(
        status="ready",
        action="interactuar_objeto_3d",
        interaction_type=interaction,
        target_id="panel",
        target_display_name="Panel",
        legacy_nombre_objeto="Panel",
    )
    response = ActionResponse(
        pensamiento="",
        accion="interactuar_objeto_3d",
        parametros={
            "nombre_objeto": "Panel",
            "tipo_accion": resolution.interaction_type.value,
        },
        metadata=resolution.to_metadata(),
    )

    normalized, canonical = orchestrator._canonicalizar_action_response_motor(response)
    assert canonical is not None
    outgoing = orchestrator._a_accion_saliente(
        normalized,
        "autonoma",
        canonical_motor_action=canonical,
    )

    assert resolution.interaction_type is interaction
    assert canonical.params.tipo_accion is interaction
    assert normalized.parametros["tipo_accion"] == interaction.value
    assert outgoing.parametros["tipo_accion"] == interaction.value
