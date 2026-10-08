from __future__ import annotations

import pytest

from ai_engine.schemas import ActionResponse
from ai_engine.perception_contracts import evaluate_perception_identity, parse_perception_payload
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator
from core.perception_authority import (
    AuthorityStatusV1,
    GenerationBindingModeV1,
    PerceptionAuthorityEvaluator,
    PerceptionAuthorityPolicyV1,
    TrustedParameterPathV1,
)
from core.perception_freshness import (
    FreshnessPolicyV1,
    PerceptionFreshnessEvaluator,
    PerceptionIngestionMetadataV1,
)
from core.perception_lifecycle import PerceptionLifecycleTracker


def _context() -> tuple[dict, FreshnessPolicyV1, PerceptionAuthorityPolicyV1]:
    parsed = parse_perception_payload(
        {
            "source_kind": "UNITY",
            "producer_instance_id": "unity-main",
            "stream_id": "world",
            "producer_epoch_id": "epoch-a",
            "logical_world_id": "world-a",
            "producer_capture_id": "capture-0",
            "generation": 0,
            "captured_at_utc_ms": 1_000,
            "objects": [
                {
                    "producer_object_id": "crate",
                    "runtime_object_id": "42",
                    "display_name": "Caja",
                    "position": {"x": 1, "y": 0, "z": 2},
                    "distance": 2,
                    "affordances": ["tocar"],
                    "confidence": 1,
                }
            ],
        },
        local_ingest_id="ingest-a",
    )
    lifecycle = PerceptionLifecycleTracker().observe_and_classify(parsed)
    freshness_policy = FreshnessPolicyV1(
        policy_id="freshness-test",
        max_capture_age_ms=100,
        max_future_skew_ms=10,
    )
    freshness = PerceptionFreshnessEvaluator().evaluate(
        parsed,
        PerceptionIngestionMetadataV1(
            local_ingest_id="ingest-a",
            received_at_utc_ms=1_005,
        ),
        lifecycle,
        policy=freshness_policy,
        evaluated_at_utc_ms=1_010,
    )
    authority_policy = PerceptionAuthorityPolicyV1(
        policy_id="authority-test",
        freshness_policy_id="freshness-test",
        generation_binding_mode=GenerationBindingModeV1.REQUIRE_AUTHORIZED_GENERATION,
    )
    context = {
        "parse": parsed.model_dump(mode="json"),
        "lifecycle": lifecycle.model_dump(mode="json"),
        "freshness": freshness.model_dump(mode="json"),
        "ingestion": {
            "contract_version": 1,
            "local_ingest_id": "ingest-a",
            "received_at_utc_ms": 1_005,
        },
    }
    return context, freshness_policy, authority_policy


def _action(context: dict, *, interaction: bool = False) -> ActionResponse:
    action_name = "interactuar_objeto_3d" if interaction else "navegar_a_objeto"
    params = {"nombre_objeto": "Caja"}
    if interaction:
        params["tipo_accion"] = "tocar"
    resolution = {
        "status": "ready",
        "target_id": "crate",
        "target_runtime_object_id": "42",
        "target_producer_object_id": "crate",
        "interaction_type": "tocar" if interaction else None,
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
    }
    return ActionResponse(
        pensamiento="interno",
        accion=action_name,
        parametros=params,
        razon="lista",
        metadata={
            "motor_resolution": resolution,
            "root029_perception": context,
        },
    )


def _orchestrator(authority_policy: PerceptionAuthorityPolicyV1 | None) -> Orchestrator:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    orchestrator.perception_authority_policy = authority_policy
    orchestrator.perception_authority_evaluator = PerceptionAuthorityEvaluator()
    return orchestrator


@pytest.mark.parametrize("origin", ["terminal", "unity", "sandbox", "roblox", "otro"])
@pytest.mark.parametrize("interaction", [False, True])
def test_authority_is_channel_independent_and_projects_exact_action(
    origin: str,
    interaction: bool,
) -> None:
    context, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen=origin,
        tipo_evento="comando_usuario",
        id_transaccion=f"tx-{origin}-{interaction}",
        detalles={},
    )
    original = _action(context, interaction=interaction)

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        original,
        action_id="action-a",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
    )
    outgoing = orchestrator._a_accion_saliente(
        governed,
        "autonoma",
        action_id="action-a",
        perception_authority=reference,
    )

    assert governed.accion == original.accion
    assert governed.parametros == original.parametros
    assert reference is not None
    assert reference.id_accion == "action-a"
    assert reference.target.runtime_object_id == "42"
    assert reference.interaction_type == ("tocar" if interaction else None)
    assert reference.safety_disposition.value == "SAFE"
    assert outgoing.id_accion == "action-a"
    assert outgoing.nombre == original.accion
    assert outgoing.parametros == original.parametros
    assert outgoing.perception_authority == reference


def test_missing_production_policy_blocks_with_zero_target_dispatch() -> None:
    context, _freshness_policy, _authority_policy = _context()
    orchestrator = _orchestrator(None)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-no-policy",
        detalles={},
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        _action(context),
        action_id="action-a",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
    )

    assert governed.accion == "none"
    assert governed.parametros == {}
    assert reference is None
    assert governed.metadata["perception_authority_receipt"]["status"] == "INDETERMINATE"
    assert governed.metadata["perception_authority_block"] == "FRESHNESS_POLICY_MISSING"


