"""Python-owned golden response/error contract for ROOT-038 V1."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from ai_engine.decision_outcome import DecisionDisposition, DecisionReasonCode
from api.errors import build_public_error_envelope
from api.schemas import (
    AccionSaliente,
    DecisionPublica,
    PublicErrorCode,
    RespuestaAPI,
)
from core.test_root_038_action_binding_vectors import build_vectors as build_binding_vectors


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "api" / "fixtures" / "root_038_wire_v1.jsonl"
STAMP = datetime(2026, 10, 11, tzinfo=timezone.utc)


def _response(
    case_id: str,
    *,
    status: str = "ok",
    action: AccionSaliente | None = None,
    decision: DecisionPublica | None = None,
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "surface": "response",
        "payload": RespuestaAPI(
            status=status,
            mensaje=case_id,
            id_transaccion=f"tx:{case_id}",
            accion=action,
            decision=decision,
            timestamp_servidor=STAMP,
        ).model_dump(mode="json"),
    }


def build_vectors() -> list[dict[str, object]]:
    vectors: list[dict[str, object]] = [
        _response("success_no_action"),
        _response(
            "pending_cancel",
            status="pendiente",
            action=AccionSaliente(
                id_accion="action:pending",
                nombre="cancelar_tarea",
                parametros={
                    "motivo": "usuario",
                    "cancelar_movimiento": True,
                    "cancelar_cola": True,
                    "texto_confirmacion": "ok",
                },
                requiere_confirmacion=True,
                estado_ejecucion="awaiting_authorization",
                id_transaccion_origen="tx:pending_cancel",
            ),
        ),
        _response(
            "dispatched_position",
            action=AccionSaliente(
                id_accion="action:position",
                nombre="navegar_a_posicion",
                parametros={"x": 1.0, "y": -2.0, "z": 3.5},
                estado_ejecucion="dispatched",
                id_transaccion_origen="tx:dispatched_position",
            ),
        ),
        _response(
            "hablar",
            action=AccionSaliente(
                id_accion="action:hablar",
                nombre="hablar",
                parametros={"texto": "hola"},
                estado_ejecucion="dispatched",
                id_transaccion_origen="tx:hablar",
            ),
        ),
    ]
    for disposition, reason in (
        (DecisionDisposition.ACTION, DecisionReasonCode.PRIMARY_VALID),
        (DecisionDisposition.NO_ACTION, DecisionReasonCode.PASSIVE_NO_ACTION),
        (DecisionDisposition.CLARIFICATION, DecisionReasonCode.MOTOR_CLARIFICATION),
        (DecisionDisposition.BLOCKED, DecisionReasonCode.UNSAFE_BLOCK),
    ):
        vectors.append(
            _response(
                f"decision_{disposition.value}",
                decision=DecisionPublica(disposition=disposition, reason_code=reason),
            )
        )
    for code in (PublicErrorCode.INTERNAL_ERROR, PublicErrorCode.RECONCILIATION_REQUIRED):
        vectors.append(
            {
                "case_id": f"error_{code.value.lower()}",
                "surface": "public_error",
                "payload": build_public_error_envelope(
                    code,
                    correlation_id=(
                        "00000000-0000-4000-8000-000000000001"
                        if code is PublicErrorCode.INTERNAL_ERROR
                        else "00000000-0000-4000-8000-000000000002"
                    ),
                ).model_dump(mode="json"),
            }
        )
    return vectors


def _load_fixture() -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").split("\n")
        if line
    ]


def test_golden_fixture_is_exact_python_source_of_truth() -> None:
    assert _load_fixture() == build_vectors()


def test_v1_fixture_inventory_and_existing_physical_goldens_are_complete() -> None:
    vectors = build_vectors()
    assert {item["surface"] for item in vectors} == {"response", "public_error"}
    assert {
        item["payload"].get("wire_version")
        for item in vectors
        if item["surface"] == "response"
    } == {1}
    assert {vector["action"]["nombre"] for vector in build_binding_vectors()} == {
        "navegar_a_objeto",
        "navegar_a_posicion",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "soltar_objeto",
        "alejarse_de_objeto",
        "girar_avatar",
        "aplicar_fuerza",
    }


def test_field_roles_remain_non_authoritative_where_documented() -> None:
    action = next(
        item["payload"]["accion"]
        for item in build_vectors()
        if item["case_id"] == "pending_cancel"
    )
    assert action["requiere_confirmacion"] is True
    assert action["parametros_descripcion"] == []
    assert action["perception_authority"] is None
