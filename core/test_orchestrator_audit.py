"""Pruebas de integración acotada de Orchestrator AuditTrace (OBS-003/005)."""

from __future__ import annotations

import json
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from api.schemas import AccionSaliente, EventoEntrante
from core.audit_trace import AuditTrace
from core.audit_writer import AuditTraceJSONLWriter
from core.orchestrator import Orchestrator
from memory.memory_manager import MemoryContextResult, MemoryManager
from memory.memory_types import (
    EpisodicMemory,
    MemoryMetadata,
    MemoryResult,
    SemanticMemory,
)
from memory.working_memory import WorkingMemory
from terminal_client.test_chat import _orquestador_aislado


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingWriter:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.traces: list[AuditTrace] = []

    def write(self, trace: AuditTrace) -> bool:
        self.traces.append(trace)
        return self.result


class StageFailingWriter(RecordingWriter):
    def __init__(self, failing_stage: str) -> None:
        super().__init__()
        self.failing_stage = failing_stage
        self.attempted_stages: list[str] = []

    def write(self, trace: AuditTrace) -> bool:
        self.attempted_stages.append(trace.stage)
        if trace.stage == self.failing_stage:
            raise OSError("simulated audit failure")
        return super().write(trace)


def _event(transaction_id: str = "obs-003-transaction") -> EventoEntrante:
    return EventoEntrante(
        origen="terminal",
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles={"mensaje": "Hola, ¿sigues ahí?"},
    )


def _full_flow_event(origin: str, transaction_id: str) -> EventoEntrante:
    return EventoEntrante(
        origen=origin,
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles={"mensaje": "Procesa este turno.", "solicita_decision": True},
    )


def _count_triage_calls(orchestrator: Orchestrator) -> dict[str, int]:
    counter = {"calls": 0}
    route_event = orchestrator.triage_router.route_event

    def counted_route_event(evento: dict, estado_agente: dict) -> dict:
        counter["calls"] += 1
        return route_event(evento, estado_agente)

    orchestrator.triage_router.route_event = counted_route_event
    return counter


def _ranked_memory_results() -> list[MemoryResult]:
    episodic_metadata = MemoryMetadata(
        tipo_evento="evento_prueba",
        valencia=0.1,
        impacto_emocional=0.7,
    )
    semantic_metadata = MemoryMetadata(
        tipo_evento="regla_prueba",
        valencia=0.0,
        impacto_emocional=0.6,
    )
    return [
        MemoryResult(
            recuerdo=EpisodicMemory(
                id="mem-01",
                metadata=episodic_metadata,
                narrativa="SECRET_EPISODIC_NARRATIVE",
            ),
            distancia=0.91,
            metadata_score=0.88,
        ),
        MemoryResult(
            recuerdo=SemanticMemory(
                id="mem-72",
                metadata=semantic_metadata,
                regla="SECRET_SEMANTIC_RULE",
            ),
            distancia=0.02,
            metadata_score=0.74,
        ),
    ]


def _isolated_orchestrator(writer: RecordingWriter | None, process):
    orchestrator = object.__new__(Orchestrator)
    if writer is not None:
        orchestrator.audit_writer = writer
    orchestrator._transacciones_lock_guard = threading.Lock()
    orchestrator._transacciones_locks = {}
    orchestrator._procesar_evento_serializado = (
        lambda evento, trace_id=None: process(evento)
    )
    return orchestrator


def test_event_start_and_end_are_correlated() -> None:
    writer = RecordingWriter()
    expected_result = object()
    orchestrator = _isolated_orchestrator(writer, lambda evento: expected_result)

    result = orchestrator.procesar_evento(_event())

    assert_true(result is expected_result, "La observabilidad no debe cambiar el resultado.")
    assert_true([trace.stage for trace in writer.traces] == ["event_start", "event_end"], "Debe emitir inicio y cierre.")
    start, end = writer.traces
    assert_true(start.trace_id == end.trace_id, "Inicio y cierre deben compartir trace_id.")
    assert_true(UUID(start.trace_id).version == 4, "El trace_id generado debe ser UUID4.")
    assert_true(
        start.id_transaccion == end.id_transaccion == "obs-003-transaction",
        "Ambos limites deben conservar id_transaccion.",
    )
    assert_true(start.input["origin"] == "terminal", "event_start debe registrar origen.")
    assert_true(start.input["event_type"] == "comando_usuario", "Debe registrar tipo.")
    assert_true(start.input["message_preview"] == "Hola, ¿sigues ahí?", "Debe registrar preview.")
    assert_true(end.input["origin"] is None, "event_end no debe adelantar metadata de otros tickets.")
    assert_true(start.pre["state"] == {} and end.post["state"] == {}, "OBS-003 no captura estado.")
    assert_true(start.triage["intent"] is None, "Triage pertenece a OBS-004.")
    assert_true(start.memory["retrieved_ids"] == [], "Memoria pertenece a OBS-005.")
    assert_true(start.decision["layer"] == "none", "Decision pertenece a OBS-006.")
    assert_true(start.durations_ms == {} and start.error is None, "Errores y tiempos pertenecen a OBS-007.")
    assert_true(orchestrator._transacciones_locks == {}, "El lock transaccional debe liberarse.")


