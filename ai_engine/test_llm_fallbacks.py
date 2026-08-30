from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sys
import threading
from pathlib import Path

import httpx


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.ai_client import BaseAIClient
from ai_engine.client_factory import build_ai_client
from ai_engine.fallback_ai_client import FallbackAIClient
from ai_engine.openai_compatible_client import OpenAICompatibleClient
from ai_engine.oracle_client import OracleClient
from config.settings import Settings


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class FakeClient(BaseAIClient):
    def __init__(self, provider_name: str, respuesta: str, model_name: str | None = None):
        self.provider_name = provider_name
        self.model_name = model_name or f"{provider_name}-test"
        self.respuesta = respuesta
        self.calls = 0

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        return self.respuesta

    def health_check(self) -> bool:
        return bool(self.respuesta)


class RaisingClient(FakeClient):
    def __init__(self, provider_name: str, error: BaseException, model_name: str):
        super().__init__(provider_name, "", model_name=model_name)
        self.error = error

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        raise self.error


def test_fallback_audit_is_call_scoped_and_thread_safe() -> None:
    primary = RaisingClient(
        "gemini",
        TimeoutError("SECRET_TIMEOUT_DETAIL"),
        "gemini-primary",
    )
    secondary = FakeClient("groq", "respuesta", model_name="groq-secondary")
    chain = FallbackAIClient([primary, secondary])
    chain.begin_audit_capture()

    response = chain.generate("SECRET_PROMPT")
    events = chain.consume_audit_events()

    assert_true(response == "respuesta", "El audit no debe cambiar el fallback funcional.")
    assert_true(len(events) == 2, "Debe observar los dos intentos existentes.")
    assert_true(events[0].provider == "gemini", "Debe conservar el proveedor primario fallido.")
    assert_true(events[0].error_type == "TimeoutError", "El timeout debe normalizarse por tipo.")
    assert_true(events[1].provider == "groq" and events[1].fallback_used, "Debe marcar el proveedor sustituto.")
    assert_true(events[1].status == "ok", "El proveedor efectivo debe quedar exitoso.")
    assert_true("SECRET" not in repr(events), "La metadata no debe conservar prompt ni mensaje de error.")

    barrier = threading.Barrier(2)

    class ConditionalPrimary(FakeClient):
        def generate(self, prompt: str, **kwargs) -> str:
            self.calls += 1
            barrier.wait(timeout=2)
            return "primaria" if prompt == "event-a" else ""

    shared_primary = ConditionalPrimary("gemini", "", model_name="gemini-concurrent")
    shared_secondary = FakeClient("groq", "secundaria", model_name="groq-concurrent")
    shared_chain = FallbackAIClient([shared_primary, shared_secondary])

    def run(prompt: str):
        shared_chain.begin_audit_capture()
        text = shared_chain.generate(prompt)
        return text, shared_chain.consume_audit_events(), shared_chain.last_provider_used

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_a = executor.submit(run, "event-a")
        future_b = executor.submit(run, "event-b")
        result_a = future_a.result(timeout=3)
        result_b = future_b.result(timeout=3)

    assert_true(result_a[0] == "primaria", "La transacción A debe usar el primario.")
    assert_true(result_a[2] == "gemini", "last_provider_used debe ser local al hilo A.")
    assert_true(len(result_a[1]) == 1 and not result_a[1][0].fallback_used, "A no debe heredar fallback de B.")
    assert_true(result_b[0] == "secundaria", "La transacción B debe usar el secundario.")
    assert_true(result_b[2] == "groq", "last_provider_used debe ser local al hilo B.")
    assert_true(len(result_b[1]) == 2 and result_b[1][-1].fallback_used, "B debe conservar su propio fallback.")


