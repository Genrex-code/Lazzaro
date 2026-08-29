"""Pruebas ejecutables del writer JSONL de AuditTrace (OBS-002)."""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from core.audit_trace import AuditTrace
from core.audit_writer import AuditTraceJSONLWriter


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingLogger:
    def __init__(self) -> None:
        self.warnings: list[tuple[tuple, dict]] = []

    def warning(self, *args, **kwargs) -> None:
        self.warnings.append((args, kwargs))


def test_utf8_append_only_and_reused_trace_id() -> None:
    with TemporaryDirectory(prefix="lazzaro-audit-writer-") as temp_dir:
        path = Path(temp_dir) / "logs" / "audit.jsonl"
        path.parent.mkdir(parents=True)
        original = '{"existing":true}\n'
        path.write_text(original, encoding="utf-8", newline="\n")
        writer = AuditTraceJSONLWriter(path)

        trace_id = "d9cd4812-c00b-4a56-9de9-1b76857733af"
        first = AuditTrace(
            trace_id=trace_id,
            id_transaccion="tx-reuse",
            stage="event_start",
            input={"message_preview": "¿Qué recibió Lázaro?"},
        )
        second = AuditTrace(
            trace_id=trace_id,
            id_transaccion="tx-reuse",
            stage="triage",
            triage={"intent": "SOCIAL", "route": "persona"},
        )

        result_first = writer.write(first)
        result_second = writer.write(second)
        raw = path.read_bytes()
        lines = raw.decode("utf-8").splitlines()

        assert_true(bool(result_first) and result_first.success, "El primer append debe pasar.")
        assert_true(bool(result_second) and result_second.success, "El segundo append debe pasar.")
        assert_true(lines[0] == original.strip(), "El contenido previo no debe reescribirse.")
        assert_true(len(lines) == 3, "Debe existir exactamente un JSON por linea.")
        payloads = [json.loads(line) for line in lines[1:]]
        assert_true(payloads[0]["input"]["message_preview"] == "¿Qué recibió Lázaro?", "Debe ser UTF-8.")
        assert_true(
            {payload["trace_id"] for payload in payloads} == {trace_id},
            "El mismo trace_id debe reutilizarse entre stages.",
        )
        assert_true(
            [payload["stage"] for payload in payloads] == ["event_start", "triage"],
            "Cada stage debe conservar su propia linea.",
        )


def test_thread_safe_writes_keep_complete_json_lines() -> None:
    with TemporaryDirectory(prefix="lazzaro-audit-threads-") as temp_dir:
        path = Path(temp_dir) / "nested" / "logs" / "audit.jsonl"
        writer = AuditTraceJSONLWriter(path)
        total = 240

        def append(index: int):
            return writer.write(
                AuditTrace(
                    id_transaccion=f"concurrent-{index}",
                    stage="decision",
                    decision={"action": "hablar", "sequence": index},
                )
            )

        with ThreadPoolExecutor(max_workers=16) as executor:
            results = list(executor.map(append, range(total)))

        lines = path.read_text(encoding="utf-8").splitlines()
        payloads = [json.loads(line) for line in lines]

        assert_true(all(result.success for result in results), "Todos los threads deben escribir.")
        assert_true(len(lines) == total, "No deben perderse ni mezclarse lineas.")
        assert_true(
            {payload["id_transaccion"] for payload in payloads}
            == {f"concurrent-{index}" for index in range(total)},
            "Cada transaccion debe aparecer exactamente una vez.",
        )
        assert_true(path.parent.is_dir(), "El writer debe crear el directorio padre.")


def test_failure_warns_and_returns_false_without_raising() -> None:
    with TemporaryDirectory(prefix="lazzaro-audit-failure-") as temp_dir:
        blocked_parent = Path(temp_dir) / "not-a-directory"
        blocked_parent.write_text("block", encoding="utf-8")
        logger = RecordingLogger()
        writer = AuditTraceJSONLWriter(blocked_parent / "audit.jsonl", human_logger=logger)

        result = writer.write(AuditTrace(stage="error"))

        assert_true(not result and not result.success, "El fallo debe ser observable.")
        assert_true(result.error_type == "FileExistsError", "Debe informar solo el tipo estable.")
        assert_true(len(logger.warnings) == 1, "Debe emitir un warning humano.")


