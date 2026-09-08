"""Codec versionado para la metadata persistida de memoria cognitiva."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from typing import Any, Iterable


MEMORY_SCHEMA_VERSION = 1
MEMORY_SCHEMA_VERSION_KEY = "__lazzaro_memory_schema_version"
MEMORY_EXTRA_JSON_KEY = "__lazzaro_memory_extra_json"
MEMORY_STATE_JSON_KEY = "__lazzaro_memory_state_json"
MEMORY_INTERNAL_PREFIX = "__lazzaro_memory_"

EPISODIC_KIND = "episodico"
SEMANTIC_KIND = "semantico"
MEMORY_KINDS = frozenset({EPISODIC_KIND, SEMANTIC_KIND})

CANONICAL_DEFAULT_ORIGIN = "desconocido"
LEGACY_DEFAULT_ORIGIN = "determinista"

_COMMON_CANONICAL_KEYS = frozenset(
    {
        "tipo",
        "tipo_evento",
        "valencia",
        "impacto_emocional",
        "timestamp",
    }
)
_INTERNAL_KEYS = frozenset(
    {
        MEMORY_SCHEMA_VERSION_KEY,
        MEMORY_EXTRA_JSON_KEY,
        MEMORY_STATE_JSON_KEY,
    }
)


def _safe_memory_type(value: Any) -> str | None:
    return value if isinstance(value, str) and value in MEMORY_KINDS else None


class MemorySchemaEncodeError(ValueError):
    """Fallo determinista y redactado al construir metadata persistible."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(f"memory_schema_encode_error reason_code={reason_code}")


class MemorySchemaDecodeError(ValueError):
    """Fallo determinista y redactado al decodificar metadata versionada."""

    def __init__(
        self,
        reason_code: str,
        *,
        schema_version: int | None = None,
        memory_type: str | None = None,
    ) -> None:
        safe_version = schema_version if type(schema_version) is int else None
        safe_type = _safe_memory_type(memory_type)
        self.reason_code = reason_code
        self.schema_version = safe_version
        self.memory_type = safe_type
        details = [f"reason_code={reason_code}"]
        if safe_version is not None:
            details.append(f"schema_version={safe_version}")
        if safe_type is not None:
            details.append(f"memory_type={safe_type}")
        super().__init__(f"memory_schema_decode_error {' '.join(details)}")


@dataclass(frozen=True)
class DecodedMemoryMetadata:
    """Campos reconstruidos desde metadata V0 o V1."""

    tipo_evento: str
    valencia: float
    impacto_emocional: float
    timestamp: datetime
    extra: dict[str, Any]
    estado_emocional: dict[str, bool | int | float] | None = None
    origen: str | None = None
    schema_version: int | None = None


class _DuplicateJsonKey(ValueError):
    pass


class _NonFiniteJsonConstant(ValueError):
    pass


def _reject_non_finite_constant(_value: str) -> None:
    raise _NonFiniteJsonConstant


def _unique_object(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _json_domain_reason(value: Any, seen: set[int] | None = None) -> str | None:
    if value is None or isinstance(value, (str, bool, int)):
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else "non_finite_json_number"

    active = seen if seen is not None else set()
    if isinstance(value, list):
        identity = id(value)
        if identity in active:
            return "cyclic_json_value"
        active.add(identity)
        try:
            for item in value:
                reason = _json_domain_reason(item, active)
                if reason:
                    return reason
        finally:
            active.remove(identity)
        return None

    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            return "cyclic_json_value"
        active.add(identity)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    return "non_string_json_key"
                reason = _json_domain_reason(item, active)
                if reason:
                    return reason
        finally:
            active.remove(identity)
        return None

    return "unsupported_json_value"


def _canonical_json(value: Any) -> str:
    reason = _json_domain_reason(value)
    if reason:
        raise MemorySchemaEncodeError(reason)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise MemorySchemaEncodeError("invalid_json_value") from exc


def _decode_json(
    raw: Any,
    *,
    root: str,
    schema_version: int,
    memory_type: str,
) -> Any:
    if not isinstance(raw, str):
        raise MemorySchemaDecodeError(
            f"{root}_json_wrong_type",
            schema_version=schema_version,
            memory_type=memory_type,
        )
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_non_finite_constant,
        )
    except _DuplicateJsonKey as exc:
        raise MemorySchemaDecodeError(
            f"duplicate_{root}_json_key",
            schema_version=schema_version,
            memory_type=memory_type,
        ) from exc
    except _NonFiniteJsonConstant as exc:
        raise MemorySchemaDecodeError(
            f"non_finite_{root}_json_number",
            schema_version=schema_version,
            memory_type=memory_type,
        ) from exc
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError) as exc:
        raise MemorySchemaDecodeError(
            f"invalid_{root}_json",
            schema_version=schema_version,
            memory_type=memory_type,
        ) from exc

    reason = _json_domain_reason(value)
    if reason:
        reason_code = (
            f"non_finite_{root}_json_number"
            if reason == "non_finite_json_number"
            else f"invalid_{root}_json_domain"
        )
        raise MemorySchemaDecodeError(
            reason_code,
            schema_version=schema_version,
            memory_type=memory_type,
        )
    return value