def test_openai_compatible_timeout_is_observable_without_message() -> None:
    client = OpenAICompatibleClient(
        provider_name="groq",
        api_key="sk-test",
        api_url="https://example.invalid/chat/completions",
        model_name="groq-test",
    )
    client.client.close()

    class TimeoutHTTPClient:
        @staticmethod
        def post(*args, **kwargs):
            request = httpx.Request("POST", "https://example.invalid/chat/completions")
            raise httpx.ReadTimeout("SECRET_HTTP_TIMEOUT", request=request)

        @staticmethod
        def close() -> None:
            return None

    client.client = TimeoutHTTPClient()
    client.begin_audit_capture()
    response = client.generate("SECRET_HTTP_PROMPT")
    events = client.consume_audit_events()
    client.close()

    assert_true(response == "", "El contrato funcional histórico debe conservar respuesta vacía.")
    assert_true(len(events) == 1 and events[0].status == "timeout", "Debe observar el timeout real.")
    assert_true(events[0].error_type == "TimeoutError", "Debe usar el tipo estable TimeoutError.")
    assert_true("SECRET" not in repr(events), "No debe conservar prompt ni mensaje HTTP.")


def test_audit_record_failure_cannot_change_successful_response() -> None:
    primary = FakeClient("gemini", "respuesta funcional", model_name="gemini-test")
    chain = FallbackAIClient([primary])

    def broken_audit_local():
        raise RuntimeError("SECRET_AUDIT_COLLECTOR_FAILURE")

    chain._audit_local = broken_audit_local
    response = chain.generate("SECRET_FUNCTIONAL_PROMPT")

    assert_true(
        response == "respuesta funcional",
        "Un fallo del collector no debe convertir una respuesta LLM exitosa en fallo.",
    )
    assert_true(primary.calls == 1, "El collector fallido no debe repetir el proveedor.")


def test_fallback_chain_and_settings_contract() -> None:
    primario = FakeClient("gemini", "")
    respaldo = FakeClient("groq", "respuesta desde fallback")
    chain = FallbackAIClient([primario, respaldo])

    respuesta = chain.generate("hola")
    assert_true(respuesta == "respuesta desde fallback", "Debe usar el segundo proveedor si el primero falla.")
    assert_true(primario.calls == 1, "Debe intentar primero el primario.")
    assert_true(respaldo.calls == 1, "Debe consultar el fallback.")
    assert_true(chain.provider_name == "gemini", "La politica debe ver el proveedor primario, no toda la cadena.")
    assert_true(chain.last_provider_used == "groq", "Debe registrar que proveedor respondio.")

    settings_alias = Settings(
        _env_file=None,
        AI_PROVIDER="deepseek_api",
        DEEPSEEEK_API_KEY="sk-test",
    )
    assert_true(
        settings_alias.DEEPSEEK_API_KEY == "sk-test",
        "Settings debe aceptar DEEPSEEEK_API_KEY por compatibilidad con el .env actual.",
    )

    settings_factory = Settings(
        _env_file=None,
        AI_PROVIDER="groq",
        AI_FALLBACK_ORDER="groq,kimi",
        GROQ_API_KEY=None,
        KIMI_API_KEY="sk-kimi",
        LLM_FALLBACKS_ENABLED=True,
    )
    cliente = build_ai_client(settings_factory)
    assert_true(
        isinstance(cliente, OpenAICompatibleClient),
        "Si el primario no tiene key, factory debe saltar al fallback configurado.",
    )
    assert_true(cliente.provider_name == "kimi", "El fallback construido debe ser Kimi.")
    cliente.close()

    settings_oracle = Settings(
        _env_file=None,
        AI_PROVIDER="gemini",
        GEMINI_API_KEY="sk-gemini",
        ORACLE_API_KEY=None,
        GROQ_API_KEY="sk-groq",
    )
    oracle_client = OracleClient(settings=settings_oracle)
    assert_true(
        oracle_client.configurado,
        "OracleClient debe usar GROQ_API_KEY cuando ORACLE_API_KEY no esta definida.",
    )

def main() -> None:
    print("=== INICIANDO PRUEBA DE FALLBACKS LLM ===\n")
    tests = [
        test_fallback_chain_and_settings_contract,
        test_fallback_audit_is_call_scoped_and_thread_safe,
        test_openai_compatible_timeout_is_observable_without_message,
        test_audit_record_failure_cannot_change_successful_response,
    ]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    print("[OK] test_llm_fallbacks paso: cadena, auditoría y settings funcionan.")


if __name__ == "__main__":
    main()
