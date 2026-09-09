"""Contratos internos de salud para recuperacion de memoria cognitiva."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Iterable, Mapping

from memory.memory_schema import MemorySchemaDecodeError
from memory.memory_types import MemoryResult


class RetrievalHealth(str, Enum):
    """Estado finito de una operacion de recuperacion."""

    OK = "OK"
    OK_EMPTY = "OK_EMPTY"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class RetrievalIssueCode(str, Enum):
    """Razones estables y redactadas de degradacion de retrieval."""

    BACKEND_FAILURE = "BACKEND_FAILURE"
    MALFORMED_BATCH = "MALFORMED_BATCH"
    INVALID_ID = "INVALID_ID"
    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    MISSING_METADATA = "MISSING_METADATA"
    INVALID_DISTANCE = "INVALID_DISTANCE"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    SCORING_FAILURE = "SCORING_FAILURE"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


def merge_reason_codes(
    *groups: Iterable[RetrievalIssueCode],
) -> tuple[RetrievalIssueCode, ...]:
    """Deduplica reason codes conservando su primera aparicion estable."""

    merged: list[RetrievalIssueCode] = []
    seen: set[RetrievalIssueCode] = set()
    for group in groups:
        for code in group:
            if code not in seen:
                seen.add(code)
                merged.append(code)
    return tuple(merged)


@dataclass(frozen=True)
class StoreRetrievalReceipt:
    """Resultados validos y salud de una unica fuente de memoria."""

    health: RetrievalHealth
    results: tuple[MemoryResult, ...] = ()
    candidate_count: int = 0
    dropped_count: int = 0
    reason_codes: tuple[RetrievalIssueCode, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(
            self,
            "reason_codes",
            merge_reason_codes(self.reason_codes),
        )
        if self.candidate_count < 0 or self.dropped_count < 0:
            raise ValueError("retrieval counts must be non-negative")
        if self.candidate_count != self.valid_count + self.dropped_count:
            raise ValueError("candidate_count must equal valid_count + dropped_count")

    @property
    def valid_count(self) -> int:
        return len(self.results)


@dataclass(frozen=True)
class MemoryRetrievalReceipt:
    """Resultado agregado de las fuentes episodica y semantica."""

    health: RetrievalHealth
    results: tuple[MemoryResult, ...]
    episodic: StoreRetrievalReceipt
    semantic: StoreRetrievalReceipt
    reason_codes: tuple[RetrievalIssueCode, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(
            self,
            "reason_codes",
            merge_reason_codes(
                self.episodic.reason_codes,
                self.semantic.reason_codes,
                self.reason_codes,
            ),
        )

    @property
    def candidate_count(self) -> int:
        return self.episodic.candidate_count + self.semantic.candidate_count

    @property
    def source_valid_count(self) -> int:
        return self.episodic.valid_count + self.semantic.valid_count

    @property
    def returned_count(self) -> int:
        return len(self.results)

    @property
    def dropped_count(self) -> int:
        return self.episodic.dropped_count + self.semantic.dropped_count


def extract_aligned_query_rows(
    raw_result: Any,
) -> tuple[tuple[tuple[Any, Any, Any, Any], ...], int, bool]:
    """Extrae filas posicionales sin intentar reparar batches desalineados."""

    if not isinstance(raw_result, Mapping):
        return (), 0, True

    batches: dict[str, list[Any] | tuple[Any, ...]] = {}
    candidate_count = 0
    for key in ("ids", "documents", "metadatas", "distances"):
        outer = raw_result.get(key)
        if not isinstance(outer, (list, tuple)) or len(outer) != 1:
            return (), candidate_count, True
        inner = outer[0]
        if not isinstance(inner, (list, tuple)):
            return (), candidate_count, True
        batches[key] = inner
        if key == "ids":
            candidate_count = len(inner)

    ids = batches["ids"]
    if any(len(batches[key]) != candidate_count for key in ("documents", "metadatas", "distances")):
        return (), candidate_count, True

    rows = tuple(
        (
            ids[index],
            batches["documents"][index],
            batches["metadatas"][index],
            batches["distances"][index],
        )
        for index in range(candidate_count)
    )
    return rows, candidate_count, False


def validate_transport_row(
    row: tuple[Any, Any, Any, Any],
) -> tuple[tuple[str, str, dict[str, Any], float] | None, RetrievalIssueCode | None]:
    """Valida una fila ya alineada sin fabricar campos ausentes."""

    memory_id, document, metadata, distance = row
    if not isinstance(memory_id, str) or not memory_id.strip():
        return None, RetrievalIssueCode.INVALID_ID
    if not isinstance(document, str):
        return None, RetrievalIssueCode.INVALID_DOCUMENT
    if metadata is None:
        return None, RetrievalIssueCode.MISSING_METADATA
    if not isinstance(metadata, dict):
        return None, RetrievalIssueCode.INVALID_SCHEMA
    if isinstance(distance, bool) or not isinstance(distance, (int, float)):
        return None, RetrievalIssueCode.INVALID_DISTANCE
    clean_distance = float(distance)
    if not math.isfinite(clean_distance):
        return None, RetrievalIssueCode.INVALID_DISTANCE
    return (memory_id, document, metadata, clean_distance), None


def schema_issue_code(error: MemorySchemaDecodeError) -> RetrievalIssueCode:
    """Mapea errores redactados de schema al dominio estable de retrieval."""

    if error.reason_code == "unknown_schema_version":
        return RetrievalIssueCode.UNKNOWN_SCHEMA
    if error.reason_code == "invalid_timestamp":
        return RetrievalIssueCode.INVALID_TIMESTAMP
    return RetrievalIssueCode.INVALID_SCHEMA
