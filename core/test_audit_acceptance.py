"""Gate final del sidequest: reconstruir un turno leyendo solo su JSONL."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_engine.fallback_ai_client import FallbackAIClient
from api.schemas import EventoEntrante
from core.audit_trace import AuditTrace
from core.audit_writer import AuditTraceJSONLWriter
from core.behavior_core import BehaviorCore
from core.orchestrator import Orchestrator
from core.test_orchestrator_audit import (
    AlwaysRaisingWriter,
    RecordingHumanLogger,
    RecordingWriter,
    _ObservableFakeClient,
    _attach_audited_persona_call,
    _count_triage_calls,
    _event,
    _isolated_orchestrator,
    _ranked_memory_results,
    assert_true,
)
from memory.memory_manager import MemoryManager
from memory.working_memory import WorkingMemory
from terminal_client.test_chat import _orquestador_aislado


def _answers_from_jsonl(path: Path, transaction_id: str) -> dict:
    """Solo recibe el JSONL y el ID: no consulta Core, Chroma ni logger humano."""
    records = [
        row for line in path.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line))["id_transaccion"] == transaction_id
    ]
    assert_true(len({row["trace_id"] for row in records}) == 1, "El fixture debe tener un solo turno, sin mezclar retries.")
    stages = {row["stage"]: row for row in records if row["stage"] != "error"}
    assert_true(len(stages) == sum(row["stage"] != "error" for row in records), "No debe ocultar stages duplicados al reconstruir el turno.")
    assert_true(
        set(stages) == {"event_start", "triage", "memory", "decision", "execution", "event_end"},
        "La aceptación requiere todos los stages reales, no sus defaults en otra línea.",
    )
    return {
        "received": stages["event_start"]["input"],
        "before": stages["event_start"]["pre"],
        "triage": stages["triage"]["triage"],
        "memory": stages["memory"]["memory"],
        "layer": stages["decision"]["decision"]["layer"],
        "action": stages["decision"]["decision"]["action"],
        "execution": stages["execution"]["execution"],
        "incidents": {
            "fallback_used": stages["decision"]["llm"]["fallback_used"],
            "errors": [row["error"] for row in records if row["stage"] == "error"],
        },
        "after": stages["event_end"]["post"],
    }


def test_transaction_is_reconstructed_from_jsonl_only() -> None:
    with TemporaryDirectory(prefix="lazzaro-audit-acceptance-") as temp_dir:
        path = Path(temp_dir) / "logs" / "audit.jsonl"
        writer = AuditTraceJSONLWriter(path)
        # Un ID ajeno evita que una lectura sin filtro pase accidentalmente.
        assert_true(writer.write(AuditTrace(id_transaccion="other-turn")), "Debe persistir el distractor.")
        orchestrator, route_calls = _orquestador_aislado("motor", intent="MOTOR_SIMPLE")
        orchestrator.audit_writer = writer
        core = BehaviorCore()
        core.state_engine._clock = lambda: 100.0
        core.state_engine._ultimo_tick = 100.0
        core.iniciar_tarea("Objetivo anterior")
        core.task_context.intentos_totales = 2
        orchestrator.behavior = core
        triage_calls = _count_triage_calls(orchestrator)
        calls = {"search": 0, "summary": 0, "save": 0, "execution": 0, "snapshot": 0}

        snapshot_original = core.obtener_snapshot_audit

        def snapshot_observable():
            calls["snapshot"] += 1
            return snapshot_original()

        core.obtener_snapshot_audit = snapshot_observable
        results = _ranked_memory_results()
        manager = object.__new__(MemoryManager)

        def search(**kwargs):
            calls["search"] += 1
            return results

        class Summary:
            def resumir(self, recuerdos, max_caracteres):
                calls["summary"] += 1
                assert_true(recuerdos is results, "La metadata y el contexto deben usar los mismos resultados.")
                return "SECRET_ACCEPTANCE_MEMORY_CONTEXT"

        manager.buscar_recuerdos = search
        manager.summarizer = Summary()
        orchestrator.memory.obtener_contexto_memoria_con_metadata = manager.obtener_contexto_memoria_con_metadata

        def save(**kwargs):
            calls["save"] += 1
            orchestrator.memory.experiencias.append(kwargs)
            return "mem-new-acceptance"

        orchestrator.memory.guardar_experiencia = save
        working_memory = WorkingMemory(max_size=10)
        working_memory.agregar("accion-previa", resultado="ok")
        orchestrator.memory.obtener_cantidad_memoria_trabajo = lambda: len(working_memory.obtener_todas())
        primary = _ObservableFakeClient("gemini", "test-primary", TimeoutError("SECRET_TIMEOUT"))
        secondary = _ObservableFakeClient("gemini", "test-fallback", "Desde el terminal solo puedo conversar.")
        _attach_audited_persona_call(orchestrator, route_calls, FallbackAIClient([primary, secondary]))
        execute_original = orchestrator._gestionar_ejecucion

        def execute_once(*args, **kwargs):
            calls["execution"] += 1
            return execute_original(*args, **kwargs)

        orchestrator._gestionar_ejecucion = execute_once
        identity_original = orchestrator._registrar_estado_identidad

        def finish_state(*args, **kwargs):
            identity_original(*args, **kwargs)
            # Mutación funcional tardía: post no puede reutilizar respuesta_core.
            core.state_engine.establecer_estado_biologico(energia=61, estres=12, foco=77)
            core.task_context.intentos_totales = 1

        orchestrator._registrar_estado_identidad = finish_state
        transaction_id = "sidequest-acceptance-001"
        message = "Ve al cubo y recuerda lo que hablamos."
        result = orchestrator.procesar_evento(EventoEntrante(
            origen="terminal", tipo_evento="comando_usuario", id_transaccion=transaction_id,
            detalles={"mensaje": message, "solicita_decision": True},
        ))
        assert_true(result is not None and result.nombre == "hablar", "El turno debe conservar la salida funcional.")
        assert_true(calls == {"search": 1, "summary": 1, "save": 1, "execution": 1, "snapshot": 2}, "La auditoría no debe repetir trabajo funcional.")
        assert_true(triage_calls["calls"] == route_calls["persona"] == primary.calls == secondary.calls == 1, "Cada operación funcional debe ocurrir una vez.")
        assert_true(route_calls["motor"] == 0, "Terminal no debe materializar la intención motora.")

        # Las nueve respuestas siguientes proceden exclusivamente del archivo.
        answers = _answers_from_jsonl(path, transaction_id)
        assert_true(answers["received"] == {"origin": "terminal", "event_type": "comando_usuario", "message_preview": message}, "Debe saber qué recibió.")
        assert_true(answers["before"]["state"]["energia"] == 80, "Debe conservar el estado anterior al evento.")
        assert_true(answers["before"]["task"]["objetivo_id"] == "Objetivo anterior" and answers["before"]["task"]["intentos_totales"] == 2, "Debe conservar la tarea anterior, no la creada por el evento.")
        assert_true(answers["triage"] == {"intent": "MOTOR_SIMPLE", "route": "motor", "effective_route": "persona", "confidence": 0.99, "reason": "Decision de prueba."}, "Debe separar Triage original y ruta efectiva.")
        assert_true(answers["memory"] == {"retrieved_ids": ["mem-01", "mem-72"], "scores": [0.88, 0.74], "created_ids": ["mem-new-acceptance"], "working_memory_count": 1}, "Debe identificar memoria y ranking sin contenido.")
        assert_true(answers["layer"] == "persona", "Debe identificar la capa que procesó.")
        assert_true(answers["action"] == "hablar", "Debe identificar la acción final.")
        assert_true(answers["execution"] == {"status": "pending", "error_type": None}, "Dispatch externo no demuestra ejecución: debe conservar pending.")
        assert_true(answers["incidents"] == {"fallback_used": True, "errors": [{"type": "TimeoutError", "component": "llm"}]}, "Debe identificar fallback, error y timeout en el mismo turno.")
        assert_true(answers["after"]["state"]["energia"] == 61 and answers["after"]["state"]["foco"] == 77, "Debe capturar la mutación tardía del estado final.")
        assert_true(answers["after"]["task"]["objetivo_id"] == message and answers["after"]["task"]["intentos_totales"] == 1, "Debe observar también la tarea final.")
        raw = path.read_text(encoding="utf-8")
        for forbidden in ("SECRET_ACCEPTANCE_MEMORY_CONTEXT", "SECRET_EPISODIC_NARRATIVE", "SECRET_SEMANTIC_RULE", "SECRET_TIMEOUT", "SECRET_PROMPT", "SECRET_PRIVATE"):
            assert_true(forbidden not in raw, "El gate no autoriza volcar recuerdos, prompts ni errores libres.")
        print("ACCEPTANCE_FROM_JSONL=" + json.dumps({"id_transaccion": transaction_id, **answers}, ensure_ascii=False, sort_keys=True))


def test_snapshot_reads_no_narrative_or_task_detectors() -> None:
    core = BehaviorCore()
    core.iniciar_tarea("objetivo")
    task = core.task_context
    task.historial.append({"accion": "SECRET_TASK_HISTORY"})
    with patch.object(core, "obtener_resumen", side_effect=AssertionError("no resumen general")), \
         patch.object(task, "obtener_resumen", side_effect=AssertionError("no resumen de tarea")), \
         patch.object(task, "detectar_loop", side_effect=AssertionError("no detector")), \
         patch.object(task, "detectar_estancamiento", side_effect=AssertionError("no detector")), \
         patch.object(task, "detectar_compulsion", side_effect=AssertionError("no detector")), \
         patch.object(core.state_engine, "tick", side_effect=AssertionError("no mutar estado")):
        snapshot = core.obtener_snapshot_audit()
    assert_true(snapshot["state"]["energia"] == 80, "La lectura no debe aplicar eventos/tick.")
    assert_true(set(snapshot["task"]) == {"task_instance_id", "objetivo_id", "intentos_totales", "intentos_fallidos", "frustracion_local", "umbral_abandono"}, "La tarea debe ser acotada e identificable, sin historial ni detectores recalculados.")
    assert_true("SECRET_TASK_HISTORY" not in json.dumps(snapshot), "No debe copiar el microhistorial.")
    core.task_context = None
    assert_true(core.obtener_snapshot_audit()["task"] == {}, "Una lectura válida sin tarea debe representarse como objeto vacío.")
    now = [100.0]
    core.state_engine._clock = lambda: now[0]
    core.state_engine._ultimo_tick = now[0]
    core.state_engine.iniciar_descanso()
    now[0] += 10
    raw_before = (core.state_engine.energia, core.state_engine._ultimo_tick, core.state_engine.descansando)
    projected = core.obtener_snapshot_audit()
    assert_true(projected["state"]["energia"] > raw_before[0], "Debe observar la proyección pública del descanso.")
    assert_true((core.state_engine.energia, core.state_engine._ultimo_tick, core.state_engine.descansando) == raw_before, "Observar no debe aplicar ni finalizar descanso.")


def test_task_snapshot_preview_is_bounded_and_sanitized() -> None:
    writer = RecordingWriter()
    orchestrator = _isolated_orchestrator(writer, lambda evento: None)
    core = BehaviorCore()
    original = "api_key=not-a-real-key " + "x" * 500
    core.iniciar_tarea(original)
    orchestrator.behavior = core
    orchestrator.procesar_evento(_event("task-preview"))
    for trace, section in ((writer.traces[0], "pre"), (writer.traces[-1], "post")):
        objective = json.loads(trace.to_json())[section]["task"]["objetivo_id"]
        assert_true(len(objective) <= 240 and "[REDACTED]" in objective, "El objetivo debe usar el preview seguro central, no volcar un prompt.")
        assert_true("not-a-real-key" not in objective, "No debe exponer secretos a través de la tarea.")
    assert_true(core.task_context.objetivo_id == original, "La auditoría no debe modificar la tarea funcional.")


def test_boundary_snapshots_are_detached_on_success_and_error() -> None:
    for raises in (False, True):
        data = {"state": {"energia": 80, "nested": {"x": 1}}, "task": {"objetivo_id": "antes"}}
        sentinel = RuntimeError("functional sentinel")
        expected_result = object()
        writer = RecordingWriter()

        def process(evento):
            data["state"]["energia"] = 45
            data["state"]["nested"]["x"] = 2
            data["task"]["objetivo_id"] = "despues"
            if raises:
                raise sentinel
            return expected_result

        orchestrator = _isolated_orchestrator(writer, process)
        class SnapshotOwner:
            def obtener_snapshot_audit(self):
                return data
        orchestrator.behavior = SnapshotOwner()
        try:
            result = orchestrator.procesar_evento(_event("snapshot-copy"))
        except RuntimeError as exc:
            assert_true(raises and exc is sentinel, "La excepción original debe conservarse.")
        else:
            assert_true(not raises and result is expected_result, "La respuesta original debe conservarse.")
        start, end = writer.traces[0], writer.traces[-1]
        assert_true(start.pre["state"] == {"energia": 80, "nested": {"x": 1}}, "El snapshot previo debe ser una copia profunda.")
        assert_true(start.pre["task"]["objetivo_id"] == "antes", "La tarea previa no puede reescribirse.")
        assert_true(end.post == data, "El cierre debe observar también cambios previos a una excepción.")
        assert_true(start.trace_id == end.trace_id and orchestrator._transacciones_locks == {}, "Debe conservar correlación y liberar locks.")


def test_snapshot_failure_is_non_blocking_and_explicitly_unknown() -> None:
    orchestrator, calls = _orquestador_aislado("persona")
    writer = RecordingWriter()
    orchestrator.audit_writer = writer
    def broken_snapshot():
        raise ValueError("SECRET_SNAPSHOT_ERROR")
    orchestrator.behavior.obtener_snapshot_audit = broken_snapshot
    with patch("core.orchestrator.logger.warning") as warning:
        result = orchestrator.procesar_evento(_event("snapshot-failure"))
    assert_true(result is not None and calls["persona"] == 1, "El snapshot fallido no debe interrumpir ni repetir la decisión.")
    assert_true(warning.call_count == 2 and all(call.args[-1] == "ValueError" for call in warning.call_args_list), "Cada límite debe advertir solo el tipo estable.")
    assert_true(writer.traces[0].pre["state"] is None and writer.traces[-1].post["task"] is None, "Una captura fallida es desconocida, no un estado inventado.")
    assert_true("error" not in [trace.stage for trace in writer.traces], "Un fallo de snapshot no debe generar recursión de auditoría.")
    with patch("core.orchestrator.logger.warning", side_effect=OSError("broken logger")):
        result = orchestrator.procesar_evento(_event("snapshot-and-logger-failure"))
    assert_true(result is not None, "Incluso el warning fallido debe ser no bloqueante.")


def test_snapshot_capture_time_is_excluded_from_total() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.behavior = BehaviorCore()
    orchestrator._begin_audit_timing()
    try:
        with patch("core.orchestrator.time.perf_counter_ns", side_effect=[100_000_000, 600_000_000, 1_000_000_000]):
            orchestrator._capturar_estado_tarea_audit()
            total = orchestrator._elapsed_total_ms(0)
    finally:
        orchestrator._end_audit_timing()
    assert_true(total == 500, "Debe descontar los 500ms de captura de auditoría.")


def test_real_snapshots_preserve_response_during_writer_outage() -> None:
    with TemporaryDirectory(prefix="lazzaro-snapshot-outage-") as temp_dir:
        parent = Path(temp_dir) / "not-a-directory"
        parent.write_text("blocked", encoding="utf-8")
        human_logger = RecordingHumanLogger()
        writers = [AuditTraceJSONLWriter(parent / "audit.jsonl", human_logger=human_logger), AlwaysRaisingWriter()]
        for writer in writers:
            expected_result = object()
            orchestrator = _isolated_orchestrator(writer, lambda evento: expected_result)
            orchestrator.behavior = BehaviorCore()
            with patch("core.orchestrator.logger.warning"):
                result = orchestrator.procesar_evento(_event("snapshot-outage"))
            assert_true(result is expected_result and orchestrator._transacciones_locks == {}, "Snapshots reales y writer caído deben conservar resultado y locks.")
        assert_true(len(human_logger.warnings) == 2, "El writer físico debe advertir por ambos límites.")


def main() -> None:
    tests = [test_transaction_is_reconstructed_from_jsonl_only, test_snapshot_reads_no_narrative_or_task_detectors,
             test_task_snapshot_preview_is_bounded_and_sanitized,
             test_boundary_snapshots_are_detached_on_success_and_error, test_snapshot_failure_is_non_blocking_and_explicitly_unknown,
             test_snapshot_capture_time_is_excluded_from_total, test_real_snapshots_preserve_response_during_writer_outage]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    print("[OK] Gate de aceptación: nueve respuestas desde un único JSONL e id_transaccion.")


if __name__ == "__main__":
    main()
