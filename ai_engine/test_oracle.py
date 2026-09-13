from __future__ import annotations

import json
import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.oracle_engine import OracleEngine
from ai_engine.persona_engine import PersonaEngine
from ai_engine.provider_prompt_projection import project_chat_messages
from ai_engine.schemas import ActionResponse, OracleResult
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator
from core.behavior_core import BehaviorCore
from core.identity_snapshot import IdentitySnapshotProvider, build_static_self_v1


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class FakeOracleMemory:
    def __init__(self, cache_result: OracleResult | None = None) -> None:
        self.cache_result = cache_result
        self.guardados: list[dict[str, str | None]] = []

    def buscar_resultado_cacheado(self, pregunta: str):
        return self.cache_result

    def buscar_respuesta_cacheada(self, pregunta: str):
        return None

    def guardar_en_cache(
        self,
        pregunta: str,
        contenido_tecnico: str,
        resumen_para_persona: str | None = None,
        fuente: str = "api",
    ) -> bool:
        self.guardados.append(
            {
                "pregunta": pregunta,
                "contenido_tecnico": contenido_tecnico,
                "resumen_para_persona": resumen_para_persona,
                "fuente": fuente,
            }
        )
        return True


class FakeOracleClient:
    def __init__(self, configurado: bool, respuesta: str | None = None) -> None:
        self._configurado = configurado
        self.respuesta = respuesta
        self.calls = 0

    @property
    def configurado(self) -> bool:
        return self._configurado

    def consultar_plan(self, mensajes):
        self.calls += 1
        return self.respuesta


class FakeLLMAdapter:
    def __init__(self, respuestas: list[str]) -> None:
        self.respuestas = list(respuestas)
        self.calls = []

    def ejecutar_plan(self, mensajes, **kwargs):
        self.calls.append((mensajes, kwargs))
        assert_true(
            "required_provider" not in kwargs,
            "El flujo de Oraculo no debe exigir un proveedor fijo.",
        )
        if not self.respuestas:
            raise AssertionError("FakeLLMAdapter no tiene mas respuestas preparadas.")
        return self.respuestas.pop(0)


def _oracle_json(contenido: str, resumen: str | None = None) -> str:
    return json.dumps(
        {
            "contenido_tecnico": contenido,
            "resumen_para_persona": resumen or f"Resumen: {contenido}",
        }
    )


def _persona_json(texto: str) -> str:
    return json.dumps(
        {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": texto},
            "pensamiento_privado": "Persona verbaliza conocimiento interno.",
            "razon": "Respuesta publica generada por Capa 2.",
        }
    )


def _orquestador_para_oracle(
    oracle_client,
    llm_adapter,
    oracle_memory: FakeOracleMemory | None = None,
) -> Orchestrator:
    orquestador = object.__new__(Orchestrator)
    orquestador.oracle_engine = OracleEngine()
    orquestador.oracle_memory = oracle_memory or FakeOracleMemory()
    orquestador.oracle_client = oracle_client
    orquestador.llm_adapter = llm_adapter
    orquestador.persona_engine = PersonaEngine()
    behavior = BehaviorCore()
    orquestador.identity_provider = IdentitySnapshotProvider(
        build_static_self_v1(), behavior
    )
    return orquestador


def _evento_oracle() -> EventoEntrante:
    return EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="test-oracle-contract",
        detalles={"mensaje": "explicame este error de arquitectura"},
    )


def test_oracle_returns_oracle_result() -> None:
    memoria = FakeOracleMemory()
    engine = OracleEngine()
    prompt = engine.preparar_consulta("explicame este bug")
    prompt_texto = "\n".join(mensaje.content for mensaje in project_chat_messages(prompt))

    assert_true('"contenido_tecnico"' in prompt_texto, "El prompt debe pedir contenido tecnico.")
    assert_true('"accion":"hablar"' not in prompt_texto, "El Oraculo no debe pedir hablar.")
    assert_true('"parametros"' not in prompt_texto, "El Oraculo no debe pedir payload de Unity.")

    resultado = engine.keep_y_save(
        _oracle_json("Diagnostico tecnico frio.", "Resumen tecnico breve."),
        "explicame este bug",
        memoria,
        fuente="api",
    )

    assert_true(isinstance(resultado, OracleResult), "OracleEngine debe devolver OracleResult.")
    assert_true(not isinstance(resultado, ActionResponse), "OracleEngine no debe devolver ActionResponse.")
    assert_true(not hasattr(resultado, "accion"), "OracleResult no debe exponer accion.")
    assert_true(resultado.contenido_tecnico == "Diagnostico tecnico frio.", "Debe conservar contenido frio.")
    assert_true(memoria.guardados[0]["contenido_tecnico"] == "Diagnostico tecnico frio.", "Debe cachear contenido tecnico.")