def test_event_end_is_emitted_when_processing_raises() -> None:
    writer = RecordingWriter()

    def raise_processing_error(evento):
        raise RuntimeError("processing failed")

    orchestrator = _isolated_orchestrator(writer, raise_processing_error)

    try:
        orchestrator.procesar_evento(_event("obs-003-error"))
    except RuntimeError as exc:
        assert_true(str(exc) == "processing failed", "Debe conservar la excepcion original.")
    else:
        raise AssertionError("La prueba debe propagar la excepcion funcional original.")

    assert_true([trace.stage for trace in writer.traces] == ["event_start", "event_end"], "El finally debe cerrar la traza.")
    assert_true(writer.traces[0].trace_id == writer.traces[1].trace_id, "El error conserva correlacion.")
    assert_true(orchestrator._transacciones_locks == {}, "El error tambien debe liberar el lock.")


def test_writer_failure_does_not_change_event_processing() -> None:
    writer = RecordingWriter(result=False)
    expected_result = object()
    orchestrator = _isolated_orchestrator(writer, lambda evento: expected_result)

    result = orchestrator.procesar_evento(_event("obs-003-writer-failure"))

    assert_true(result is expected_result, "Un fallo observable del writer no debe cortar el evento.")
    assert_true(len(writer.traces) == 2, "Debe intentar tanto inicio como cierre.")


def test_legacy_isolated_orchestrator_without_writer_still_runs() -> None:
    expected_result = object()
    orchestrator = _isolated_orchestrator(None, lambda evento: expected_result)

    result = orchestrator.procesar_evento(_event("obs-003-legacy"))

    assert_true(result is expected_result, "Los fakes aislados previos deben conservar compatibilidad.")


def test_real_writer_persists_two_correlated_json_lines() -> None:
    with TemporaryDirectory(prefix="lazzaro-obs-003-") as temp_dir:
        path = Path(temp_dir) / "logs" / "audit.jsonl"
        writer = AuditTraceJSONLWriter(path)
        orchestrator = _isolated_orchestrator(writer, lambda evento: object())

        orchestrator.procesar_evento(_event("obs-003-real-writer"))

        payloads = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        assert_true(len(payloads) == 2, "El writer real debe persistir dos lineas.")
        assert_true(
            [payload["stage"] for payload in payloads] == ["event_start", "event_end"],
            "El JSONL debe conservar ambos limites en orden.",
        )
        assert_true(
            payloads[0]["trace_id"] == payloads[1]["trace_id"],
            "Las lineas JSONL deben quedar correlacionadas.",
        )


def test_cached_retry_still_gets_correlated_boundaries() -> None:
    transaction_id = "obs-003-cached"
    cached_action = AccionSaliente(
        id_accion="cached-action",
        nombre="hablar",
        parametros={"texto_a_comunicar": "cache"},
    )
    writer = RecordingWriter()
    orchestrator = object.__new__(Orchestrator)
    orchestrator.audit_writer = writer
    orchestrator._transacciones_procesadas = OrderedDict(
        [(transaction_id, cached_action)]
    )
    orchestrator._transacciones_lock_guard = threading.Lock()
    orchestrator._transacciones_locks = {}

    result = orchestrator.procesar_evento(_event(transaction_id))

    assert_true(result is cached_action, "El retry debe conservar la respuesta cacheada.")
    assert_true(
        [trace.stage for trace in writer.traces] == ["event_start", "event_end"],
        "El cache debe cerrar la traza sin fabricar un stage triage.",
    )
    assert_true(writer.traces[0].trace_id == writer.traces[1].trace_id, "El cache debe quedar correlacionado.")


