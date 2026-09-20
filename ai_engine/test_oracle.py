from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.oracle_engine import OracleEngine
from ai_engine.oracle_memory import OracleCacheLookupResultV1
from ai_engine.knowledge_provenance import (
    CacheEntryIdentityV1,
    CacheLookupStatusV1,
    CacheStoreStatusV1,
    KnowledgeProducerKindV1,
    KnowledgeProducerRouteV1,
    ProducerReceiptV1,
    cache_receipt_for_lookup,
    oracle_cache_logical_key,
    oracle_query_sha256,
    provenance_for,
    with_cache_store_status,
)
from ai_engine.fallback_ai_client import FallbackAIClient
from ai_engine.llm_call_control import (
    LLMCallResultV1,
    LLMCallTerminalV1,
    ProviderAttemptReceiptV1,
)
from ai_engine.persona_engine import PersonaEngine
from ai_engine.provider_prompt_projection import project_chat_messages
from ai_engine.schemas import ActionResponse, OracleResult
from ai_engine.structured_parse_types import StructuredParseStatus
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

    def buscar_resultado_cacheado_con_recibo(self, pregunta: str):
        if self.cache_result is not None:
            return OracleCacheLookupResultV1(
                1,
                self.cache_result,
                self.cache_result.provenance.retrieval.cache,
            )
        receipt = cache_receipt_for_lookup(
            pregunta,
            status=CacheLookupStatusV1.MISS,
            threshold=0.35,
        )
        return OracleCacheLookupResultV1(1, None, receipt)

    def buscar_resultado_cacheado(self, pregunta: str):
        return self.buscar_resultado_cacheado_con_recibo(pregunta).cached_result

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

    def guardar_resultado_en_cache(self, resultado: OracleResult):
        self.guardados.append(
            {
                "pregunta": resultado.pregunta_original,
                "contenido_tecnico": resultado.contenido_tecnico,
                "resumen_para_persona": resultado.resumen_para_persona,
                "fuente": resultado.fuente,
            }
        )
        identity = CacheEntryIdentityV1(
            entry_id="fixture-entry",
            logical_key=oracle_cache_logical_key(resultado.pregunta_original),
            stored_query_sha256=oracle_query_sha256(resultado.pregunta_original),
            cache_schema_version=1,
            knowledge_contract_version=1,
            provenance_version=1,
            entry_created_at_utc=datetime.now(timezone.utc),
        )
        return with_cache_store_status(
            resultado.provenance.retrieval.cache,
            CacheStoreStatusV1.STORED,
            stored_entry=identity,
        )


class FakeOracleClient:
    provider_name = "oracle_fake"
    model_name = "oracle-fixture"

    def __init__(self, configurado: bool, respuesta: str | None = None) -> None:
        self._configurado = configurado
        self.respuesta = respuesta
        self.calls = 0

    @property
    def configurado(self) -> bool:
        return self._configurado

    async def attempt_async(self, *, operation, value, child_budget, generation):
        del value, child_budget, generation
        assert_true(operation == "plan", "Oracle fake sólo admite plan.")
        self.calls += 1
        return self.respuesta or ""


class FakeLLMAdapter:
    def __init__(self, respuestas: list[str]) -> None:
        self.respuestas = list(respuestas)
        self.calls = []
        owner = self

        class ActiveProvider:
            provider_name = "active-fixture"
            model_name = "active-fixture-model"

            async def attempt_async(self, *, operation, value, child_budget, generation):
                del operation, child_budget, generation
                del value
                return owner._pop()

        self.ai_client = SimpleNamespace(providers=(ActiveProvider(),))

    def ejecutar_plan(self, mensajes, **kwargs):
        return self._next(mensajes, kwargs)

    def ejecutar_plan_result(self, mensajes, **kwargs):
        providers = tuple(kwargs.get("providers") or ())
        if providers:
            self.calls.append((mensajes, kwargs))
            return FallbackAIClient(providers).execute_plan_result(
                mensajes,
                budget=kwargs.get("budget"),
                generation={},
                providers=providers,
            )
        text = self._next(mensajes, kwargs)
        receipt = ProviderAttemptReceiptV1(
            provider="persona-fixture",
            model_id="persona-fixture",
            failure_class=None,
            error_type=None,
            http_status=None,
            retry_after_seconds=None,
            started_ns=1,
            finished_ns=2,
        )
        return LLMCallResultV1(
            terminal=LLMCallTerminalV1.SUCCESS,
            final_text=text,
            final_provider="persona-fixture",
            final_model_id="persona-fixture",
            final_failure=None,
            receipts=(receipt,),
        )

    def _pop(self):
        if not self.respuestas:
            raise AssertionError("FakeLLMAdapter no tiene mas respuestas preparadas.")
        return self.respuestas.pop(0)

    def _next(self, mensajes, kwargs):
        self.calls.append((mensajes, kwargs))
        assert_true(
            "required_provider" not in kwargs,
            "El flujo de Oraculo no debe exigir un proveedor fijo.",
        )
        return self._pop()