def test_orchestrator_oracle_fallback_calls_persona() -> None:
    llm_adapter = FakeLLMAdapter(
        [
            _oracle_json("Respuesta fria desde el LLM activo."),
            _persona_json("Lo traduzco con mi voz, sin sonar como el Oraculo."),
        ]
    )
    oracle_client = FakeOracleClient(configurado=False)
    orquestador = _orquestador_para_oracle(oracle_client, llm_adapter)
    audit_observation = {}

    respuesta = orquestador._resolver_ruta_oracle(
        evento=_evento_oracle(),
        contexto_llm={},
        memoria_texto="",
        politica="exploradora",
        audit_observation=audit_observation,
    )

    assert_true(respuesta.accion == "hablar", "La respuesta publica final debe ser hablar.")
    assert_true(
        respuesta.parametros["texto_a_comunicar"] == "Lo traduzco con mi voz, sin sonar como el Oraculo.",
        "La voz final debe venir de Persona.",
    )
    assert_true(oracle_client.calls == 0, "OracleClient sin API key no debe consultarse.")
    assert_true(len(llm_adapter.calls) == 2, "Debe llamar LLM para Oraculo frio y luego Persona.")
    assert_true(
        audit_observation == {"oracle_source": "fallback"},
        "Oracle sin credenciales debe declarar fallback sin inventar un error.",
    )
    assert_true(
        "Oraculo" in llm_adapter.calls[0][1]["error_context"],
        "La primera llamada debe ser la consulta fria de Oraculo.",
    )
    assert_true(
        "Verbalizacion" in llm_adapter.calls[1][1]["error_context"],
        "La segunda llamada debe ser la verbalizacion social.",
    )


def test_orchestrator_oracle_external_then_persona() -> None:
    llm_adapter = FakeLLMAdapter([_persona_json("Respuesta social desde conocimiento externo.")])
    oracle_client = FakeOracleClient(
        configurado=True,
        respuesta=_oracle_json("Respuesta fria desde Oraculo externo."),
    )
    orquestador = _orquestador_para_oracle(oracle_client, llm_adapter)
    audit_observation = {}

    respuesta = orquestador._resolver_ruta_oracle(
        evento=_evento_oracle(),
        contexto_llm={},
        memoria_texto="",
        politica="exploradora",
        audit_observation=audit_observation,
    )

    assert_true(oracle_client.calls == 1, "OracleClient configurado debe consultarse.")
    assert_true(len(llm_adapter.calls) == 1, "Con API externa solo Persona debe usar el LLM local/activo.")
    assert_true(
        audit_observation == {"oracle_source": "api"},
        "Una respuesta externa exitosa debe conservar fuente api.",
    )
    assert_true(
        respuesta.parametros["texto_a_comunicar"] == "Respuesta social desde conocimiento externo.",
        "Persona debe verbalizar el resultado externo.",
    )


def test_oracle_cache_returns_oracle_result_and_calls_persona_only() -> None:
    cache_result = OracleResult(
        pregunta_original="explicame este error de arquitectura",
        contenido_tecnico="Conocimiento frio recuperado de cache.",
        resumen_para_persona="Cache lista para verbalizar.",
        fuente="cache",
        cache_hit=True,
    )
    memoria = FakeOracleMemory(cache_result=cache_result)
    llm_adapter = FakeLLMAdapter([_persona_json("Recupere esa idea y te la explico a mi manera.")])
    oracle_client = FakeOracleClient(configurado=True, respuesta=_oracle_json("No debe usarse."))
    orquestador = _orquestador_para_oracle(oracle_client, llm_adapter, oracle_memory=memoria)
    audit_observation = {}

    respuesta = orquestador._resolver_ruta_oracle(
        evento=_evento_oracle(),
        contexto_llm={},
        memoria_texto="",
        politica="exploradora",
        audit_observation=audit_observation,
    )

    assert_true(oracle_client.calls == 0, "Un cache hit no debe llamar OracleClient.")
    assert_true(len(llm_adapter.calls) == 1, "Un cache hit solo debe llamar Persona.")
    assert_true(memoria.guardados == [], "Un cache hit no debe reescribir cache.")
    assert_true(
        audit_observation == {"oracle_source": "cache"},
        "El cache hit debe conservar fuente cache sin marcar fallback.",
    )
    assert_true(
        respuesta.parametros["texto_a_comunicar"] == "Recupere esa idea y te la explico a mi manera.",
        "Persona debe verbalizar el cache hit.",
    )


def main() -> None:
    print("=== INICIANDO PRUEBA DE CONTRATO ORACULO -> PERSONA ===\n")
    test_oracle_returns_oracle_result()
    test_orchestrator_oracle_fallback_calls_persona()
    test_orchestrator_oracle_external_then_persona()
    test_oracle_cache_returns_oracle_result_and_calls_persona_only()
    print("[OK] test_oracle paso: Oraculo queda frio y Persona conserva la voz publica.")


if __name__ == "__main__":
    main()