def test_normal_triage_preserves_original_contract_and_trace() -> None:
    writer = RecordingWriter()
    orchestrator, route_calls = _orquestador_aislado(
        "persona",
        intent="SOCIAL_CONVERSATION",
    )
    orchestrator.audit_writer = writer
    triage_calls = _count_triage_calls(orchestrator)

    result = orchestrator.procesar_evento(
        _full_flow_event("unity", "obs-004-normal")
    )

    assert_true(result is not None and result.nombre == "hablar", "La decision funcional debe conservarse.")
    assert_true(route_calls["persona"] == 1, "La ruta Persona debe ejecutarse una vez.")
    assert_true(triage_calls["calls"] == 1, "Auditar no debe volver a ejecutar Triage.")
    assert_true(
        [trace.stage for trace in writer.traces]
        == ["event_start", "triage", "memory", "event_end"],
        "El flujo debe conservar los stages observables en orden.",
    )
    assert_true(
        len({trace.trace_id for trace in writer.traces}) == 1,
        "Todos los stages del turno deben reutilizar el mismo trace_id.",
    )

    triage = writer.traces[1].triage
    assert_true(triage["intent"] == "SOCIAL_CONVERSATION", "Debe conservar el intent original.")
    assert_true(triage["route"] == "persona", "Debe conservar la ruta original.")
    assert_true(
        triage["effective_route"] == "persona",
        "Sin adaptación, route y effective_route deben coincidir.",
    )
    assert_true(triage["confidence"] == 0.99, "Debe conservar confidence como número.")
    assert_true(triage["reason"] == "Decision de prueba.", "Debe conservar reason original.")

    memory = writer.traces[2].memory
    assert_true(memory["retrieved_ids"] == [], "Sin resultados no debe inventar IDs.")
    assert_true(memory["scores"] == [], "Sin resultados no debe inventar scores.")
    assert_true(memory["created_ids"] == [], "Un retorno None debe producir created_ids vacío.")
    assert_true(len(orchestrator.memory.experiencias) == 1, "La escritura original debe intentarse una vez.")


def test_terminal_triage_records_original_and_effective_routes() -> None:
    writer = RecordingWriter()
    orchestrator, route_calls = _orquestador_aislado(
        "motor",
        intent="MOTOR_SIMPLE",
    )
    orchestrator.audit_writer = writer
    triage_calls = _count_triage_calls(orchestrator)

    result = orchestrator.procesar_evento(
        _full_flow_event("terminal", "obs-004-terminal-motor")
    )

    assert_true(result is not None and result.nombre == "hablar", "Terminal debe conservar su salida funcional.")
    assert_true(route_calls["persona"] == 1, "La ruta efectiva Persona debe ejecutarse.")
    assert_true(route_calls["motor"] == 0, "La ruta física original no debe ejecutarse en terminal.")
    assert_true(triage_calls["calls"] == 1, "La telemetría no debe reevaluar Triage.")

    triage = writer.traces[1].triage
    assert_true(triage["intent"] == "MOTOR_SIMPLE", "Debe conservar el intent motor original.")
    assert_true(triage["route"] == "motor", "route debe ser la decisión original de Capa 0.")
    assert_true(
        triage["effective_route"] == "persona",
        "effective_route debe reflejar la reconducción operativa del terminal.",
    )
    assert_true(triage["confidence"] == 0.99, "Debe conservar confidence original.")
    assert_true(
        triage["reason"] == "Decision de prueba.",
        "Debe conservar reason original, no la razón modificada por el adaptador.",
    )


def test_behavior_core_failure_has_no_triage_stage() -> None:
    class FailingBehavior:
        @staticmethod
        def procesar_evento(tipo_evento: str, detalles: dict) -> dict:
            raise RuntimeError("simulated BehaviorCore failure")

    writer = RecordingWriter()
    orchestrator, _ = _orquestador_aislado("persona")
    orchestrator.audit_writer = writer
    orchestrator.behavior = FailingBehavior()
    triage_calls = _count_triage_calls(orchestrator)

    result = orchestrator.procesar_evento(
        _full_flow_event("terminal", "obs-004-behavior-failure")
    )

    assert_true(result is None, "Debe conservar el resultado funcional del fallo previo.")
    assert_true(triage_calls["calls"] == 0, "BehaviorCore falló antes de alcanzar Triage.")
    assert_true(
        [trace.stage for trace in writer.traces] == ["event_start", "event_end"],
        "Un fallo previo a Triage no debe fabricar el stage triage.",
    )