def _oracle_json(contenido: str, resumen: str | None = None) -> str:
    return json.dumps(
        {
            "contenido_tecnico": contenido,
            "resumen_para_persona": resumen or f"Resumen: {contenido}",
        }
    )


def _persona_json(texto: str, template: str = "ORACLE_GENERATED") -> str:
    del texto  # ROOT-017: model output cannot own public free prose.
    return json.dumps(
        {
            "version": 1,
            "acts": [{"kind": "ATTRIBUTED_TEXT", "evidence_id": "pe-0007",
                      "template_id": template, "display": "EXCERPT_240"}],
        }
    )


def _producer(
    route: KnowledgeProducerRouteV1 = KnowledgeProducerRouteV1.ORACLE_EXTERNAL,
) -> ProducerReceiptV1:
    provider = "oracle_fake" if route is KnowledgeProducerRouteV1.ORACLE_EXTERNAL else "active-fixture"
    return ProducerReceiptV1(
        kind=KnowledgeProducerKindV1.PROVIDER_GENERATED,
        route=route,
        provider=provider,
        model_id=f"{provider}-model",
        final_parse_status=StructuredParseStatus.VALID,
        parse_contract_version=1,
        provider_fallback_used=False,
        scheduler_attempt_count=1,
        failure_classes=(),
        generation_terminal=LLMCallTerminalV1.SUCCESS,
        final_failure=None,
        accepted_at_utc=datetime.now(timezone.utc),
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
        producer=_producer(),
        cache_receipt=cache_receipt_for_lookup(
            "explicame este bug",
            status=CacheLookupStatusV1.MISS,
            threshold=0.35,
        ),
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
        "Resumen: Respuesta fria desde el LLM activo." in respuesta.parametros["texto_a_comunicar"],
        "Persona debe atribuir el contenido tecnico en vez de publicar texto libre.",
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
    assert_true(
        len(llm_adapter.calls) == 2,
        "La ruta tipada registra scheduling Oracle y luego verbalizacion Persona.",
    )
    assert_true(
        audit_observation == {"oracle_source": "api"},
        "Una respuesta externa exitosa debe conservar fuente api.",
    )
    assert_true(
        "Resumen: Respuesta fria desde Oraculo externo." in respuesta.parametros["texto_a_comunicar"],
        "Persona debe atribuir el resultado externo.",
    )


def test_oracle_cache_returns_oracle_result_and_calls_persona_only() -> None:
    cache_result = OracleResult(
        pregunta_original="explicame este error de arquitectura",
        contenido_tecnico="Conocimiento frio recuperado de cache.",
        resumen_para_persona="Cache lista para verbalizar.",
        provenance=provenance_for(
            _producer(),
            cache_receipt_for_lookup(
                "explicame este error de arquitectura",
                status=CacheLookupStatusV1.HIT,
                threshold=0.35,
                matched_entry=CacheEntryIdentityV1(
                    entry_id="cache-fixture",
                    logical_key=oracle_cache_logical_key(
                        "explicame este error de arquitectura"
                    ),
                    stored_query_sha256=oracle_query_sha256(
                        "explicame este error de arquitectura"
                    ),
                    cache_schema_version=1,
                    knowledge_contract_version=1,
                    provenance_version=1,
                    entry_created_at_utc=datetime.now(timezone.utc),
                ),
                distance=0.1,
            ),
        ),
    )
    memoria = FakeOracleMemory(cache_result=cache_result)
    llm_adapter = FakeLLMAdapter([_persona_json("", "ORACLE_CACHED")])
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
        "Cache lista para verbalizar." in respuesta.parametros["texto_a_comunicar"]
        and "previo" in respuesta.parametros["texto_a_comunicar"],
        "Persona debe atribuir el cache hit como resultado previo.",
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