def test_logger_recursion_is_stopped() -> None:
    class RecursiveLogger(RecordingLogger):
        writer: AuditTraceJSONLWriter
        trace: AuditTrace

        def __init__(self) -> None:
            super().__init__()
            self.reentrant_result = None

        def warning(self, *args, **kwargs) -> None:
            super().warning(*args, **kwargs)
            self.reentrant_result = self.writer.write(self.trace)

    with TemporaryDirectory(prefix="lazzaro-audit-recursion-") as temp_dir:
        blocked_parent = Path(temp_dir) / "not-a-directory"
        blocked_parent.write_text("block", encoding="utf-8")
        logger = RecursiveLogger()
        writer = AuditTraceJSONLWriter(blocked_parent / "audit.jsonl", human_logger=logger)
        logger.writer = writer
        logger.trace = AuditTrace(stage="error")

        outer_result = writer.write(logger.trace)

        assert_true(not outer_result.success, "El fallo externo debe devolverse.")
        assert_true(len(logger.warnings) == 1, "El warning no debe repetirse recursivamente.")
        assert_true(logger.reentrant_result is not None, "Debe haberse intentado la reentrada.")
        assert_true(
            logger.reentrant_result.error_type == "ReentrantAuditWrite",
            "La guardia debe identificar la reentrada sin lanzar.",
        )


def test_writer_delegates_serialization_to_audit_trace() -> None:
    with TemporaryDirectory(prefix="lazzaro-audit-delegate-") as temp_dir:
        trace = AuditTrace(stage="event_end")
        writer = AuditTraceJSONLWriter(Path(temp_dir) / "audit.jsonl")
        original_to_json = AuditTrace.to_json
        calls: list[AuditTrace] = []

        def recording_to_json(instance: AuditTrace) -> str:
            calls.append(instance)
            return original_to_json(instance)

        with patch.object(AuditTrace, "to_json", recording_to_json):
            result = writer.write(trace)

        assert_true(result.success, "La escritura delegada debe pasar.")
        assert_true(calls == [trace], "El writer debe llamar AuditTrace.to_json() una sola vez.")


def test_partial_write_retries_remaining_bytes() -> None:
    with TemporaryDirectory(prefix="lazzaro-audit-partial-") as temp_dir:
        path = Path(temp_dir) / "audit.jsonl"
        trace = AuditTrace(
            id_transaccion="partial-write",
            stage="event_end",
            post={"state": {"mensaje": "línea UTF-8 completa"}},
        )
        expected_line = trace.to_json()
        writer = AuditTraceJSONLWriter(path)
        real_os_write = os.write
        write_sizes: list[int] = []

        def partial_write(file_descriptor: int, data: bytes) -> int:
            chunk_size = min(17, len(data))
            written = real_os_write(file_descriptor, data[:chunk_size])
            write_sizes.append(written)
            return written

        with patch("core.audit_writer.os.write", side_effect=partial_write):
            result = writer.write(trace)

        assert_true(result.success, "El writer debe completar un append parcial.")
        assert_true(len(write_sizes) > 1, "La simulacion debe requerir varios os.write.")
        assert_true(path.read_text(encoding="utf-8") == expected_line + "\n", "No debe perder bytes.")


def test_zero_byte_write_is_reported_as_os_error() -> None:
    with TemporaryDirectory(prefix="lazzaro-audit-zero-") as temp_dir:
        logger = RecordingLogger()
        writer = AuditTraceJSONLWriter(
            Path(temp_dir) / "audit.jsonl",
            human_logger=logger,
        )

        with patch("core.audit_writer.os.write", return_value=0):
            result = writer.write(AuditTrace(stage="error"))

        assert_true(not result.success, "Cero bytes debe producir fallo observable.")
        assert_true(result.error_type == "OSError", "El retorno cero debe tratarse como OSError.")
        assert_true(len(logger.warnings) == 1, "El fallo debe avisarse por el logger humano.")


def main() -> None:
    tests = [
        test_utf8_append_only_and_reused_trace_id,
        test_thread_safe_writes_keep_complete_json_lines,
        test_failure_warns_and_returns_false_without_raising,
        test_logger_recursion_is_stopped,
        test_writer_delegates_serialization_to_audit_trace,
        test_partial_write_retries_remaining_bytes,
        test_zero_byte_write_is_reported_as_os_error,
    ]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    print("[OK] OBS-002 AuditTrace JSONL writer.")


if __name__ == "__main__":
    main()
