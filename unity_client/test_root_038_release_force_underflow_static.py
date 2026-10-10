"""ROOT-038 B4: guardia estatica de underflow para fuerza de lanzamiento."""

from __future__ import annotations

import math
from pathlib import Path
import struct

import pytest


ROOT = Path(__file__).resolve().parents[1]
AGENT = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
MATERIALIZER = (
    ROOT / "unity_client" / "ObjectMaterializationController.cs"
).read_text(encoding="utf-8-sig")
MISSING = object()


def _method(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def _float32(value: float) -> float:
    return struct.unpack("!f", struct.pack("!f", value))[0]


def _release_force_boundary(
    value: object = MISSING, *, parameter_object_present: bool = True
) -> tuple[bool, float]:
    """Modelo ejecutable del contrato acotado que el test fija en el C# estático."""

    if not parameter_object_present:
        return False, 0.0
    if value is MISSING:
        return True, 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, 0.0
    raw = float(value)
    if not math.isfinite(raw) or raw < 0.0 or raw > 20.0:
        return False, 0.0
    converted = _float32(raw)
    if (
        not math.isfinite(converted)
        or converted < 0.0
        or converted > 20.0
        or (raw > 0.0 and converted == 0.0)
    ):
        return False, 0.0
    return True, converted


def _parser() -> str:
    return _method(
        AGENT,
        "private static bool TryLeerFuerzaLanzamiento(",
        "private static bool TryLeerDistanciaNavegacionObjeto(",
    )


def _release_handler() -> str:
    return _method(
        AGENT,
        "private void EjecutarSoltar(ContextoAccion contexto)",
        "private void EjecutarAplicarFuerza(ContextoAccion contexto)",
    )


def test_null_parameter_object_is_rejected() -> None:
    valid, force = _release_force_boundary(parameter_object_present=False)
    parser = _parser()

    null_check = parser.index("if (parametros == null)")
    missing_field_check = parser.index(
        '!parametros.TryGetValue("fuerza_lanzamiento", out JToken token)'
    )
    null_branch = parser.split("if (parametros == null)", 1)[1].split(
        "if (!parametros.TryGetValue", 1
    )[0]

    assert not valid and force == 0.0
    assert null_check < missing_field_check
    assert "return false;" in null_branch


def test_nonnull_empty_parameter_object_keeps_zero_release_only_default() -> None:
    valid, force = _release_force_boundary()
    parser = _parser()
    materializer = _method(
        MATERIALIZER,
        "public MaterialEffectReceipt TryRelease(",
        "public void ApplyForce(",
    )

    assert valid and force == 0.0
    assert "fuerza = 0f;" in parser
    assert '!parametros.TryGetValue("fuerza_lanzamiento", out JToken token)' in parser
    assert "return true;" in parser.split("TryGetValue", 1)[1]
    assert "if (throwForce > 0f" in materializer


def test_explicit_null_force_is_rejected() -> None:
    valid, force = _release_force_boundary(None)
    parser = _parser()

    assert not valid and force == 0.0
    assert "token == null" in parser
    assert "token.Type != JTokenType.Integer" in parser
    assert "token.Type != JTokenType.Float" in parser


@pytest.mark.parametrize("raw", (0.0, 1.0, 20.0))
def test_zero_normal_positive_and_upper_bound_are_accepted(raw: float) -> None:
    valid, force = _release_force_boundary(raw)

    assert valid
    assert force == raw


@pytest.mark.parametrize("raw", (-0.01, 20.0001))
def test_out_of_range_values_are_rejected(raw: float) -> None:
    valid, _ = _release_force_boundary(raw)
    parser = _parser()

    assert not valid
    assert "valorRaw < 0.0d" in parser
    assert "valorRaw > 20.0d" in parser
    assert "valorUnity < 0.0f" in parser
    assert "valorUnity > 20.0f" in parser


@pytest.mark.parametrize("raw", (True, False, "1.0"))
def test_bool_and_numeric_string_are_rejected(raw: object) -> None:
    valid, _ = _release_force_boundary(raw)
    parser = _parser()

    assert not valid
    assert "token == null" in parser
    assert "token.Type != JTokenType.Integer" in parser
    assert "token.Type != JTokenType.Float" in parser
    assert "float.TryParse" not in parser


@pytest.mark.parametrize("raw", (math.nan, math.inf, -math.inf))
def test_nonfinite_values_are_rejected(raw: float) -> None:
    valid, _ = _release_force_boundary(raw)
    parser = _parser()

    assert not valid
    assert "double.IsNaN(valorRaw)" in parser
    assert "double.IsInfinity(valorRaw)" in parser
    assert "float.IsNaN(valorUnity)" in parser
    assert "float.IsInfinity(valorUnity)" in parser


def test_original_double_is_validated_before_float_conversion() -> None:
    parser = _parser()

    raw_read = parser.index("valorRaw = token.Value<double>();")
    raw_validation = parser.index("double.IsNaN(valorRaw)")
    conversion = parser.index("float valorUnity = (float)valorRaw;")
    converted_validation = parser.index("float.IsNaN(valorUnity)")

    assert raw_read < raw_validation < conversion < converted_validation


def test_positive_double_that_underflows_to_float_zero_is_rejected() -> None:
    raw = 5e-324
    assert raw > 0.0
    assert _float32(raw) == 0.0

    valid, _ = _release_force_boundary(raw)
    parser = _parser()

    assert not valid
    assert "valorRaw > 0.0d && valorUnity == 0.0f" in parser


def test_invalid_force_returns_before_try_release_and_has_no_release_call() -> None:
    handler = _release_handler()
    validation = handler.index("TryLeerFuerzaLanzamiento")
    dispatch = handler.index("materializacion.TryRelease")
    invalid_branch = handler.split(
        "if (!TryLeerFuerzaLanzamiento", 1
    )[1].split("MaterialEffectReceipt receipt", 1)[0]

    assert validation < dispatch
    assert "invalid_physical_parameters" in invalid_branch
    assert "ReportarResultadoMaterial" in invalid_branch
    assert "return;" in invalid_branch
    assert "TryRelease" not in invalid_branch
    assert handler.count("materializacion.TryRelease") == 1


def test_root005_material_receipt_semantics_are_preserved() -> None:
    handler = _release_handler()

    assert "ReportarResultadoAccion(" not in handler
    assert "MaterialEffectReceipt.Failed(" in handler
    assert "MaterialEffectReceipt receipt = materializacion.TryRelease" in handler
    assert "ReportarResultadoMaterial(contexto, receipt);" in handler
