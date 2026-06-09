from __future__ import annotations

import json
import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.oracle_engine import OracleEngine
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class FakeOracleMemory:
    def __init__(self) -> None:
        self.cache: list[tuple[str, str]] = []

    def buscar_respuesta_cacheada(self, pregunta: str):
        return None

    def guardar_en_cache(self, pregunta: str, respuesta: str) -> bool:
        self.cache.append((pregunta, respuesta))
        return True


class FakeOracleClient:
    def __init__(self, configurado: bool, respuesta: str | None = None) -> None:
        self._configurado = configurado
        self.respuesta = respuesta
        self.calls = 0

    @property
    def configurado(self) -> bool:
        return self._configurado

    def consultar_oraculo(self, mensajes):
        self.calls += 1
        return self.respuesta


class FakeLLMAdapter:
    def __init__(self, respuesta: str) -> None:
        self.respuesta = respuesta
        self.calls = []

    def ejecutar_texto(self, mensajes, **kwargs):
        self.calls.append((mensajes, kwargs))
        assert_true(
            "required_provider" not in kwargs,
            "El fallback de Oraculo no debe exigir Ollama cuando hay otro cliente activo.",
        )
        return self.respuesta


def _respuesta_json(texto: str) -> str:
    return json.dumps(
        {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": texto},
            "pensamiento_privado": "Consulta analitica resuelta.",
            "razon": "Respuesta de prueba.",
        }
    )


def _orquestador_para_oracle(oracle_client, llm_adapter) -> Orchestrator:
    orquestador = object.__new__(Orchestrator)
    orquestador.oracle_engine = OracleEngine()
    orquestador.oracle_memory = FakeOracleMemory()
    orquestador.oracle_client = oracle_client
    orquestador.llm_adapter = llm_adapter
    return orquestador


def main() -> None:
    print("=== INICIANDO PRUEBA DE LA CAPA ORACULO ===\n")

    evento = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="test-oracle-active-fallback",
        detalles={"mensaje": "puedes recordar algo de tu base de datos?"},
    )

    llm_adapter = FakeLLMAdapter(_respuesta_json("Respuesta desde el cliente LLM activo."))
    oracle_client = FakeOracleClient(configurado=False)
    orquestador = _orquestador_para_oracle(oracle_client, llm_adapter)

    respuesta = orquestador._resolver_ruta_oracle(evento, politica="exploradora")
    assert_true(respuesta.accion == "hablar", "Oraculo debe responder hablando.")
    assert_true(respuesta.error is None, "Un fallback activo exitoso no debe marcar error.")
    assert_true(oracle_client.calls == 0, "OracleClient sin API key no debe consultarse.")
    assert_true(len(llm_adapter.calls) == 1, "Debe usarse el cliente LLM activo como fallback.")
    assert_true(
        respuesta.parametros["texto_a_comunicar"] == "Respuesta desde el cliente LLM activo.",
        "La respuesta del cliente activo debe llegar a Unity.",
    )

    llm_adapter_externo = FakeLLMAdapter(_respuesta_json("No deberia usarse."))
    oracle_client_externo = FakeOracleClient(
        configurado=True,
        respuesta=_respuesta_json("Respuesta desde Oraculo externo."),
    )
    orquestador_externo = _orquestador_para_oracle(oracle_client_externo, llm_adapter_externo)
    respuesta_externa = orquestador_externo._resolver_ruta_oracle(evento, politica="exploradora")

    assert_true(oracle_client_externo.calls == 1, "OracleClient configurado debe consultarse.")
    assert_true(len(llm_adapter_externo.calls) == 0, "Si Oraculo externo responde, no debe haber fallback.")
    assert_true(
        respuesta_externa.parametros["texto_a_comunicar"] == "Respuesta desde Oraculo externo.",
        "La respuesta externa debe conservarse.",
    )

    llm_adapter_texto = FakeLLMAdapter("Respuesta tecnica en texto plano.")
    oracle_client_texto = FakeOracleClient(configurado=False)
    orquestador_texto = _orquestador_para_oracle(oracle_client_texto, llm_adapter_texto)
    respuesta_texto = orquestador_texto._resolver_ruta_oracle(evento, politica="exploradora")

    assert_true(respuesta_texto.error is None, "Texto plano util no debe marcar error.")
    assert_true(
        respuesta_texto.parametros["texto_a_comunicar"] == "Respuesta tecnica en texto plano.",
        "Oraculo debe encapsular texto plano como respuesta hablada.",
    )

    print("[OK] test_oracle paso: Oraculo usa API externa o fallback LLM activo sin exigir Ollama.")


if __name__ == "__main__":
    main()
