"""Journal SQLite dedicado para la saga de escrituras de memoria ROOT-015."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Callable
import uuid

from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure

from memory.memory_write_types import (
    MemoryWriteIssueCode,
    MemoryWriteKind,
    MemoryWriteState,
    MemoryWriteStatus,
    merge_write_issue_codes,
)


SCHEMA_VERSION = 1
logger = get_logger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class MemoryWriteOperation:
    operation_id: str
    operation_kind: MemoryWriteKind
    logical_key: str | None
    target_id: str
    target_timestamp_utc: str
    intent_fingerprint: str
    expected_fingerprint: str
    state: MemoryWriteState
    final_status: MemoryWriteStatus | None
    reason_codes: tuple[MemoryWriteIssueCode, ...]
    source_ids: tuple[str, ...]
    remaining_source_ids: tuple[str, ...]
    matched_count: int
    changed_count: int
    reconciliation_required: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MemoryWriteClaim:
    operation: MemoryWriteOperation
    replayed: bool
    logical_adoption: bool
    conflict: bool = False


class MemoryWriteLedger:
    """Owner explicito de una conexion SQLite; no crea archivos al importar."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    @property
    def in_transaction(self) -> bool:
        connection = self._connection
        return bool(connection is not None and connection.in_transaction)

    def open(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            if self.path != ":memory:":
                Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                timeout=30.0,
                isolation_level=None,
                check_same_thread=False,
            )
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 30000")
                if self.path != ":memory:":
                    connection.execute("PRAGMA journal_mode = WAL")
                self._connection = connection
                self._initialize_schema()
            except BaseException:
                self._connection = None
                try:
                    connection.close()
                except BaseException as cleanup_error:
                    try:
                        log_safe_boundary_failure(
                            logger,
                            component="memory_write_ledger.open.rollback",
                            code="LEDGER_UNAVAILABLE",
                            error=cleanup_error,
                            level=logging.WARNING,
                        )
                    except Exception:
                        pass
                raise

    def close(self) -> None:
        with self._lock:
            connection = self._connection
            self._connection = None
            if connection is not None:
                connection.close()

    def _require_connection(self) -> sqlite3.Connection:
        connection = self._connection
        if connection is None:
            raise RuntimeError("memory write ledger is not open")
        return connection

    def _initialize_schema(self) -> None:
        connection = self._require_connection()
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS memory_write_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_write_operations (
                operation_id TEXT PRIMARY KEY,
                operation_kind TEXT NOT NULL,
                logical_key TEXT,
                target_id TEXT NOT NULL,
                target_timestamp_utc TEXT NOT NULL,
                intent_fingerprint TEXT NOT NULL,
                expected_fingerprint TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL,
                final_status TEXT,
                reason_codes_json TEXT NOT NULL DEFAULT '[]',
                source_ids_json TEXT,
                remaining_source_ids_json TEXT,
                matched_count INTEGER NOT NULL DEFAULT 0,
                changed_count INTEGER NOT NULL DEFAULT 0,
                reconciliation_required INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_memory_write_logical_identity
                ON memory_write_operations(operation_kind, logical_key)
                WHERE logical_key IS NOT NULL;
            CREATE TABLE IF NOT EXISTS memory_write_keys (
                idempotency_key TEXT PRIMARY KEY,
                operation_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(operation_id) REFERENCES memory_write_operations(operation_id)
            );
            INSERT OR IGNORE INTO memory_write_meta(key, value)
                VALUES ('schema_version', '1');
            COMMIT;
            """
        )

    def claim(
        self,
        *,
        operation_kind: MemoryWriteKind,
        idempotency_key: str,
        intent_fingerprint: str,
        logical_key: str | None = None,
        source_ids: tuple[str, ...] = (),
        target_id: str | None = None,
        target_timestamp_utc: str | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], str] | None = None,
    ) -> MemoryWriteClaim:
        """Reclama key, logical identity y target en una transaccion corta."""

        with self._lock:
            connection = self._require_connection()
            now = (clock or _utc_now)()
            try:
                connection.execute("BEGIN IMMEDIATE")
                keyed = connection.execute(
                    """
                    SELECT o.* FROM memory_write_keys k
                    JOIN memory_write_operations o ON o.operation_id = k.operation_id
                    WHERE k.idempotency_key = ?
                    """,
                    (idempotency_key,),
                ).fetchone()
                if keyed is not None:
                    operation = self._from_row(keyed)
                    connection.execute("COMMIT")
                    return MemoryWriteClaim(
                        operation=operation,
                        replayed=True,
                        logical_adoption=False,
                        conflict=operation.intent_fingerprint != intent_fingerprint,
                    )

                logical = None
                if logical_key is not None:
                    logical = connection.execute(
                        """
                        SELECT * FROM memory_write_operations
                        WHERE operation_kind = ? AND logical_key = ?
                        """,
                        (operation_kind.value, logical_key),
                    ).fetchone()
                if logical is not None:
                    operation = self._from_row(logical)
                    if operation.intent_fingerprint != intent_fingerprint:
                        connection.execute("COMMIT")
                        return MemoryWriteClaim(
                            operation=operation,
                            replayed=False,
                            logical_adoption=True,
                            conflict=True,
                        )
                    connection.execute(
                        "INSERT INTO memory_write_keys(idempotency_key, operation_id, created_at) VALUES (?, ?, ?)",
                        (idempotency_key, operation.operation_id, now),
                    )
                    connection.execute("COMMIT")
                    return MemoryWriteClaim(
                        operation=operation,
                        replayed=False,
                        logical_adoption=True,
                    )

                operation_id = (id_factory or (lambda: str(uuid.uuid4())))()
                stable_target_id = target_id or (id_factory or (lambda: str(uuid.uuid4())))()
                stable_timestamp = target_timestamp_utc or now
                canonical_sources = tuple(sorted({str(item) for item in source_ids if str(item)}))
                connection.execute(
                    """
                    INSERT INTO memory_write_operations(
                        operation_id, operation_kind, logical_key, target_id,
                        target_timestamp_utc, intent_fingerprint, expected_fingerprint,
                        state, final_status, reason_codes_json, source_ids_json,
                        remaining_source_ids_json, matched_count, changed_count,
                        reconciliation_required, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, '', ?, NULL, '[]', ?, ?, 0, 0, 0, ?, ?)
                    """,
                    (
                        operation_id,
                        operation_kind.value,
                        logical_key,
                        stable_target_id,
                        stable_timestamp,
                        intent_fingerprint,
                        MemoryWriteState.INTENT_RECORDED.value,
                        self._encode_ids(canonical_sources),
                        self._encode_ids(canonical_sources),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    "INSERT INTO memory_write_keys(idempotency_key, operation_id, created_at) VALUES (?, ?, ?)",
                    (idempotency_key, operation_id, now),
                )
                row = connection.execute(
                    "SELECT * FROM memory_write_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                connection.execute("COMMIT")
                assert row is not None
                return MemoryWriteClaim(
                    operation=self._from_row(row),
                    replayed=False,
                    logical_adoption=False,
                )
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    def get_by_key(self, idempotency_key: str) -> MemoryWriteOperation | None:
        with self._lock:
            row = self._require_connection().execute(
                """
                SELECT o.* FROM memory_write_keys k
                JOIN memory_write_operations o ON o.operation_id = k.operation_id
                WHERE k.idempotency_key = ?
                """,
                (idempotency_key,),
            ).fetchone()
            return self._from_row(row) if row is not None else None

    def get_operation(self, operation_id: str) -> MemoryWriteOperation | None:
        with self._lock:
            row = self._require_connection().execute(
                "SELECT * FROM memory_write_operations WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            return self._from_row(row) if row is not None else None

    def primary_key_for_operation(self, operation_id: str) -> str:
        """Devuelve una key estable para receipts emitidos por reconciliacion."""

        with self._lock:
            row = self._require_connection().execute(
                """
                SELECT idempotency_key FROM memory_write_keys
                WHERE operation_id = ?
                ORDER BY created_at, idempotency_key
                LIMIT 1
                """,
                (operation_id,),
            ).fetchone()
            return str(row["idempotency_key"]) if row is not None else ""

    def list_nonterminal(self, limit: int = 100) -> tuple[MemoryWriteOperation, ...]:
        with self._lock:
            rows = self._require_connection().execute(
                """
                SELECT * FROM memory_write_operations
                WHERE state NOT IN (?, ?)
                ORDER BY created_at, operation_id LIMIT ?
                """,
                (MemoryWriteState.COMMITTED.value, MemoryWriteState.FAILED.value, max(0, limit)),
            ).fetchall()
            return tuple(self._from_row(row) for row in rows)

    def set_expected_fingerprint(
        self,
        operation_id: str,
        expected_fingerprint: str,
    ) -> MemoryWriteOperation:
        return self.transition(operation_id, expected_fingerprint=expected_fingerprint)

    def transition(
        self,
        operation_id: str,
        *,
        state: MemoryWriteState | None = None,
        final_status: MemoryWriteStatus | None = None,
        reason_codes: tuple[MemoryWriteIssueCode, ...] = (),
        expected_fingerprint: str | None = None,
        matched_count: int | None = None,
        changed_count: int | None = None,
        reconciliation_required: bool | None = None,
        remaining_source_ids: tuple[str, ...] | None = None,
    ) -> MemoryWriteOperation:
        with self._lock:
            connection = self._require_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM memory_write_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                if row is None:
                    raise KeyError("unknown memory write operation")
                current = self._from_row(row)
                merged_reasons = merge_write_issue_codes(current.reason_codes, reason_codes)
                values = {
                    "state": (state or current.state).value,
                    "final_status": (
                        final_status.value
                        if final_status is not None
                        else (current.final_status.value if current.final_status else None)
                    ),
                    "reason_codes_json": json.dumps(
                        [reason.value for reason in merged_reasons], separators=(",", ":")
                    ),
                    "expected_fingerprint": (
                        current.expected_fingerprint
                        if expected_fingerprint is None
                        else expected_fingerprint
                    ),
                    "matched_count": current.matched_count if matched_count is None else matched_count,
                    "changed_count": current.changed_count if changed_count is None else changed_count,
                    "reconciliation_required": (
                        current.reconciliation_required
                        if reconciliation_required is None
                        else reconciliation_required
                    ),
                    "remaining_source_ids_json": (
                        self._encode_ids(current.remaining_source_ids)
                        if remaining_source_ids is None
                        else self._encode_ids(tuple(sorted(set(remaining_source_ids))))
                    ),
                    "updated_at": _utc_now(),
                }
                connection.execute(
                    """
                    UPDATE memory_write_operations SET
                        state=:state, final_status=:final_status,
                        reason_codes_json=:reason_codes_json,
                        expected_fingerprint=:expected_fingerprint,
                        matched_count=:matched_count, changed_count=:changed_count,
                        reconciliation_required=:reconciliation_required,
                        remaining_source_ids_json=:remaining_source_ids_json,
                        updated_at=:updated_at
                    WHERE operation_id=:operation_id
                    """,
                    {**values, "operation_id": operation_id},
                )
                updated = connection.execute(
                    "SELECT * FROM memory_write_operations WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                connection.execute("COMMIT")
                assert updated is not None
                return self._from_row(updated)
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise

    @staticmethod
    def _encode_ids(ids: tuple[str, ...]) -> str | None:
        if not ids:
            return None
        return json.dumps(list(ids), ensure_ascii=True, separators=(",", ":"))

    @staticmethod
    def _decode_ids(raw: str | None) -> tuple[str, ...]:
        if not raw:
            return ()
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return ()
        return tuple(str(item) for item in value) if isinstance(value, list) else ()

    @staticmethod
    def _decode_reasons(raw: str) -> tuple[MemoryWriteIssueCode, ...]:
        try:
            values = json.loads(raw or "[]")
        except (TypeError, ValueError):
            return ()
        reasons: list[MemoryWriteIssueCode] = []
        for value in values if isinstance(values, list) else []:
            try:
                reason = MemoryWriteIssueCode(value)
            except ValueError:
                continue
            if reason not in reasons:
                reasons.append(reason)
        return tuple(reasons)

    @classmethod
    def _from_row(cls, row: sqlite3.Row) -> MemoryWriteOperation:
        final_raw = row["final_status"]
        return MemoryWriteOperation(
            operation_id=str(row["operation_id"]),
            operation_kind=MemoryWriteKind(row["operation_kind"]),
            logical_key=str(row["logical_key"]) if row["logical_key"] is not None else None,
            target_id=str(row["target_id"]),
            target_timestamp_utc=str(row["target_timestamp_utc"]),
            intent_fingerprint=str(row["intent_fingerprint"]),
            expected_fingerprint=str(row["expected_fingerprint"]),
            state=MemoryWriteState(row["state"]),
            final_status=MemoryWriteStatus(final_raw) if final_raw else None,
            reason_codes=cls._decode_reasons(str(row["reason_codes_json"])),
            source_ids=cls._decode_ids(row["source_ids_json"]),
            remaining_source_ids=cls._decode_ids(row["remaining_source_ids_json"]),
            matched_count=int(row["matched_count"]),
            changed_count=int(row["changed_count"]),
            reconciliation_required=bool(row["reconciliation_required"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
