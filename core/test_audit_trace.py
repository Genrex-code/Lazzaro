"""Pruebas ejecutables del contrato AuditTrace v1 (OBS-001)."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from core.audit_trace import AUDIT_REDACTED, AuditTrace, serialize_audit_trace


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_partial_mapping_produces_stable_json() -> None:
    raw = {
        "id_transaccion": "tx-obs-001",
        "stage": "triage",
        "triage": {"intent": "SOCIAL", "confidence": 0.91},
        "unknown_top_level": "must-not-expand-v1",
    }

    payload = json.loads(serialize_audit_trace(raw))

    expected_top_level = {
        "trace_version",
        "trace_id",
        "id_transaccion",
        "timestamp",
        "stage",
        "build",
        "llm",
        "input",
        "pre",
        "triage",
        "memory",
        "decision",
        "execution",
        "post",
        "durations_ms",
        "error",
    }
    assert_true(set(payload) == expected_top_level, "La forma superior de v1 debe ser estable.")
    assert_true(payload["trace_version"] == 1, "La version debe ser 1.")
    assert_true(UUID(payload["trace_id"]).version == 4, "trace_id debe ser UUID4 por defecto.")
    assert_true(payload["id_transaccion"] == "tx-obs-001", "Debe conservar correlacion.")
    assert_true(payload["triage"]["intent"] == "SOCIAL", "Debe conservar datos presentes.")
    assert_true(payload["triage"]["route"] is None, "Debe completar campos ausentes.")
    assert_true(payload["memory"]["retrieved_ids"] == [], "Debe crear listas independientes.")
    assert_true("unknown_top_level" not in payload, "v1 no debe crecer con campos accidentales.")


def test_missing_fields_and_defaults_are_independent() -> None:
    first = AuditTrace.from_mapping({})
    second = AuditTrace.from_mapping(None)

    first.memory["retrieved_ids"].append("memory-1")
    first.pre["state"]["energia"] = 12

    assert_true(second.memory["retrieved_ids"] == [], "Los defaults no deben compartirse.")
    assert_true(second.pre["state"] == {}, "Los dicts anidados no deben compartirse.")
    assert_true(first.id_transaccion is None, "id_transaccion ausente debe tolerarse.")
    assert_true(first.stage == "event_start", "stage ausente debe usar event_start.")
    assert_true(
        json.loads(serialize_audit_trace(second))["trace_version"] == 1,
        "El serializador debe aceptar tambien la instancia dataclass.",
    )


def test_safe_serialization_redacts_secrets() -> None:
    fake_gemini = "AIza" + ("A" * 35)
    fake_bearer = "Bearer " + ("B" * 30)
    raw = {
        "stage": "decision",
        "input": {"message_preview": f"api_key={fake_gemini} continuar"},
        "decision": {
            "action": "hablar",
            "api_key": fake_gemini,
            "authorization": fake_bearer,
            "database_password_value": "not-for-audit",
            "max_tokens": 512,
        },
        "error": {"client_secret": "not-for-audit", "type": "RemoteError"},
    }

    encoded = serialize_audit_trace(raw)
    payload = json.loads(encoded)

    assert_true(fake_gemini not in encoded, "Una API key no debe aparecer en JSON.")
    assert_true(fake_bearer not in encoded, "Un bearer token no debe aparecer en JSON.")
    assert_true(payload["decision"]["api_key"] == AUDIT_REDACTED, "Debe redactar por clave.")
    assert_true(
        payload["decision"]["authorization"] == AUDIT_REDACTED,
        "Debe redactar authorization.",
    )
    assert_true(
        payload["decision"]["database_password_value"] == AUDIT_REDACTED,
        "Debe redactar nombres sensibles compuestos.",
    )
    assert_true(payload["decision"]["max_tokens"] == 512, "max_tokens no es un secreto.")
    assert_true(AUDIT_REDACTED in payload["input"]["message_preview"], "Debe sanear previews.")
    assert_true(payload["error"]["client_secret"] == AUDIT_REDACTED, "Debe sanear errores.")


def test_non_json_values_are_normalized_without_mutating_input() -> None:
    created_ids = {"b", "a"}
    raw = {
        "timestamp": datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc),
        "stage": "memory",
        "memory": {"created_ids": created_ids, "score": math.nan},
        "build": {"workspace": Path("workspace")},
        "error": RuntimeError("private failure detail"),
    }

    payload = json.loads(serialize_audit_trace(raw))

    assert_true(payload["timestamp"] == "2026-08-29T12:00:00.000Z", "UTC debe ser estable.")
    assert_true(payload["memory"]["created_ids"] == ["a", "b"], "Sets deben ser JSON estable.")
    assert_true(payload["memory"]["score"] is None, "NaN debe normalizarse a null.")
    assert_true(payload["build"]["workspace"] == "workspace", "Path debe serializarse.")
    assert_true(payload["error"] == {"type": "RuntimeError"}, "No debe filtrar mensajes de error.")
    assert_true(created_ids == {"a", "b"}, "La serializacion no debe mutar la entrada.")


def test_invalid_stage_is_rejected() -> None:
    try:
        AuditTrace(stage="unknown")
    except ValueError:
        return
    raise AssertionError("Un stage fuera del contrato debe rechazarse.")


def main() -> None:
    tests = [
        test_partial_mapping_produces_stable_json,
        test_missing_fields_and_defaults_are_independent,
        test_safe_serialization_redacts_secrets,
        test_non_json_values_are_normalized_without_mutating_input,
        test_invalid_stage_is_rejected,
    ]
    for test in tests:
        test()
        print(f"[OK] {test.__name__}")
    print("[OK] OBS-001 AuditTrace v1: contrato y serializacion segura.")


if __name__ == "__main__":
    main()