def _encode_number(value: Any, reason_code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MemorySchemaEncodeError(reason_code)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MemorySchemaEncodeError(reason_code) from exc
    if not math.isfinite(result):
        raise MemorySchemaEncodeError(reason_code)
    return result


def _decode_number(
    value: Any,
    reason_code: str,
    *,
    schema_version: int,
    memory_type: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MemorySchemaDecodeError(
            reason_code,
            schema_version=schema_version,
            memory_type=memory_type,
        )
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise MemorySchemaDecodeError(
            reason_code,
            schema_version=schema_version,
            memory_type=memory_type,
        ) from exc
    if not math.isfinite(result):
        raise MemorySchemaDecodeError(
            reason_code,
            schema_version=schema_version,
            memory_type=memory_type,
        )
    return result


def _encode_text(value: Any, reason_code: str) -> str:
    if not isinstance(value, str):
        raise MemorySchemaEncodeError(reason_code)
    return value


def _decode_text(
    metadata: dict[str, Any],
    key: str,
    *,
    schema_version: int,
    memory_type: str,
) -> str:
    value = metadata.get(key)
    if not isinstance(value, str):
        raise MemorySchemaDecodeError(
            f"invalid_{key}",
            schema_version=schema_version,
            memory_type=memory_type,
        )
    return value


def _encode_timestamp(value: Any) -> str:
    if not isinstance(value, datetime):
        raise MemorySchemaEncodeError("invalid_timestamp")
    return value.isoformat()


def _decode_timestamp(
    value: Any,
    *,
    schema_version: int,
    memory_type: str,
) -> datetime:
    if not isinstance(value, str) or not value:
        raise MemorySchemaDecodeError(
            "invalid_timestamp",
            schema_version=schema_version,
            memory_type=memory_type,
        )
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise MemorySchemaDecodeError(
            "invalid_timestamp",
            schema_version=schema_version,
            memory_type=memory_type,
        ) from exc


def _validate_emotional_state_for_encode(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise MemorySchemaEncodeError("invalid_emotional_state_root")
    for key, item in value.items():
        if not isinstance(key, str):
            raise MemorySchemaEncodeError("invalid_emotional_state_key")
        if isinstance(item, bool) or isinstance(item, int):
            continue
        if isinstance(item, float) and math.isfinite(item):
            continue
        if isinstance(item, float):
            raise MemorySchemaEncodeError("non_finite_emotional_state_number")
        raise MemorySchemaEncodeError("unsupported_emotional_state_value")


def _decode_emotional_state(
    raw: Any,
    *,
    schema_version: int,
    memory_type: str,
) -> dict[str, bool | int | float] | None:
    value = _decode_json(
        raw,
        root="state",
        schema_version=schema_version,
        memory_type=memory_type,
    )
    if value is None:
        return None
    if not isinstance(value, dict):
        raise MemorySchemaDecodeError(
            "invalid_emotional_state_root",
            schema_version=schema_version,
            memory_type=memory_type,
        )
    for key, item in value.items():
        if not isinstance(key, str):
            raise MemorySchemaDecodeError(
                "invalid_emotional_state_key",
                schema_version=schema_version,
                memory_type=memory_type,
            )
        if isinstance(item, bool) or isinstance(item, int):
            continue
        if isinstance(item, float) and math.isfinite(item):
            continue
        reason = (
            "non_finite_emotional_state_number"
            if isinstance(item, float)
            else "unsupported_emotional_state_value"
        )
        raise MemorySchemaDecodeError(
            reason,
            schema_version=schema_version,
            memory_type=memory_type,
        )
    return value


def _v0_text(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _v0_float(value: Any, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


class MemorySchemaCodec:
    """Única autoridad de encode/decode para metadata cognitiva V1."""

    @staticmethod
    def reserved_keys(memory_type: str) -> frozenset[str]:
        if memory_type not in MEMORY_KINDS:
            raise MemorySchemaEncodeError("invalid_memory_type")
        reserved = set(_COMMON_CANONICAL_KEYS)
        reserved.update(_INTERNAL_KEYS)
        if memory_type == SEMANTIC_KIND:
            reserved.add("origen")
        return frozenset(reserved)

    @classmethod
    def project_extra(
        cls,
        extra: dict[str, Any],
        memory_type: str,
    ) -> dict[str, str | bool | int | float]:
        if not isinstance(extra, dict):
            raise MemorySchemaEncodeError("invalid_extra_root")
        reason = _json_domain_reason(extra)
        if reason:
            raise MemorySchemaEncodeError(reason)

        reserved = cls.reserved_keys(memory_type)
        projected: dict[str, str | bool | int | float] = {}
        for key, value in extra.items():
            if key in reserved or key.startswith(MEMORY_INTERNAL_PREFIX):
                continue
            if isinstance(value, (str, bool, int)):
                projected[key] = value
            elif isinstance(value, float) and math.isfinite(value):
                projected[key] = value
        return projected

    @classmethod
    def encode_episodic_metadata(
        cls,
        *,
        tipo_evento: Any,
        valencia: Any,
        impacto_emocional: Any,
        timestamp: Any,
        extra: Any,
        estado_emocional: Any,
    ) -> dict[str, Any]:
        if not isinstance(extra, dict):
            raise MemorySchemaEncodeError("invalid_extra_root")
        _validate_emotional_state_for_encode(estado_emocional)
        extra_json = _canonical_json(extra)
        state_json = _canonical_json(estado_emocional)
        metadata: dict[str, Any] = {
            MEMORY_SCHEMA_VERSION_KEY: MEMORY_SCHEMA_VERSION,
            "tipo": EPISODIC_KIND,
            "tipo_evento": _encode_text(tipo_evento, "invalid_tipo_evento"),
            "valencia": _encode_number(valencia, "invalid_valencia"),
            "impacto_emocional": _encode_number(
                impacto_emocional,
                "invalid_impacto_emocional",
            ),
            "timestamp": _encode_timestamp(timestamp),
            MEMORY_EXTRA_JSON_KEY: extra_json,
            MEMORY_STATE_JSON_KEY: state_json,
        }
        metadata.update(cls.project_extra(extra, EPISODIC_KIND))
        return metadata

    @classmethod
    def encode_semantic_metadata(
        cls,
        *,
        tipo_evento: Any,
        valencia: Any,
        impacto_emocional: Any,
        timestamp: Any,
        extra: Any,
        origen: Any,
    ) -> dict[str, Any]:
        if not isinstance(extra, dict):
            raise MemorySchemaEncodeError("invalid_extra_root")
        extra_json = _canonical_json(extra)
        metadata: dict[str, Any] = {
            MEMORY_SCHEMA_VERSION_KEY: MEMORY_SCHEMA_VERSION,
            "tipo": SEMANTIC_KIND,
            "tipo_evento": _encode_text(tipo_evento, "invalid_tipo_evento"),
            "valencia": _encode_number(valencia, "invalid_valencia"),
            "impacto_emocional": _encode_number(
                impacto_emocional,
                "invalid_impacto_emocional",
            ),
            "timestamp": _encode_timestamp(timestamp),
            "origen": _encode_text(origen, "invalid_origen"),
            MEMORY_EXTRA_JSON_KEY: extra_json,
        }
        metadata.update(cls.project_extra(extra, SEMANTIC_KIND))
        return metadata

    @classmethod
    def decode_metadata(
        cls,
        metadata: Any,
        expected_kind: str,
    ) -> DecodedMemoryMetadata:
        if not isinstance(metadata, dict):
            # Mantiene el boundary V0/ROOT-014: metadata explícitamente ausente
            # sigue sin poder tratarse como un record recuperable.
            raise AttributeError("memory metadata row is not a mapping")
        if MEMORY_SCHEMA_VERSION_KEY not in metadata:
            return cls._decode_v0(metadata, expected_kind)

        version = metadata[MEMORY_SCHEMA_VERSION_KEY]
        memory_type = metadata.get("tipo")
        safe_type = _safe_memory_type(memory_type)
        if type(version) is not int:
            raise MemorySchemaDecodeError(
                "invalid_schema_version_type",
                memory_type=safe_type,
            )
        if version != MEMORY_SCHEMA_VERSION:
            raise MemorySchemaDecodeError(
                "unknown_schema_version",
                schema_version=version,
                memory_type=safe_type,
            )
        return cls._decode_v1(metadata, expected_kind)

    @classmethod
    def _decode_v1(
        cls,
        metadata: dict[str, Any],
        expected_kind: str,
    ) -> DecodedMemoryMetadata:
        version = MEMORY_SCHEMA_VERSION
        memory_type = metadata.get("tipo")
        if memory_type != expected_kind or expected_kind not in MEMORY_KINDS:
            raise MemorySchemaDecodeError(
                "unexpected_memory_type",
                schema_version=version,
                memory_type=_safe_memory_type(memory_type),
            )

        tipo_evento = _decode_text(
            metadata,
            "tipo_evento",
            schema_version=version,
            memory_type=expected_kind,
        )
        valencia = _decode_number(
            metadata.get("valencia"),
            "invalid_valencia",
            schema_version=version,
            memory_type=expected_kind,
        )
        impacto = _decode_number(
            metadata.get("impacto_emocional"),
            "invalid_impacto_emocional",
            schema_version=version,
            memory_type=expected_kind,
        )
        timestamp = _decode_timestamp(
            metadata.get("timestamp"),
            schema_version=version,
            memory_type=expected_kind,
        )
        extra = _decode_json(
            metadata.get(MEMORY_EXTRA_JSON_KEY),
            root="extra",
            schema_version=version,
            memory_type=expected_kind,
        )
        if not isinstance(extra, dict):
            raise MemorySchemaDecodeError(
                "invalid_extra_json_root",
                schema_version=version,
                memory_type=expected_kind,
            )

        if expected_kind == EPISODIC_KIND:
            state = _decode_emotional_state(
                metadata.get(MEMORY_STATE_JSON_KEY),
                schema_version=version,
                memory_type=expected_kind,
            )
            origin = None
        else:
            state = None
            origin = _decode_text(
                metadata,
                "origen",
                schema_version=version,
                memory_type=expected_kind,
            )

        return DecodedMemoryMetadata(
            tipo_evento=tipo_evento,
            valencia=valencia,
            impacto_emocional=impacto,
            timestamp=timestamp,
            extra=extra,
            estado_emocional=state,
            origen=origin,
            schema_version=version,
        )

    @classmethod
    def _decode_v0(
        cls,
        metadata: dict[str, Any],
        expected_kind: str,
    ) -> DecodedMemoryMetadata:
        default_event = "desconocido" if expected_kind == EPISODIC_KIND else "semantica"
        tipo_evento = _v0_text(metadata.get("tipo_evento"), default_event)
        valencia = _v0_float(metadata.get("valencia"), 0.0)
        impacto = _v0_float(metadata.get("impacto_emocional"), 0.5)
        timestamp_raw = _v0_text(metadata.get("timestamp"), "")
        try:
            timestamp = datetime.fromisoformat(timestamp_raw) if timestamp_raw else datetime.utcnow()
        except Exception:
            timestamp = datetime.utcnow()

        reserved = set(_COMMON_CANONICAL_KEYS)
        if expected_kind == SEMANTIC_KIND:
            reserved.add("origen")
            origin = _v0_text(metadata.get("origen"), LEGACY_DEFAULT_ORIGIN)
        else:
            origin = None
        extra = {key: value for key, value in metadata.items() if key not in reserved}

        return DecodedMemoryMetadata(
            tipo_evento=tipo_evento,
            valencia=valencia,
            impacto_emocional=impacto,
            timestamp=timestamp,
            extra=extra,
            estado_emocional=None,
            origen=origin,
            schema_version=None,
        )

    @classmethod
    def update_scalar_extra(
        cls,
        metadata: dict[str, Any],
        *,
        key: str,
        value: str | bool | int | float,
        expected_kind: str,
    ) -> dict[str, Any]:
        """Actualiza una extra escalar sin separar payload y proyección V1."""
        if not isinstance(metadata, dict):
            raise MemorySchemaEncodeError("invalid_metadata_root")
        if expected_kind not in MEMORY_KINDS:
            raise MemorySchemaEncodeError("invalid_memory_type")
        if not isinstance(key, str):
            raise MemorySchemaEncodeError("non_string_json_key")
        if isinstance(value, float) and not math.isfinite(value):
            raise MemorySchemaEncodeError("non_finite_json_number")
        if not isinstance(value, (str, bool, int, float)):
            raise MemorySchemaEncodeError("unsupported_projection_value")

        updated = dict(metadata)
        if MEMORY_SCHEMA_VERSION_KEY not in metadata:
            updated[key] = value
            return updated

        decoded = cls.decode_metadata(metadata, expected_kind)
        extra = dict(decoded.extra)
        extra[key] = value
        updated[MEMORY_EXTRA_JSON_KEY] = _canonical_json(extra)
        reserved = cls.reserved_keys(expected_kind)
        for metadata_key in list(updated):
            if not isinstance(metadata_key, str):
                updated.pop(metadata_key)
            elif metadata_key not in reserved and not metadata_key.startswith(
                MEMORY_INTERNAL_PREFIX
            ):
                updated.pop(metadata_key)
        updated.update(cls.project_extra(extra, expected_kind))
        return updated
