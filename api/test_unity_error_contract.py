"""Validacion estatica del parser Unity para PublicErrorEnvelope v1."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_CLIENT = (ROOT / "unity_client" / "ApiClient.cs").read_text(encoding="utf-8-sig")
DTOS = (ROOT / "unity_client" / "DTOs.cs").read_text(encoding="utf-8-sig")
AGENT = (ROOT / "unity_client" / "LazzaroAgent.cs").read_text(encoding="utf-8-sig")
ERROR_BOUNDARY = (
    ROOT / "unity_client" / "UnityPublicErrorWireContractV1.cs"
).read_text(encoding="utf-8-sig")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _method_body(start_marker: str, end_marker: str) -> str:
    start = API_CLIENT.index(start_marker)
    end = API_CLIENT.index(end_marker, start)
    return API_CLIENT[start:end]


def test_01_unity_has_typed_error_envelope_and_failure_dtos() -> None:
    for expected in (
        "class PublicErrorEnvelope",
        "class PublicErrorDetail",
        "class ApiFailure",
        '[JsonProperty("correlation_id")]',
        "public bool TransportFailure",
    ):
        assert_true(expected in DTOS, f"Falta DTO Unity: {expected}")


def test_02_non_2xx_parses_envelope_before_safe_fallback() -> None:
    branch = _method_body("else\n            {", "private static bool TryParsePublicError")
    assert_true("TryParsePublicError(body" in branch, "non-2xx debe intentar envelope.")
    assert_true("{body}" not in branch and "+ body" not in branch, "Body crudo no puede entrar al failure.")
    assert_true("{request.error}" not in branch, "request.error no puede entrar a UX.")


def test_03_known_codes_use_local_allowlisted_messages() -> None:
    parser = _method_body("private static bool TryParsePublicError", "private static bool StatusCompatible")
    assert_true("MensajesPublicos.TryGetValue" in parser, "Code conocido debe resolver template local.")
    assert_true("envelope.error.message" not in parser, "Mensaje remoto nunca es authority de UX.")
    for code in ("TURN_ABORTED", "INTERNAL_ERROR", "VALIDATION_ERROR"):
        assert_true(code in API_CLIENT, f"Allowlist Unity incompleta: {code}")


def test_04_unknown_code_fails_closed_to_generic_local_error() -> None:
    parser = _method_body("private static bool TryParsePublicError", "private static bool StatusCompatible")
    assert_true("UnityPublicErrorWireContractV1.TryParse" in parser, "Debe delegar al boundary raw.")
    assert_true("!Codes.Contains(code)" in ERROR_BOUNDARY, "Code desconocido debe rechazarse.")
    assert_true('"BACKEND_ERROR"' in API_CLIENT, "Fallback debe conservar error local generico.")


def test_05_invalid_version_or_malformed_json_never_exposes_body() -> None:
    assert_true("IsExactInteger(root[\"version\"], 1)" in ERROR_BOUNDARY, "Debe validar version exacta.")
    assert_true("catch (Exception error)" in ERROR_BOUNDARY, "Parse fail debe cerrarse.")
    assert_true("return false;" in ERROR_BOUNDARY, "Parse fail debe cerrarse.")
    assert_true("CANARY_HTTP_BODY" not in API_CLIENT, "No debe existir body fixture en produccion.")


def test_06_success_ok_and_pending_keep_the_success_callback() -> None:
    assert_true(
        'respuesta.status == "ok" || respuesta.status == "pendiente"' in API_CLIENT,
        "ROOT-004 status valido debe conservarse.",
    )
    assert_true("onSuccess?.Invoke(respuesta)" in API_CLIENT, "Success callback no debe cambiar.")


def test_07_parse_exceptions_and_legacy_2xx_errors_use_fixed_local_failures() -> None:
    assert_true("catch (Exception e)" not in API_CLIENT, "No debe conservar e.Message.")
    assert_true("e.Message" not in API_CLIENT, "Parse exception no cruza UX.")
    assert_true('"INVALID_RESPONSE"' in API_CLIENT, "Parse fail necesita code local estable.")
    assert_true("respuesta.mensaje" not in API_CLIENT, "Error legacy tampoco usa mensaje remoto.")


def test_08_agent_consumes_only_safe_failure_rendering() -> None:
    assert_true("Action<ApiFailure> onFailure" in API_CLIENT, "Callback debe ser tipado.")
    assert_true("failure.ToSafeString()" in AGENT, "Consumer debe renderizar allowlist local.")
    assert_true("mensajeError =>" not in AGENT, "Consumer string legacy debe desaparecer.")


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-010 Unity static: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
