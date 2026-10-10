"""ROOT-038 B3: orden canónico antes de autoridad, wire y ledger."""

from __future__ import annotations

import pytest

from ai_engine.decision_outcome import DecisionDisposition, DecisionReasonCode
from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.action_ledger import ActionLedger, ActionState
from core.orchestrator import Orchestrator
from core.perception_authority import (
    AuthorityStatusV1,
    PerceptionAuthorityEvaluator,
    TrustedParameterPathV1,
    action_binding_sha256,
    canonical_json,
)
from core.test_root_029_orchestrator_projection import _context, _orchestrator
from tools.executor import ActionExecutor


def _raw_force(
    context: dict,
    *,
    vector_x: float,
    vector_y: float,
    vector_z: float = 0.0,
) -> ActionResponse:
    return ActionResponse(
        pensamiento="",
        accion="aplicar_fuerza",
        parametros={
            "nombre_objeto": "Caja",
            "vector_x": vector_x,
            "vector_y": vector_y,
            "vector_z": vector_z,
            "fuerza": 4.0,
        },
        metadata={
            "root029_perception": context,
            "motor_resolution": {
                "status": "ready",
                "target_id": "crate",
                "target_runtime_object_id": "42",
                "target_producer_object_id": "crate",
                "interaction_type": None,
                "candidates": [
                    {
                        "target_id": "crate",
                        "affordance_decision": {
                            "allowed": True,
                            "safety_assessment": {
                                "disposition": "SAFE",
                                "reason_code": "NO_HAZARD",
                            },
                        },
                    }
                ],
            },
        },
    )


def test_signed_zero_has_identical_json_and_root029_binding() -> None:
    context, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="root038-b3-signed-zero",
        detalles={},
    )

    signed_response, signed_action = orchestrator._canonicalizar_action_response_motor(
        _raw_force(context, vector_x=1.0, vector_y=-0.0, vector_z=0.0)
    )
    positive_response, positive_action = orchestrator._canonicalizar_action_response_motor(
        _raw_force(context, vector_x=1.0, vector_y=0.0, vector_z=0.0)
    )
    assert signed_action is not None
    assert positive_action is not None
    assert signed_action.parameters_dict() == positive_action.parameters_dict()
    assert canonical_json(signed_action.parameters_dict()) == canonical_json(
        positive_action.parameters_dict()
    )

    _signed_governed, signed_reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        signed_response,
        action_id="root038-b3-signed-zero-action",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
        canonical_motor_action=signed_action,
    )
    _positive_governed, positive_reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        positive_response,
        action_id="root038-b3-signed-zero-action",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
        canonical_motor_action=positive_action,
    )
    assert signed_reference is not None
    assert positive_reference is not None
    assert signed_reference.canonical_parameters_json == positive_reference.canonical_parameters_json
    assert signed_reference.action_binding_sha256 == positive_reference.action_binding_sha256


def test_normalization_precedes_root029_binding_outgoing_and_ledger() -> None:
    context, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="root038-b3-pipeline",
        detalles={},
    )
    normalized, canonical = orchestrator._canonicalizar_action_response_motor(
        _raw_force(context, vector_x=100.0, vector_y=100.0)
    )
    assert canonical is not None
    expected = canonical.parameters_dict()
    assert expected["vector_x"] == pytest.approx(1.0 / 2**0.5)
    assert expected["vector_y"] == pytest.approx(1.0 / 2**0.5)
    assert normalized.parametros == expected

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        normalized,
        action_id="root038-b3-action",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
        canonical_motor_action=canonical,
    )
    assert reference is not None
    assert governed.metadata["perception_authority_receipt"]["status"] == AuthorityStatusV1.AUTHORIZED.value
    assert reference.canonical_parameters_json == canonical_json(expected)
    assert reference.action_binding_sha256 == action_binding_sha256(
        id_accion=reference.id_accion,
        action_name=reference.action_name,
        canonical_parameters_json=reference.canonical_parameters_json,
        target=reference.target,
        authority_policy_id=reference.authority_policy_id,
        freshness_policy_id=reference.freshness_policy_id,
        generation_binding_mode=reference.generation_binding_mode,
        interaction_type=reference.interaction_type,
        safety_disposition=reference.safety_disposition,
        safety_reason_code=reference.safety_reason_code,
    )

    outgoing = orchestrator._a_accion_saliente(
        governed,
        "autonoma",
        action_id="root038-b3-action",
        perception_authority=reference,
        canonical_motor_action=canonical,
    )
    assert outgoing.parametros == expected

    ledger = ActionLedger(":memory:")
    ledger.claim_transaction(
        event.id_transaccion,
        origin=event.origen,
        event_type=event.tipo_evento,
    )
    orchestrator.action_ledger = ledger
    orchestrator._persistir_identidad_accion(
        event,
        outgoing,
        task_instance_id=None,
        decision_valid=True,
    )
    stored = ledger.get_action("root038-b3-action")
    assert stored is not None
    assert stored.parameters == expected
    assert stored.action_binding_sha256 == reference.action_binding_sha256
    assert stored.action_state is ActionState.CREATED


def test_zero_direction_stops_before_root029_outgoing_and_ledger(monkeypatch) -> None:
    calls = 0

    def unexpected_evaluate(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("zero direction reached ROOT-029")

    monkeypatch.setattr(PerceptionAuthorityEvaluator, "evaluate", unexpected_evaluate)
    context, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    rejected, reference = orchestrator._aplicar_autoridad_perceptiva(
        EventoEntrante(
            origen="unity",
            tipo_evento="comando_usuario",
            id_transaccion="root038-b3-zero",
            detalles={},
        ),
        _raw_force(context, vector_x=0.0, vector_y=0.0),
        action_id="root038-b3-zero-action",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
    )

    assert reference is None
    assert rejected.accion == "none"
    assert rejected.outcome.disposition is DecisionDisposition.INVALID
    assert DecisionReasonCode.INVALID_PARAMETERS in rejected.outcome.reason_codes
    assert calls == 0

    ledger = ActionLedger(":memory:")
    ledger.claim_transaction(
        "root038-b3-zero",
        origin="unity",
        event_type="comando_usuario",
    )
    orchestrator.action_ledger = ledger
    invalid_outgoing = AccionSaliente(
        id_accion="root038-b3-zero-action",
        nombre="aplicar_fuerza",
        parametros={
            "nombre_objeto": "Caja",
            "vector_x": 0.0,
            "vector_y": 0.0,
            "vector_z": 0.0,
            "fuerza": 1.0,
        },
    )
    with pytest.raises(TypeError):
        orchestrator._persistir_identidad_accion(
            EventoEntrante(
                origen="unity",
                tipo_evento="comando_usuario",
                id_transaccion="root038-b3-zero",
                detalles={},
            ),
            invalid_outgoing,
            task_instance_id=None,
            decision_valid=False,
        )
    assert ledger.get_action("root038-b3-zero-action") is None

    execution = ActionExecutor.ejecutar(invalid_outgoing)
    assert execution.exito is False
    assert "reason_code=FIELD_VALUE_INVALID" in str(execution.error)
