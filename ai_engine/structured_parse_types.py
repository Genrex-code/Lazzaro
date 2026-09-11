"""Contratos internos para resultados de parsing estructurado.

El modulo no aplica policy de ejecucion, retry, fallback ni cache. Su unica
responsabilidad es describir de forma finita y versionada lo observado al
interpretar una salida que debia ser JSON estructurado.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, Optional, TypeVar


T = TypeVar("T")
_MAX_REPORTED_RAW_LENGTH = 2_147_483_647
_MAX_SALVAGE_LENGTH = 12_000


class StructuredParseStatus(str, Enum):
    VALID = "VALID"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"


class StructuredParseIssueCode(str, Enum):
    EMPTY_OUTPUT = "EMPTY_OUTPUT"
    MALFORMED_JSON = "MALFORMED_JSON"
    WRONG_ROOT_TYPE = "WRONG_ROOT_TYPE"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    PARTIAL_RECOVERY = "PARTIAL_RECOVERY"
    PLAIN_TEXT_ONLY = "PLAIN_TEXT_ONLY"
    NONCANONICAL_WRAPPER = "NONCANONICAL_WRAPPER"
    MULTIPLE_OBJECTS = "MULTIPLE_OBJECTS"
    DUPLICATE_KEYS = "DUPLICATE_KEYS"


class StructuredNormalization(str, Enum):
    FENCE_STRIPPED = "FENCE_STRIPPED"
    THINK_BLOCK_STRIPPED = "THINK_BLOCK_STRIPPED"


class StructuredRawShape(str, Enum):
    EMPTY = "EMPTY"
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"
    STRING = "STRING"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    NULL = "NULL"
    UNKNOWN = "UNKNOWN"


class StructuredTextSalvageKind(str, Enum):
    PERSONA_SAFE_TEXT = "PERSONA_SAFE_TEXT"
    ORACLE_CANDIDATE_TEXT = "ORACLE_CANDIDATE_TEXT"


@dataclass(frozen=True)
class StructuredTextSalvage:
    kind: StructuredTextSalvageKind
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, StructuredTextSalvageKind):
            raise ValueError("salvage.kind must be StructuredTextSalvageKind")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("salvage.text must be a non-empty string")
        if len(self.text) > _MAX_SALVAGE_LENGTH:
            raise ValueError("salvage.text exceeds the bounded contract")


@dataclass(frozen=True)
class StructuredParseResult(Generic[T]):
    contract_version: int
    status: StructuredParseStatus
    value: Optional[T]
    salvage: Optional[StructuredTextSalvage]
    reason_codes: tuple[StructuredParseIssueCode, ...]
    normalizations: tuple[StructuredNormalization, ...]
    raw_shape: StructuredRawShape
    raw_length: int

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("contract_version must be the integer 1")
        if not isinstance(self.status, StructuredParseStatus):
            raise ValueError("status must be StructuredParseStatus")
        if not isinstance(self.raw_shape, StructuredRawShape):
            raise ValueError("raw_shape must be StructuredRawShape")
        if type(self.raw_length) is not int or not 0 <= self.raw_length <= _MAX_REPORTED_RAW_LENGTH:
            raise ValueError("raw_length must be a bounded non-negative integer")
        if not isinstance(self.reason_codes, tuple) or any(
            not isinstance(code, StructuredParseIssueCode) for code in self.reason_codes
        ):
            raise ValueError("reason_codes must be a tuple of StructuredParseIssueCode")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must not contain duplicates")
        if not isinstance(self.normalizations, tuple) or any(
            not isinstance(item, StructuredNormalization) for item in self.normalizations
        ):
            raise ValueError("normalizations must be a tuple of StructuredNormalization")
        if len(set(self.normalizations)) != len(self.normalizations):
            raise ValueError("normalizations must not contain duplicates")
        if self.salvage is not None and not isinstance(self.salvage, StructuredTextSalvage):
            raise ValueError("salvage must be StructuredTextSalvage or None")

        if self.status is StructuredParseStatus.VALID:
            if self.value is None or self.salvage is not None or self.reason_codes:
                raise ValueError("VALID requires value and forbids salvage/reason_codes")
        elif self.status is StructuredParseStatus.DEGRADED:
            if self.value is not None or not self.reason_codes:
                raise ValueError("DEGRADED forbids value and requires a reason_code")
        elif self.value is not None or self.salvage is not None or not self.reason_codes:
            raise ValueError("ERROR forbids value/salvage and requires a reason_code")


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class JsonObjectInspection:
    """Resultado transitorio del lexer JSON; nunca sale del boundary del parser."""

    data: Optional[dict[str, Any]]
    issue_code: Optional[StructuredParseIssueCode]
    normalizations: tuple[StructuredNormalization, ...]
    raw_shape: StructuredRawShape
    raw_length: int
    normalized_text: str


def inspect_json_object(raw: object) -> JsonObjectInspection:
    """Inspecciona exactamente una salida sin coercion ni reparacion de dominio."""
    if not isinstance(raw, str):
        return JsonObjectInspection(
            data=None,
            issue_code=StructuredParseIssueCode.WRONG_ROOT_TYPE,
            normalizations=(),
            raw_shape=_shape_for_python_value(raw),
            raw_length=0,
            normalized_text="",
        )

    raw_length = min(len(raw), _MAX_REPORTED_RAW_LENGTH)
    text = raw.strip()
    if not text:
        return JsonObjectInspection(
            data=None,
            issue_code=StructuredParseIssueCode.EMPTY_OUTPUT,
            normalizations=(),
            raw_shape=StructuredRawShape.EMPTY,
            raw_length=raw_length,
            normalized_text="",
        )

    normalizations: list[StructuredNormalization] = []
    text, think_removed = _strip_leading_think_block(text)
    if think_removed:
        normalizations.append(StructuredNormalization.THINK_BLOCK_STRIPPED)

    text, fence_removed = _strip_exact_fence(text)
    if fence_removed:
        normalizations.append(StructuredNormalization.FENCE_STRIPPED)

    text, inner_think_removed = _strip_leading_think_block(text)
    if inner_think_removed and StructuredNormalization.THINK_BLOCK_STRIPPED not in normalizations:
        normalizations.append(StructuredNormalization.THINK_BLOCK_STRIPPED)

    text = text.strip()
    if not text:
        return JsonObjectInspection(
            data=None,
            issue_code=StructuredParseIssueCode.EMPTY_OUTPUT,
            normalizations=tuple(normalizations),
            raw_shape=StructuredRawShape.EMPTY,
            raw_length=raw_length,
            normalized_text=text,
        )

    decoder = json.JSONDecoder(object_pairs_hook=_reject_duplicate_keys)
    try:
        parsed, end = decoder.raw_decode(text)
    except _DuplicateJsonKey:
        return _inspection_error(
            StructuredParseIssueCode.DUPLICATE_KEYS,
            text,
            normalizations,
            raw_length,
        )
    except json.JSONDecodeError:
        return _inspect_non_exact_json(text, decoder, normalizations, raw_length)

    trailing = text[end:].strip()
    if trailing:
        try:
            second_object = isinstance(parsed, dict) and _is_second_complete_object(
                trailing,
                decoder,
            )
        except _DuplicateJsonKey:
            return _inspection_error(
                StructuredParseIssueCode.DUPLICATE_KEYS,
                text,
                normalizations,
                raw_length,
                shape=StructuredRawShape.OBJECT,
            )
        if second_object:
            return _inspection_error(
                StructuredParseIssueCode.MULTIPLE_OBJECTS,
                text,
                normalizations,
                raw_length,
                shape=StructuredRawShape.OBJECT,
            )
        if isinstance(parsed, dict):
            return JsonObjectInspection(
                data=parsed,
                issue_code=StructuredParseIssueCode.NONCANONICAL_WRAPPER,
                normalizations=tuple(normalizations),
                raw_shape=StructuredRawShape.OBJECT,
                raw_length=raw_length,
                normalized_text=text,
            )
        return _inspection_error(
            StructuredParseIssueCode.WRONG_ROOT_TYPE,
            text,
            normalizations,
            raw_length,
            shape=_shape_for_python_value(parsed),
        )

    shape = _shape_for_python_value(parsed)
    if not isinstance(parsed, dict):
        return _inspection_error(
            StructuredParseIssueCode.WRONG_ROOT_TYPE,
            text,
            normalizations,
            raw_length,
            shape=shape,
        )
    return JsonObjectInspection(
        data=parsed,
        issue_code=None,
        normalizations=tuple(normalizations),
        raw_shape=StructuredRawShape.OBJECT,
        raw_length=raw_length,
        normalized_text=text,
    )


def _inspect_non_exact_json(
    text: str,
    decoder: json.JSONDecoder,
    normalizations: list[StructuredNormalization],
    raw_length: int,
) -> JsonObjectInspection:
    if text.lstrip().startswith(("{", "[")):
        return _inspection_error(
            StructuredParseIssueCode.MALFORMED_JSON,
            text,
            normalizations,
            raw_length,
        )

    candidates: list[dict[str, Any]] = []
    index = 0
    while index < len(text):
        brace = text.find("{", index)
        if brace < 0:
            break
        try:
            parsed, consumed = decoder.raw_decode(text[brace:])
        except _DuplicateJsonKey:
            return _inspection_error(
                StructuredParseIssueCode.DUPLICATE_KEYS,
                text,
                normalizations,
                raw_length,
                shape=StructuredRawShape.OBJECT,
            )
        except json.JSONDecodeError:
            index = brace + 1
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
            index = brace + consumed
        else:
            index = brace + 1

    if len(candidates) > 1:
        return _inspection_error(
            StructuredParseIssueCode.MULTIPLE_OBJECTS,
            text,
            normalizations,
            raw_length,
            shape=StructuredRawShape.OBJECT,
        )
    if len(candidates) == 1:
        return JsonObjectInspection(
            data=candidates[0],
            issue_code=StructuredParseIssueCode.NONCANONICAL_WRAPPER,
            normalizations=tuple(normalizations),
            raw_shape=StructuredRawShape.OBJECT,
            raw_length=raw_length,
            normalized_text=text,
        )

    return _inspection_error(
        StructuredParseIssueCode.PLAIN_TEXT_ONLY,
        text,
        normalizations,
        raw_length,
    )


def _strip_leading_think_block(text: str) -> tuple[str, bool]:
    if not text.lower().startswith("<think>"):
        return text, False
    close_match = re.search(r"</think>", text, flags=re.IGNORECASE)
    if close_match is None:
        return text, False
    return text[close_match.end() :].strip(), True


def _strip_exact_fence(text: str) -> tuple[str, bool]:
    match = re.fullmatch(
        r"```(?:json)?[ \t]*\r?\n(?P<payload>[\s\S]*?)\r?\n```",
        text,
        flags=re.IGNORECASE,
    )
    if match is None:
        return text, False
    return match.group("payload").strip(), True


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(key)
        result[key] = value
    return result


def _is_second_complete_object(text: str, decoder: json.JSONDecoder) -> bool:
    if not text.startswith("{"):
        return False
    try:
        parsed, end = decoder.raw_decode(text)
    except _DuplicateJsonKey:
        raise
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict)


def _inspection_error(
    issue: StructuredParseIssueCode,
    text: str,
    normalizations: list[StructuredNormalization],
    raw_length: int,
    *,
    shape: Optional[StructuredRawShape] = None,
) -> JsonObjectInspection:
    return JsonObjectInspection(
        data=None,
        issue_code=issue,
        normalizations=tuple(normalizations),
        raw_shape=shape or _shape_for_text(text),
        raw_length=raw_length,
        normalized_text=text,
    )


def _shape_for_text(text: str) -> StructuredRawShape:
    stripped = text.lstrip()
    if not stripped:
        return StructuredRawShape.EMPTY
    if stripped.startswith("{"):
        return StructuredRawShape.OBJECT
    if stripped.startswith("["):
        return StructuredRawShape.ARRAY
    if stripped.startswith('"'):
        return StructuredRawShape.STRING
    if stripped.startswith(("true", "false")):
        return StructuredRawShape.BOOLEAN
    if stripped.startswith("null"):
        return StructuredRawShape.NULL
    if stripped[0] in "-0123456789":
        return StructuredRawShape.NUMBER
    return StructuredRawShape.STRING


def _shape_for_python_value(value: object) -> StructuredRawShape:
    if value is None:
        return StructuredRawShape.NULL
    if isinstance(value, bool):
        return StructuredRawShape.BOOLEAN
    if isinstance(value, dict):
        return StructuredRawShape.OBJECT
    if isinstance(value, list):
        return StructuredRawShape.ARRAY
    if isinstance(value, str):
        return StructuredRawShape.STRING
    if isinstance(value, (int, float)):
        return StructuredRawShape.NUMBER
    return StructuredRawShape.UNKNOWN
