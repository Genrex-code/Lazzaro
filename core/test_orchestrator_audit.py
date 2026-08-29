"""Pruebas de integración acotada event_start/event_end (OBS-003)."""

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


def _event(transaction_id: str = "obs-003-transaction") -> EventoEntrante:
    return EventoEntrante(
        origen="terminal",
        tipo_evento="comando_usuario",
        id_transaccion=transaction_id,
        detalles={"mensaje": "Hola, ¿sigues ahí?"},
    )


def _isolated_orchestrator(writer: RecordingWriter | None, process):
    orchestrator = object.__new__(Orchestrator)
    if writer is not None:
        orchestrator.audit_writer = writer
    orchestrator._transacciones_lock_guard = threading.Lock()
    orchestrator._transacciones_locks = {}
    orchestrator._procesar_evento_serializado = process
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
    assert_true([trace.stage for trace in writer.traces] == ["event_start", "event_end"], "El cache tambien debe cerrar la traza.")
    assert_true(writer.traces[0].trace_id == writer.traces[1].trace_id, "El cache debe quedar correlacionado.")


def main() -> None:
    tests = [
        test_event_start_and_end_are_correlated,
        test_event_end_is_emitted_when_processing_raises,
        test_writer_failure_does_not_change_event_processing,
        test_legacy_isolated_orchestrator_without_writer_still_runs,
        test_real_writer_persists_two_correlated_json_lines,
        test_cached_retry_still_gets_correlated_boundaries,
    ]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    print("[OK] OBS-003 Orchestrator event_start/event_end.")


if __name__ == "__main__":
    main()