def test_triage_writer_failure_does_not_change_functional_route() -> None:
    writer = StageFailingWriter("triage")
    orchestrator, route_calls = _orquestador_aislado(
        "motor",
        intent="MOTOR_SIMPLE",
    )
    orchestrator.audit_writer = writer

    result = orchestrator.procesar_evento(
        _full_flow_event("terminal", "obs-004-writer-failure")
    )

    assert_true(result is not None and result.nombre == "hablar", "El writer no debe tumbar la decisión.")
    assert_true(route_calls["persona"] == 1, "Debe conservar la ruta efectiva Persona.")
    assert_true(route_calls["motor"] == 0, "No debe deshacer la adaptación terminal.")
    assert_true(
        writer.attempted_stages == ["event_start", "triage", "memory", "event_end"],
        "Debe intentar Triage y continuar hasta event_end tras el fallo.",
    )


def test_memory_context_metadata_uses_the_same_ranked_results() -> None:
    expected_results = _ranked_memory_results()
    calls = {"search": 0}

    class IdentitySummarizer:
        observed_results = None

        def resumir(self, recuerdos, max_caracteres: int) -> str:
            self.observed_results = recuerdos
            return "SECRET_SUMMARY_CONTEXT"

    manager = object.__new__(MemoryManager)
    summarizer = IdentitySummarizer()
    manager.summarizer = summarizer

    def buscar_recuerdos(**kwargs):
        calls["search"] += 1
        return expected_results

    manager.buscar_recuerdos = buscar_recuerdos

    result = manager.obtener_contexto_memoria_con_metadata(
        query="consulta única",
        limite=5,
        max_caracteres=600,
    )

    assert_true(calls["search"] == 1, "La observabilidad no debe repetir buscar_recuerdos.")
    assert_true(
        summarizer.observed_results is expected_results,
        "El resumidor y la metadata deben usar la misma lista de MemoryResult.",
    )
    assert_true(result.texto == "SECRET_SUMMARY_CONTEXT", "Debe conservar el contexto funcional.")
    assert_true(result.retrieved_ids == ("mem-01", "mem-72"), "Debe conservar el ranking de IDs.")
    assert_true(result.scores == (0.88, 0.74), "scores debe usar metadata_score en el mismo orden.")
    assert_true(
        result.scores != tuple(item.distancia for item in expected_results),
        "scores no debe esconder la distancia cruda de Chroma.",
    )


