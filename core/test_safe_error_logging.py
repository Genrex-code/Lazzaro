"""Canarios sinteticos para sinks humanos y AuditTrace de ROOT-010."""

from __future__ import annotations

from contextlib import contextmanager
import io
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import httpx

from ai_engine.gemini_client import GeminiClient
from ai_engine.ollama_client import OllamaClient
from ai_engine.openai_compatible_client import OpenAICompatibleClient
from ai_engine.oracle_client import OracleClient
from core.audit_trace import serialize_audit_trace
from core.safe_error_logging import log_safe_boundary_failure


CANARIES = (
    "CANARY_API_KEY_7E4A",
    "CANARY_PASSWORD_93B1",
    "CANARY_BEARER_11CC",
    "CANARY_HTTP_BODY_55AA",
    "C:\\SECRET_PATH",
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@contextmanager
def _captured_logs():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        handler.close()


def _assert_no_canary(text: str) -> None:
    for canary in CANARIES:
        assert_true(canary not in text, f"Canario filtrado al sink: {canary}")


def test_01_safe_log_uses_fields_without_exception_message() -> None:
    error = RuntimeError(
        "provider failed token=CANARY_API_KEY_7E4A "
        "Authorization: Bearer CANARY_BEARER_11CC password=CANARY_PASSWORD_93B1"
    )
    logger = logging.getLogger("root010.safe-test")
    with _captured_logs() as logs:
        correlation = log_safe_boundary_failure(
            logger,
            component="provider.generate",
            code="UPSTREAM_PROVIDER_FAILED",
            error=error,
            http_status=401,
        )
    captured = logs.getvalue()
    _assert_no_canary(captured)
    UUID(correlation)
    for expected in ("component=provider.generate", "error_type=RuntimeError", "http_status=401"):
        assert_true(expected in captured, f"Falta campo diagnostico {expected}")


def test_02_invalid_dynamic_identifiers_and_correlation_are_not_reflected() -> None:
    logger = logging.getLogger("root010.identifier-test")
    with _captured_logs() as logs:
        correlation = log_safe_boundary_failure(
            logger,
            component="CANARY_API_KEY_7E4A=bad",
            code="CANARY_BEARER_11CC bad",
            correlation_id="CANARY_PASSWORD_93B1",
            error=RuntimeError("CANARY_HTTP_BODY_55AA"),
        )
    captured = logs.getvalue()
    _assert_no_canary(captured)
    UUID(correlation)
    assert_true("component=unknown_component" in captured, "Component invalido debe cerrarse.")
    assert_true("code=INTERNAL_ERROR" in captured, "Code invalido debe cerrarse.")


def test_03_openai_http_body_enters_client_but_not_log() -> None:
    client = OpenAICompatibleClient(
        provider_name="fixture",
        api_key="synthetic-key",
        api_url="https://example.invalid/v1/chat/completions",
        model_name="fixture-model",
    )

    class FakeHTTP:
        @staticmethod
        def post(*args, **kwargs):
            del args, kwargs
            request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
            response = httpx.Response(401, text="CANARY_HTTP_BODY_55AA", request=request)
            raise httpx.HTTPStatusError("CANARY_API_KEY_7E4A", request=request, response=response)

        @staticmethod
        def close():
            return None

    client.client.close()
    client.client = FakeHTTP()
    with _captured_logs() as logs:
        result = client.generate("synthetic prompt")
    assert_true(result == "", "Provider failure conserva fallback vacio.")
    captured = logs.getvalue()
    _assert_no_canary(captured)
    assert_true("http_status=401" in captured, "Debe sobrevivir status seguro.")


def test_04_gemini_exception_enters_backoff_but_not_log() -> None:
    client = object.__new__(GeminiClient)
    client.model_name = "fixture-model"
    client.generation_config = SimpleNamespace(model_dump=lambda **kwargs: {})
    client._build_generation_config = lambda overrides: {}

    def fail(**kwargs):
        del kwargs
        raise RuntimeError("retry in 0s token=CANARY_API_KEY_7E4A")

    client.client = SimpleNamespace(models=SimpleNamespace(generate_content=fail))
    with _captured_logs() as logs, patch("ai_engine.gemini_client.time.sleep", return_value=None):
        result = client.generate("synthetic prompt")
    assert_true(result == "", "Gemini conserva su fallback tras retries.")
    _assert_no_canary(logs.getvalue())
    assert_true("component=gemini.generate" in logs.getvalue(), "Debe conservar component.")


def test_05_ollama_url_userinfo_is_not_logged() -> None:
    settings = SimpleNamespace(
        OLLAMA_BASE_URL="https://user:CANARY_PASSWORD_93B1@example.invalid/path",
        OLLAMA_MODEL_NAME="fixture-model",
        OLLAMA_TIMEOUT_SECONDS=1,
    )
    with _captured_logs() as logs:
        client = OllamaClient(settings=settings)
        client.close()
    _assert_no_canary(logs.getvalue())
    assert_true("base_url" not in logs.getvalue(), "URL completa ya no pertenece al sink.")


def test_06_oracle_http_body_is_not_logged() -> None:
    settings = SimpleNamespace(
        ORACLE_API_URL="https://example.invalid/v1/chat/completions",
        ORACLE_API_KEY="synthetic-key",
        ORACLE_MODEL="fixture-model",
        ORACLE_TIMEOUT=1,
    )
    client = OracleClient(settings=settings)
    request = httpx.Request("POST", settings.ORACLE_API_URL)
    response = httpx.Response(500, text="CANARY_HTTP_BODY_55AA", request=request)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            del args, kwargs
            raise httpx.HTTPStatusError("CANARY_API_KEY_7E4A", request=request, response=response)

    with _captured_logs() as logs, patch("ai_engine.oracle_client.httpx.Client", FakeClient):
        result = client.consultar_oraculo([{"role": "user", "content": "fixture"}])
    assert_true(result is None, "Oracle conserva fallback None.")
    _assert_no_canary(logs.getvalue())
    assert_true("http_status=500" in logs.getvalue(), "Debe conservar HTTP seguro.")


def test_07_startup_exception_is_redacted_without_changing_lifecycle() -> None:
    main_source = (Path(__file__).resolve().parents[1] / "main.py").read_text(
        encoding="utf-8-sig"
    )
    try_position = main_source.index("    try:\n")
    settings_position = main_source.index("        settings = Settings()")
    except_position = main_source.index("    except Exception as error:", settings_position)
    boundary = main_source[try_position:except_position + 400]
    assert_true(try_position < settings_position < except_position, "Settings debe vivir dentro del boundary.")
    assert_true("log_safe_boundary_failure(" in boundary, "Startup debe usar el helper probado con canario.")
    assert_true('component="startup"' in boundary, "Startup necesita component fijo.")
    assert_true("exc_info=True" not in boundary and "str(error)" not in boundary, "Startup no vuelca traceback/mensaje.")


def test_08_audittrace_serializes_type_not_canary_message() -> None:
    payload = serialize_audit_trace(
        {
            "stage": "error",
            "id_transaccion": "root010-audit",
            "error": RuntimeError("CANARY_API_KEY_7E4A"),
        }
    )
    _assert_no_canary(payload)
    assert_true('"type":"RuntimeError"' in payload, "AuditTrace conserva solo el tipo.")


def main() -> None:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"  PASS {test.__name__}")
    print(f"[OK] ROOT-010 logs: {len(tests)}/{len(tests)} pruebas pasaron.")


if __name__ == "__main__":
    main()
