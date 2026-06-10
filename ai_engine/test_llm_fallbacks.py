from __future__ import annotations

import sys
from pathlib import Path


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
    def __init__(self, provider_name: str, respuesta: str):
        self.provider_name = provider_name
        self.respuesta = respuesta
        self.calls = 0

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        return self.respuesta

    def health_check(self) -> bool:
        return bool(self.respuesta)


def main() -> None:
    print("=== INICIANDO PRUEBA DE FALLBACKS LLM ===\n")

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

    print("[OK] test_llm_fallbacks paso: cadena y settings de proveedores externos funcionan.")


if __name__ == "__main__":
    main()
