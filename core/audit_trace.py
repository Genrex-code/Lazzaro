"""Contrato estructurado y serializacion segura para AuditTrace v1.

Este modulo define datos; la persistencia JSONL pertenece a OBS-002. Mantener
esa separacion permite probar el contrato sin acoplarlo al logger operativo.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4


AUDIT_TRACE_VERSION = 1
AUDIT_REDACTED = "[REDACTED]"
AUDIT_MAX_STRING_LENGTH = 4_000
AUDIT_MESSAGE_PREVIEW_LENGTH = 240

AuditStage = Literal[
    "event_start",
    "triage",
    "memory",
    "decision",
    "execution",
    "event_end",
    "error",
]

AUDIT_STAGES = frozenset(
    {
        "event_start",
        "triage",
        "memory",
        "decision",
        "execution",
        "event_end",
        "error",
    }
)

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "auth_token",
        "access_token",
        "refresh_token",
        "id_token",
        "bearer_token",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
        "cookie",
        "set_cookie",
        "env",
        "dotenv",
    }
)

_SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\b(?:gsk_|xai-)[0-9A-Za-z_-]{16,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z_-]{16,}\b"),
    re.compile(
        r"(?i)\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
        r"auth[_ -]?token|password|passwd|client[_ -]?secret|private[_ -]?key)"
        r"\s*[:=]\s*[^\s,;]+"
    ),
)


def _build_defaults() -> dict[str, Any]:
    return {"commit": None, "branch": None, "config_hash": None}


def _llm_defaults() -> dict[str, Any]:
    return {"provider": None, "model_id": None, "fallback_used": False}


def _input_defaults() -> dict[str, Any]:
    return {"origin": None, "event_type": None, "message_preview": None}


def _pre_defaults() -> dict[str, Any]:
    return {"state": {}, "task": {}, "perception_snapshot_id": None}


def _triage_defaults() -> dict[str, Any]:
    return {
        "intent": None,
        "route": None,
        "effective_route": None,
        "confidence": None,
        "reason": None,
    }


def _memory_defaults() -> dict[str, Any]:
    return {
        "retrieved_ids": [],
        "scores": [],
        "created_ids": [],
        "working_memory_count": 0,
    }


def _decision_defaults() -> dict[str, Any]:
    return {"layer": "none", "action": None, "valid": True}


def _execution_defaults() -> dict[str, Any]:
    return {"status": "pending", "error_type": None}


def _post_defaults() -> dict[str, Any]:
    return {"state": {}, "task": {}}


_SECTION_DEFAULTS = {
    "build": _build_defaults,
    "llm": _llm_defaults,
    "input": _input_defaults,
    "pre": _pre_defaults,
    "triage": _triage_defaults,
    "memory": _memory_defaults,
    "decision": _decision_defaults,
    "execution": _execution_defaults,
    "post": _post_defaults,
}


def _utc_timestamp(value: datetime | str | None = None) -> str:
    if value is None:
        parsed = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            parsed = datetime.now(timezone.utc)
        else:
            try:
                parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("timestamp debe usar formato ISO-8601") from exc
    else:
        raise TypeError("timestamp debe ser datetime, str o None")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _normalize_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalize_key(key)
    if normalized in _SENSITIVE_KEYS:
        return True
    parts = set(normalized.split("_"))
    if parts.intersection(
        {"authorization", "password", "passwd", "secret", "credential", "credentials", "cookie"}
    ):
        return True
    return "api_key" in normalized or "apikey" in normalized


def _redact_text(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(AUDIT_REDACTED, redacted)
    if len(redacted) > AUDIT_MAX_STRING_LENGTH:
        return redacted[: AUDIT_MAX_STRING_LENGTH - 1] + "…"
    return redacted


def safe_preview(value: object, max_length: int = AUDIT_MESSAGE_PREVIEW_LENGTH) -> str:
    """Crea una vista previa en una linea, acotada y con secretos redactados."""
    if max_length < 1:
        raise ValueError("max_length debe ser mayor que cero")
    preview = " ".join(str(value or "").split())
    preview = _redact_text(preview)
    if len(preview) > max_length:
        return preview[: max_length - 1] + "…"
    return preview


def _safe_value(
    value: Any,
    *,
    key: object | None = None,
    depth: int = 0,
    seen: set[int] | None = None,
) -> Any:
    if key is not None and _is_sensitive_key(key):
        return AUDIT_REDACTED
    if depth > 12:
        return "<max-depth>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, datetime):
        return _utc_timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Path, UUID)):
        return _redact_text(str(value))
    if isinstance(value, Enum):
        return _safe_value(value.value, depth=depth + 1, seen=seen)
    if isinstance(value, BaseException):
        return {"type": value.__class__.__name__}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<bytes:{len(value)}>"

    seen = seen if seen is not None else set()
    marker = id(value)
    if marker in seen:
        return "<cycle>"

    if is_dataclass(value) and not isinstance(value, type):
        seen.add(marker)
        try:
            return {
                item.name: _safe_value(
                    getattr(value, item.name),
                    key=item.name,
                    depth=depth + 1,
                    seen=seen,
                )
                for item in fields(value)
            }
        finally:
            seen.remove(marker)

    if isinstance(value, Mapping):
        seen.add(marker)
        try:
            return {
                str(item_key): _safe_value(
                    item_value,
                    key=item_key,
                    depth=depth + 1,
                    seen=seen,
                )
                for item_key, item_value in value.items()
            }
        finally:
            seen.remove(marker)

    if isinstance(value, set | frozenset):
        value = sorted(value, key=repr)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        seen.add(marker)
        try:
            return [
                _safe_value(item, depth=depth + 1, seen=seen)
                for item in value
            ]
        finally:
            seen.remove(marker)

    return _redact_text(str(value))


def safe_serialize_value(value: Any) -> Any:
    """Convierte un valor arbitrario a tipos compatibles con JSON y lo sanea."""
    return _safe_value(value)


def _merge_section(default_factory, value: object) -> dict[str, Any]:
    merged = default_factory()
    if isinstance(value, Mapping):
        merged.update(value)
    return merged


@dataclass(slots=True)
class AuditTrace:
    """Registro estructurado de una etapa cognitiva de una transaccion."""

    trace_version: int = AUDIT_TRACE_VERSION
    trace_id: str = field(default_factory=lambda: str(uuid4()))
    id_transaccion: str | None = None
    timestamp: datetime | str | None = field(default_factory=lambda: datetime.now(timezone.utc))
    stage: AuditStage | str = "event_start"
    build: dict[str, Any] = field(default_factory=_build_defaults)
    llm: dict[str, Any] = field(default_factory=_llm_defaults)
    input: dict[str, Any] = field(default_factory=_input_defaults)
    pre: dict[str, Any] = field(default_factory=_pre_defaults)
    triage: dict[str, Any] = field(default_factory=_triage_defaults)
    memory: dict[str, Any] = field(default_factory=_memory_defaults)
    decision: dict[str, Any] = field(default_factory=_decision_defaults)
    execution: dict[str, Any] = field(default_factory=_execution_defaults)
    post: dict[str, Any] = field(default_factory=_post_defaults)
    durations_ms: dict[str, Any] = field(default_factory=dict)
    error: Any = None

    def __post_init__(self) -> None:
        if self.trace_version != AUDIT_TRACE_VERSION:
            raise ValueError(f"AuditTrace solo admite trace_version={AUDIT_TRACE_VERSION}")

        self.trace_id = str(self.trace_id or uuid4())
        self.id_transaccion = (
            str(self.id_transaccion).strip() if self.id_transaccion is not None else None
        ) or None
        self.timestamp = _utc_timestamp(self.timestamp)
        self.stage = str(self.stage or "event_start").strip()
        if self.stage not in AUDIT_STAGES:
            raise ValueError(f"stage de AuditTrace no valido: {self.stage}")

        for section_name, default_factory in _SECTION_DEFAULTS.items():
            setattr(
                self,
                section_name,
                _merge_section(default_factory, getattr(self, section_name)),
            )

        if not isinstance(self.durations_ms, Mapping):
            self.durations_ms = {}
        else:
            self.durations_ms = dict(self.durations_ms)

        message = self.input.get("message_preview")
        if message is not None:
            self.input["message_preview"] = safe_preview(message)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None = None) -> "AuditTrace":
        """Construye el contrato desde un dict parcial e ignora campos superiores ajenos."""
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise TypeError("AuditTrace.from_mapping espera un mapping o None")
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: item for key, item in value.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        """Devuelve la forma estable, saneada y compatible con JSON."""
        raw = {item.name: getattr(self, item.name) for item in fields(self)}
        return safe_serialize_value(raw)

    def to_json(self) -> str:
        """Serializa una linea JSON determinista; nunca emite NaN/Infinity."""
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def serialize_audit_trace(value: AuditTrace | Mapping[str, Any] | None = None) -> str:
    """Serializa un AuditTrace o un dict parcial usando el contrato v1."""
    trace = value if isinstance(value, AuditTrace) else AuditTrace.from_mapping(value)
    return trace.to_json()