def test_memory_stage_observes_existing_operations_without_content() -> None:
    writer = RecordingWriter()
    orchestrator, route_calls = _orquestador_aislado(
        "persona",
        intent="MEMORY_QUERY",
    )
    orchestrator.audit_writer = writer
    results = _ranked_memory_results()
    calls = {"context": 0, "legacy_context": 0, "save": 0}
    functional_contexts: list[str] = []

    def contexto_con_metadata(**kwargs) -> MemoryContextResult:
        calls["context"] += 1
        return MemoryContextResult(
            texto="SECRET_SUMMARY_CONTEXT",
            retrieved_ids=tuple(item.recuerdo.id for item in results),
            scores=tuple(item.metadata_score for item in results),
        )

    def contexto_legacy(**kwargs) -> str:
        calls["legacy_context"] += 1
        return "LEGACY_CONTEXT_MUST_NOT_RUN"

    def guardar_experiencia(**kwargs) -> str:
        calls["save"] += 1
        orchestrator.memory.experiencias.append(kwargs)
        return "mem-new-91"

    working_memory = WorkingMemory(max_size=10)
    for index in range(4):
        working_memory.agregar(f"accion-previa-{index}", resultado="ok")

    orchestrator.memory.obtener_contexto_memoria_con_metadata = contexto_con_metadata
    orchestrator.memory.obtener_contexto_memoria = contexto_legacy
    orchestrator.memory.guardar_experiencia = guardar_experiencia
    orchestrator.memory.working_memory = working_memory
    orchestrator.memory.obtener_cantidad_memoria_trabajo = (
        lambda: MemoryManager.obtener_cantidad_memoria_trabajo(orchestrator.memory)
    )
    agregar_accion_original = orchestrator.memory.agregar_accion

    def agregar_accion_observable(**kwargs) -> None:
        agregar_accion_original(**kwargs)
        working_memory.agregar(
            accion=kwargs["accion"],
            resultado=kwargs.get("resultado", "pendiente"),
            detalles=kwargs.get("detalles"),
        )

    orchestrator.memory.agregar_accion = agregar_accion_observable
    resolver_persona_original = orchestrator._resolver_ruta_persona

    def resolver_persona_observable(**kwargs):
        functional_contexts.append(kwargs["memoria_texto"])
        return resolver_persona_original(**kwargs)

    orchestrator._resolver_ruta_persona = resolver_persona_observable

    result = orchestrator.procesar_evento(
        _full_flow_event("unity", "obs-005-memory")
    )

    assert_true(result is not None and result.nombre == "hablar", "La observabilidad no debe cambiar la acción.")
    assert_true(route_calls["persona"] == 1, "Debe conservar la decisión funcional Persona.")
    assert_true(calls == {"context": 1, "legacy_context": 0, "save": 1}, "Debe reutilizar una lectura y una escritura.")
    assert_true(
        functional_contexts and "SECRET_SUMMARY_CONTEXT" in functional_contexts[0],
        "El mismo resumen observado debe alimentar la decisión funcional.",
    )
    assert_true(
        [trace.stage for trace in writer.traces]
        == ["event_start", "triage", "memory", "event_end"],
        "El turno debe emitir memory entre triage y event_end.",
    )
    assert_true(
        len({trace.trace_id for trace in writer.traces}) == 1,
        "memory debe reutilizar el trace_id del turno.",
    )

    memory_trace = writer.traces[2]
    assert_true(
        memory_trace.memory
        == {
            "retrieved_ids": ["mem-01", "mem-72"],
            "scores": [0.88, 0.74],
            "created_ids": ["mem-new-91"],
            "working_memory_count": 4,
        },
        "Debe emitir exactamente los cuatro campos v1 con metadata alineada.",
    )
    serialized = memory_trace.to_json()
    for forbidden in (
        "SECRET_EPISODIC_NARRATIVE",
        "SECRET_SEMANTIC_RULE",
        "SECRET_SUMMARY_CONTEXT",
        '"types"',
    ):
        assert_true(forbidden not in serialized, "AuditTrace no debe convertirse en almacén de recuerdos.")
    assert_true(
        len(working_memory.obtener_todas()) == 5,
        "El snapshot debe contar cuatro elementos antes de agregar la acción actual.",
    )


def test_memory_writer_failure_does_not_change_functional_decision() -> None:
    writer = StageFailingWriter("memory")
    orchestrator, route_calls = _orquestador_aislado("persona")
    orchestrator.audit_writer = writer

    result = orchestrator.procesar_evento(
        _full_flow_event("unity", "obs-005-writer-failure")
    )

    assert_true(result is not None and result.nombre == "hablar", "El writer no debe tumbar la decisión.")
    assert_true(route_calls["persona"] == 1, "La ruta funcional debe ejecutarse normalmente.")
    assert_true(len(orchestrator.memory.experiencias) == 1, "La escritura funcional debe ocurrir una vez.")
    assert_true(
        writer.attempted_stages == ["event_start", "triage", "memory", "event_end"],
        "El fallo de memory debe continuar hasta event_end.",
    )


def main() -> None:
    tests = [
        test_event_start_and_end_are_correlated,
        test_event_end_is_emitted_when_processing_raises,
        test_writer_failure_does_not_change_event_processing,
        test_legacy_isolated_orchestrator_without_writer_still_runs,
        test_real_writer_persists_two_correlated_json_lines,
        test_cached_retry_still_gets_correlated_boundaries,
        test_normal_triage_preserves_original_contract_and_trace,
        test_terminal_triage_records_original_and_effective_routes,
        test_behavior_core_failure_has_no_triage_stage,
        test_triage_writer_failure_does_not_change_functional_route,
        test_memory_context_metadata_uses_the_same_ranked_results,
        test_memory_stage_observes_existing_operations_without_content,
        test_memory_writer_failure_does_not_change_functional_decision,
    ]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    print("[OK] OBS-003/005 Orchestrator boundaries, Triage y memoria.")


if __name__ == "__main__":
    main()
