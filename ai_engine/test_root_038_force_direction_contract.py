"""ROOT-038 B3: contrato canónico de dirección para aplicar_fuerza."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_engine.decision_outcome import DecisionDisposition, DecisionReasonCode
from ai_engine.motor_schemas import (
    ApplyForceParams,
    MotorActionAdapterV1,
    MotorActionContractFailureV1,
    MotorActionFailureCodeV1,
)
from ai_engine.schemas import ActionResponse
from core.orchestrator import Orchestrator


ROOT = Path(__file__).resolve().parents[1]
AGENT = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
MATERIALIZER = (
    ROOT / "unity_client" / "ObjectMaterializationController.cs"
).read_text(encoding="utf-8-sig")


@pytest.mark.parametrize(
    "vector",
    (
        (0.0, 0.0, 0.0),
        (-0.0, 0.0, 0.0),
    ),
)
def test_zero_direction_is_canonical_invalid(vector: tuple[float, float, float]) -> None:
    with pytest.raises(ValidationError):
        ApplyForceParams(
            nombre_objeto="Caja",
            vector_x=vector[0],
            vector_y=vector[1],
            vector_z=vector[2],
            fuerza=1.0,
        )

    parsed = MotorActionAdapterV1.parse(
        "aplicar_fuerza",
        {
            "nombre_objeto": "Caja",
            "vector_x": vector[0],
            "vector_y": vector[1],
            "vector_z": vector[2],
            "fuerza": 1.0,
        },
    )
    assert isinstance(parsed, MotorActionContractFailureV1)
    assert parsed.reason_code is MotorActionFailureCodeV1.FIELD_VALUE_INVALID


def test_unit_direction_is_preserved() -> None:
    params = ApplyForceParams(
        nombre_objeto="Caja",
        vector_x=-1.0,
        vector_y=0.0,
        vector_z=0.0,
        fuerza=1.0,
    )
    assert (params.vector_x, params.vector_y, params.vector_z) == (-1.0, 0.0, 0.0)


def test_scaled_equivalent_vectors_have_identical_canonical_direction() -> None:
    first = MotorActionAdapterV1.require(
        "aplicar_fuerza",
        {
            "nombre_objeto": "Caja",
            "vector_x": 1.0,
            "vector_y": 1.0,
            "vector_z": 0.0,
            "fuerza": 3.0,
        },
    ).parameters_dict()
    scaled = MotorActionAdapterV1.require(
        "aplicar_fuerza",
        {
            "nombre_objeto": "Caja",
            "vector_x": 100.0,
            "vector_y": 100.0,
            "vector_z": 0.0,
            "fuerza": 3.0,
        },
    ).parameters_dict()
    assert first == scaled


def test_canonical_direction_is_an_exact_idempotent_serialization_fixed_point() -> None:
    first = ApplyForceParams(
        nombre_objeto="Caja",
        vector_x=-17.0,
        vector_y=31.0,
        vector_z=9.0,
        fuerza=3.0,
    )
    replayed = ApplyForceParams.model_validate(first.model_dump())

    assert math.hypot(first.vector_x, first.vector_y, first.vector_z) == 1.0
    assert replayed.model_dump() == first.model_dump()


def test_diagonal_direction_is_normalized() -> None:
    params = ApplyForceParams(
        nombre_objeto="Caja",
        vector_x=1.0,
        vector_y=1.0,
        vector_z=0.0,
        fuerza=1.0,
    )
    expected = 1.0 / math.sqrt(2.0)
    assert params.vector_x == pytest.approx(expected)
    assert params.vector_y == pytest.approx(expected)
    assert params.vector_z == 0.0
    assert math.hypot(
        params.vector_x,
        params.vector_y,
        params.vector_z,
    ) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "magnitude",
    (
        float.fromhex("0x0.0000000000001p-1022"),
        sys.float_info.max,
    ),
)
def test_extreme_finite_vectors_normalize_without_overflow_or_underflow(
    magnitude: float,
) -> None:
    canonical = MotorActionAdapterV1.require(
        "aplicar_fuerza",
        {
            "nombre_objeto": "Caja",
            "vector_x": magnitude,
            "vector_y": 0.0,
            "vector_z": 0.0,
            "fuerza": 1.0,
        },
    )
    params = canonical.parameters_dict()
    assert params["vector_x"] == 1.0
    assert params["vector_y"] == 0.0
    assert params["vector_z"] == 0.0


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_nonfinite_direction_remains_invalid(value: float) -> None:
    with pytest.raises(ValidationError):
        ApplyForceParams(
            nombre_objeto="Caja",
            vector_x=value,
            vector_y=1.0,
            vector_z=0.0,
            fuerza=1.0,
        )


@pytest.mark.parametrize("force", (0.0, -0.1, 100.1))
def test_force_bounds_remain_unchanged(force: float) -> None:
    with pytest.raises(ValidationError):
        ApplyForceParams(
            nombre_objeto="Caja",
            vector_x=1.0,
            vector_y=0.0,
            vector_z=0.0,
            fuerza=force,
        )


def test_normalized_direction_reaches_action_response_and_outgoing_projection() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    raw = ActionResponse(
        pensamiento="",
        accion="aplicar_fuerza",
        parametros={
            "nombre_objeto": "Caja",
            "vector_x": 100.0,
            "vector_y": 0.0,
            "vector_z": 0.0,
            "fuerza": 5.0,
        },
    )

    normalized, canonical = orchestrator._canonicalizar_action_response_motor(raw)
    assert canonical is not None
    expected = {
        "nombre_objeto": "Caja",
        "vector_x": 1.0,
        "vector_y": 0.0,
        "vector_z": 0.0,
        "fuerza": 5.0,
    }
    assert normalized.parametros == expected
    outgoing = orchestrator._a_accion_saliente(
        normalized,
        "autonoma",
        action_id="root038-b3-normalized",
        canonical_motor_action=canonical,
    )
    assert outgoing.parametros == expected


def test_zero_direction_becomes_invalid_decision_before_outgoing_projection() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.tool_registry = None
    raw = ActionResponse(
        pensamiento="",
        accion="aplicar_fuerza",
        parametros={
            "nombre_objeto": "Caja",
            "vector_x": 0.0,
            "vector_y": 0.0,
            "vector_z": 0.0,
            "fuerza": 1.0,
        },
    )

    rejected, canonical = orchestrator._canonicalizar_action_response_motor(raw)
    assert canonical is None
    assert rejected.accion == "none"
    assert rejected.parametros == {}
    assert rejected.valido is False
    assert rejected.outcome.disposition is DecisionDisposition.INVALID
    assert DecisionReasonCode.INVALID_PARAMETERS in rejected.outcome.reason_codes
    assert rejected.metadata["motor_contract_failure"]["reason_code"] == "FIELD_VALUE_INVALID"


def test_unity_receives_unit_float_compatible_values_and_keeps_defensive_guard() -> None:
    tiny = float.fromhex("0x0.0000000000001p-1022")
    params = MotorActionAdapterV1.require(
        "aplicar_fuerza",
        {
            "nombre_objeto": "Caja",
            "vector_x": tiny,
            "vector_y": tiny,
            "vector_z": 0.0,
            "fuerza": 1.0,
        },
    ).parameters_dict()

    assert params["vector_x"] == pytest.approx(1.0 / math.sqrt(2.0))
    assert params["vector_y"] == pytest.approx(1.0 / math.sqrt(2.0))
    assert 0.0 < abs(params["vector_x"]) <= 1.0
    assert 'new Vector3(vx, vy, vz)' in AGENT
    assert "direction.sqrMagnitude <= Mathf.Epsilon" in MATERIALIZER
    assert "normalizedDirection = direction.normalized" in MATERIALIZER
    assert "body.AddForce(normalizedDirection * force" in MATERIALIZER
