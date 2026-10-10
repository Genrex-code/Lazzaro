"""ROOT-038 B2: paridad estatica del contrato canonico de rotacion."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_engine.motor_schemas import MotorActionAdapterV1


ROOT = Path(__file__).resolve().parents[1]
AGENT = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
MOVEMENT = (ROOT / "unity_client" / "MovementController.cs").read_text(
    encoding="utf-8-sig"
)


def _method(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


@pytest.mark.parametrize("degrees", (-360.0, -0.5, -0.1, 0.0, 0.1, 0.5, 360.0))
def test_python_canonical_domain_is_preserved_at_unity_consumer(degrees: float) -> None:
    canonical = MotorActionAdapterV1.require("girar_avatar", {"grados": degrees})
    assert canonical.parameters_dict()["grados"] == degrees

    parser = _method(
        AGENT,
        "private static bool TryLeerGradosRotacion(",
        "private void ManejarMovimientoCompletado(",
    )
    assert "valor < -360.0d" in parser
    assert "valor > 360.0d" in parser
    assert "Mathf.Abs" not in parser
    assert "Mathf.Clamp" not in parser


def test_wire_boundary_accepts_only_finite_json_numbers_in_range() -> None:
    parser = _method(
        AGENT,
        "private static bool TryLeerGradosRotacion(",
        "private void ManejarMovimientoCompletado(",
    )

    assert 'TryGetValue("grados", out JToken token)' in parser
    assert "token.Type != JTokenType.Integer" in parser
    assert "token.Type != JTokenType.Float" in parser
    assert "double.IsNaN(valor)" in parser
    assert "double.IsInfinity(valor)" in parser
    assert "valor < -360.0d" in parser
    assert "valor > 360.0d" in parser
    assert "float.TryParse" not in parser


def test_invalid_present_rotation_fails_before_dispatch() -> None:
    handler = _method(
        AGENT,
        "private void EjecutarGirarAvatar(",
        "private static bool TryLeerGradosRotacion(",
    )

    validation = handler.index("TryLeerGradosRotacion")
    dispatch = handler.index("movimiento.GirarAvatar")
    assert validation < dispatch
    invalid_branch = handler.split("if (!TryLeerGradosRotacion", 1)[1].split(
        "ContextoAccion movimientoContexto", 1
    )[0]
    assert "ReportarResultadoAccion" in invalid_branch
    assert "false" in invalid_branch
    assert "return;" in invalid_branch


def test_exact_valid_value_reaches_movement_controller_without_rewrite() -> None:
    handler = _method(
        AGENT,
        "private void EjecutarGirarAvatar(",
        "private static bool TryLeerGradosRotacion(",
    )

    assert '{ "grados", grados }' in handler
    assert "movimiento.GirarAvatar(contexto.IdAccion, grados);" in handler
    assert "Math.Round" not in handler
    assert "Mathf.Clamp" not in handler
    assert "Mathf.Abs" not in handler


def test_movement_controller_rejects_nonfinite_and_out_of_range_direct_calls() -> None:
    handler = _method(
        MOVEMENT,
        "public void GirarAvatar(",
        "private static bool EsRotacionValida(",
    )
    validator = _method(
        MOVEMENT,
        "private static bool EsRotacionValida(",
        "public void FrenarMovimiento()",
    )

    assert handler.index("EsRotacionValida") < handler.index("PuedeEjecutarOrden")
    assert "RechazarMovimiento" in handler
    assert "float.IsNaN(grados)" in validator
    assert "float.IsInfinity(grados)" in validator
    assert "grados >= -360f" in validator
    assert "grados <= 360f" in validator


def test_zero_degrees_completes_successfully_without_physical_coroutine() -> None:
    handler = _method(
        MOVEMENT,
        "public void GirarAvatar(",
        "private static bool EsRotacionValida(",
    )
    zero_branch = handler.split("if (grados == 0f)", 1)[1].split(
        "IniciarRutinaMovimiento", 1
    )[0]

    assert "OnMovimientoCompletado?.Invoke(actionId, true" in zero_branch
    assert "return;" in zero_branch
    assert "MoveRotation" not in zero_branch
    assert "StartCoroutine" not in zero_branch


def test_subdegree_rotation_uses_exact_requested_delta() -> None:
    handler = _method(
        MOVEMENT,
        "public void GirarAvatar(",
        "private static bool EsRotacionValida(",
    )
    coroutine = _method(
        MOVEMENT,
        "private IEnumerator GirarHastaAngulo(",
        "private void AplicarMovimientoFisico(",
    )

    assert "Mathf.Abs(grados) < 1f" not in handler
    assert "Mathf.Clamp(grados" not in handler
    assert "GirarHastaAngulo(actionId, grados)" in handler
    assert "transform.eulerAngles.y + grados" in coroutine
    assert "rb.MoveRotation(rotacionDestino);" in coroutine


def test_root029_and_root042_action_material_remain_unchanged() -> None:
    handler = _method(
        AGENT,
        "private void EjecutarGirarAvatar(",
        "private static bool TryLeerGradosRotacion(",
    )

    assert "contexto.IdAccion" in handler
    assert "contexto.ConDetalles" in handler
    assert "contexto.PerceptionAuthority" not in handler
    assert "movimiento.GirarAvatar(contexto.IdAccion, grados);" in handler
