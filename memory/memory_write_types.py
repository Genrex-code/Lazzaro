"""Contratos tipados para escrituras de memoria verificables (ROOT-015)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MemoryWriteStatus(str, Enum):
    """Outcome durable de una operacion, no la condicion de replay del caller."""

    CREATED = "CREATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    FAILED = "FAILED"
    AMBIGUOUS = "AMBIGUOUS"


class MemoryWriteIssueCode(str, Enum):
    INVALID_INTENT = "INVALID_INTENT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    JOURNAL_FAILURE = "JOURNAL_FAILURE"
    BACKEND_FAILURE = "BACKEND_FAILURE"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    LEGACY_EFFECT_ADOPTED = "LEGACY_EFFECT_ADOPTED"
    POST_WRITE_MAINTENANCE_FAILURE = "POST_WRITE_MAINTENANCE_FAILURE"
    PARTIAL_DELETE = "PARTIAL_DELETE"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"


class MemoryWriteKind(str, Enum):
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    MANUAL = "MANUAL"
    ALIAS = "ALIAS"
    AFFECTIVE = "AFFECTIVE"
    RELATIONAL = "RELATIONAL"
    CONSOLIDATION = "CONSOLIDATION"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class MemoryWriteState(str, Enum):
    INTENT_RECORDED = "INTENT_RECORDED"
    WRITE_ATTEMPTED = "WRITE_ATTEMPTED"
    TARGET_VERIFIED = "TARGET_VERIFIED"
    CLEANUP_PENDING = "CLEANUP_PENDING"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"
    RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"


TERMINAL_MEMORY_WRITE_STATES = frozenset(
    {MemoryWriteState.COMMITTED, MemoryWriteState.FAILED}
)
NON_TERMINAL_MEMORY_WRITE_STATES = frozenset(
    state for state in MemoryWriteState if state not in TERMINAL_MEMORY_WRITE_STATES
)


@dataclass(frozen=True)
class MemoryWriteReceipt:
    operation_id: str
    idempotency_key: str
    operation_kind: MemoryWriteKind
    status: MemoryWriteStatus
    target_id: str | None = None
    matched_count: int = 0
    changed_count: int = 0
    reason_codes: tuple[MemoryWriteIssueCode, ...] = ()
    durable: bool = False
    reconciliation_required: bool = False
    replayed: bool = False


class ExactLookupStatus(str, Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ExactMemoryRow:
    id: str
    document: str
    metadata: dict[str, Any] | None


@dataclass(frozen=True)
class ExactLookupReceipt:
    """Lectura por IDs: presencia/ausencia comprobada o fallo del backend."""

    status: ExactLookupStatus
    requested_ids: tuple[str, ...] = ()
    rows: tuple[ExactMemoryRow, ...] = ()

    @property
    def completed(self) -> bool:
        return self.status is not ExactLookupStatus.FAILED

    @property
    def present_ids(self) -> tuple[str, ...]:
        return tuple(row.id for row in self.rows)


def merge_write_issue_codes(
    *groups: tuple[MemoryWriteIssueCode, ...] | list[MemoryWriteIssueCode],
) -> tuple[MemoryWriteIssueCode, ...]:
    """Deduplica por primera aparicion sin perder un orden estable."""

    seen: set[MemoryWriteIssueCode] = set()
    merged: list[MemoryWriteIssueCode] = []
    for group in groups:
        for reason in group:
            if reason in seen:
                continue
            seen.add(reason)
            merged.append(reason)
    return tuple(merged)
