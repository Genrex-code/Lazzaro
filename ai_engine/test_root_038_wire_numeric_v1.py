"""ROOT-038 B17/B19: executable float32 and int64 wire domains."""

from __future__ import annotations

import math
import struct
import sys

import pytest
from pydantic import ValidationError

from ai_engine.motor_schemas import (
    ApplyForceParams,
    NavigateToPositionParams,
)
from ai_engine.perception_contracts import MAX_GENERATION, PerceptionDataV1
from api.schemas import DecisionPublica, RespuestaAPI
from ai_engine.decision_outcome import DecisionDisposition, DecisionReasonCode


FLOAT32_MAX = float.fromhex("0x1.fffffep+127")
FLOAT32_MIN_SUBNORMAL = 2.0 ** -149


def _float32(value: float) -> float:
    return struct.unpack("!f", struct.pack("!f", value))[0]


def _perception(captured: object) -> PerceptionDataV1:
    return PerceptionDataV1(
        source_kind="UNITY",
        producer_instance_id="producer",
        stream_id="stream",
        producer_epoch_id="epoch",
        producer_capture_id="capture",
        generation=0,
        captured_at_utc_ms=captured,
        objects=(),
    )


def test_physical_scalars_are_canonical_float32_before_serialization() -> None:
    position = NavigateToPositionParams(x=0.1, y=-0.0, z=FLOAT32_MIN_SUBNORMAL)
    assert position.x == _float32(0.1)
    assert math.copysign(1.0, position.y) == 1.0
    assert position.z == FLOAT32_MIN_SUBNORMAL
    assert NavigateToPositionParams(x=FLOAT32_MAX, y=0, z=0).x == FLOAT32_MAX


@pytest.mark.parametrize("value", (FLOAT32_MAX * 2.0, sys.float_info.max))
def test_physical_float32_overflow_fails_closed(value: float) -> None:
    with pytest.raises(ValidationError):
        NavigateToPositionParams(x=value, y=0, z=0)


def test_nonzero_float32_underflow_fails_closed() -> None:
    with pytest.raises(ValidationError):
        NavigateToPositionParams(x=FLOAT32_MIN_SUBNORMAL / 2.0, y=0, z=0)


def test_force_direction_is_float32_and_exact_revalidation_fixed_point() -> None:
    first = ApplyForceParams(
        nombre_objeto="Caja",
        vector_x=-17.0,
        vector_y=31.0,
        vector_z=9.0,
        fuerza=0.1,
    )
    replayed = ApplyForceParams.model_validate(first.model_dump())
    assert replayed.model_dump() == first.model_dump()
    assert first.fuerza == _float32(0.1)
    assert all(
        component == _float32(component)
        for component in (first.vector_x, first.vector_y, first.vector_z)
    )
    assert math.hypot(first.vector_x, first.vector_y, first.vector_z) == pytest.approx(
        1.0, abs=2e-6
    )


@pytest.mark.parametrize("value", (0, 1, MAX_GENERATION))
def test_capture_timestamp_accepts_exact_int64_domain(value: int) -> None:
    assert _perception(value).captured_at_utc_ms == value


@pytest.mark.parametrize("value", (-1, MAX_GENERATION + 1, True, 1.0, "1"))
def test_capture_timestamp_rejects_outside_strict_int64_domain(value: object) -> None:
    with pytest.raises(ValidationError):
        _perception(value)


@pytest.mark.parametrize("value", (True, 1.0, "1", False, 2))
def test_wire_and_decision_versions_are_exact_integer_one(value: object) -> None:
    with pytest.raises(ValidationError):
        RespuestaAPI(wire_version=value)
    with pytest.raises(ValidationError):
        DecisionPublica(
            version=value,
            disposition=DecisionDisposition.ACTION,
            reason_code=DecisionReasonCode.PRIMARY_VALID,
        )