def test_mixed_capture_receipts_block_before_outgoing_projection() -> None:
    context, _freshness_policy, authority_policy = _context()
    context["freshness"]["producer_capture_id"] = "capture-other"
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-mixed",
        detalles={},
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        _action(context),
        action_id="action-a",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
    )

    assert governed.accion == "none"
    assert reference is None
    assert governed.metadata["perception_authority_block"] == "RECEIPT_CORRELATION_MISMATCH"


def test_unknown_contract_version_blocks_without_falling_back_to_legacy_target() -> None:
    context, _freshness_policy, authority_policy = _context()
    context["parse"]["contract_version"] = 2
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-version",
        detalles={},
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        _action(context),
        action_id="action-a",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
    )

    assert governed.accion == "none"
    assert governed.parametros == {}
    assert reference is None


def test_action_id_is_preallocated_and_shared_one_to_one_with_authority_decision() -> None:
    context, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-id",
        detalles={},
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        _action(context),
        action_id="preallocated-action",
        trusted_parameter_path=TrustedParameterPathV1.MOTOR_PERCEPTION_RESOLUTION,
    )

    assert governed.metadata["perception_authority_receipt"]["status"] == AuthorityStatusV1.AUTHORIZED.value
    assert reference is not None
    assert reference.id_accion == "preallocated-action"
    assert reference.authority_decision_id


def test_nonperception_and_body_event_paths_are_not_silently_perception_governed() -> None:
    _context_value, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="alerta_dolor_fisico",
        id_transaccion="tx-body",
        detalles={},
    )
    body = ActionResponse(
        pensamiento="interno",
        accion="alejarse_de_objeto",
        parametros={"direccion": "atras"},
        razon="reflejo",
        metadata={},
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        body,
        action_id="body-action",
        trusted_parameter_path=TrustedParameterPathV1.CRITICAL_BODY_EVENT,
    )

    assert governed.accion == "alejarse_de_objeto"
    assert reference is None
    assert governed.metadata["parameter_provenance"]["kind"] == "BODY_EVENT"


def _position_action() -> ActionResponse:
    return ActionResponse(
        pensamiento="interno",
        accion="navegar_a_posicion",
        parametros={"x": 1.0, "y": 0.0, "z": 2.0},
        razon="coordenadas",
        metadata={},
    )


def test_reasoning_generated_position_has_unknown_provenance_and_zero_dispatch() -> None:
    _context_value, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-position-reasoning",
        detalles={},
    )
    trusted_path = orchestrator._trusted_path_for_motor_action(
        "navegar_a_posicion",
        perception_resolution=False,
        reasoning_generated=True,
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        _position_action(),
        action_id="position-reasoning",
        trusted_parameter_path=trusted_path,
    )

    assert trusted_path is TrustedParameterPathV1.UNKNOWN
    assert governed.accion == "none"
    assert governed.parametros == {}
    assert reference is None
    assert governed.metadata["parameter_provenance"]["kind"] == "UNKNOWN"
    assert governed.metadata["perception_authority_receipt"]["status"] == "INDETERMINATE"
    assert governed.metadata["perception_authority_block"] == "PROVENANCE_UNKNOWN"


def test_deterministic_coordinate_parser_is_explicit_and_not_applicable() -> None:
    _context_value, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-position-explicit",
        detalles={},
    )
    trusted_path = orchestrator._trusted_path_for_motor_action(
        "navegar_a_posicion",
        perception_resolution=False,
        reasoning_generated=False,
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        _position_action(),
        action_id="position-explicit",
        trusted_parameter_path=trusted_path,
    )

    assert trusted_path is TrustedParameterPathV1.MOTOR_EXPLICIT_COORDINATE_PARSER
    assert governed.accion == "navegar_a_posicion"
    assert governed.parametros == {"x": 1.0, "y": 0.0, "z": 2.0}
    assert reference is None
    assert governed.metadata["parameter_provenance"]["kind"] == "EXPLICIT_COORDINATES"
    assert governed.metadata["perception_authority_receipt"]["status"] == "NOT_APPLICABLE"


def test_perception_derived_coordinates_are_required_and_fail_closed_in_v1() -> None:
    _context_value, _freshness_policy, authority_policy = _context()
    orchestrator = _orchestrator(authority_policy)
    event = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="tx-position-perception",
        detalles={},
    )

    governed, reference = orchestrator._aplicar_autoridad_perceptiva(
        event,
        _position_action(),
        action_id="position-perception",
        trusted_parameter_path=TrustedParameterPathV1.PERCEPTION_DERIVED_COORDINATES,
    )

    assert governed.accion == "none"
    assert reference is None
    assert governed.metadata["parameter_provenance"]["kind"] == "PERCEPTION_TARGET"
    assert governed.metadata["perception_authority_receipt"]["applicability"] == "REQUIRED"
    assert governed.metadata["perception_authority_receipt"]["status"] != "AUTHORIZED"
