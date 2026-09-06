"""Ledger operacional durable para identidad request/action/outcome.

Este modulo no es memoria cognitiva ni telemetria. SQLite es la autoridad local
para reclamar transacciones, conservar identidades de accion y deduplicar
outcomes mediante constraints atomicos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any


LEDGER_SCHEMA_VERSION = 1
MAX_JSON_BYTES = 64 * 1024
_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "access_token",
    "password",
    "secret",
    "prompt",
    "thought",
    "pensamiento",
    "contexto_llm",
    "memory_context",
)


class ActionLedgerError(RuntimeError):
    """Fallo de almacenamiento o contrato del ledger."""


class TransactionIncompleteError(ActionLedgerError):
    """La transaccion ya fue reclamada y no tiene completion durable."""


class TransactionConflictError(ActionLedgerError):
    """Un ID canonico fue reutilizado con contenido incompatible."""


class TransactionClaimStatus(str, Enum):
    CREATED = "created"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    CONFLICT = "conflict"


class ActionRecordStatus(str, Enum):
    CREATED = "created"
    EXISTING = "existing"
    CONFLICT = "conflict"


class OutcomeRecordStatus(str, Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    MISMATCH = "mismatch"


class TransactionCompletionStatus(str, Enum):
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class OutcomeConsumptionStatus(str, Enum):
    CONSUMED = "consumed"
    ALREADY_CONSUMED = "already_consumed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TransactionRecord:
    transaction_id: str
    origin: str
    event_type: str
    status: str
    action_id: str | None
    response_action: dict[str, Any] | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class TransactionClaimResult:
    status: TransactionClaimStatus
    transaction: TransactionRecord


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    transaction_id: str
    action_name: str
    parameters: dict[str, Any]
    requires_confirmation: bool
    task_instance_id: str | None
    intended_client: str
    outcome_expected: bool
    created_at: str


@dataclass(frozen=True)
class ActionRecordResult:
    status: ActionRecordStatus
    action: ActionRecord | None


@dataclass(frozen=True)
class OutcomeRecord:
    action_id: str
    outcome_transaction_id: str
    success: bool
    action_name: str
    payload: dict[str, Any]
    payload_fingerprint: str
    received_at: str
    consumed_at: str | None


@dataclass(frozen=True)
class OutcomeRecordResult:
    status: OutcomeRecordStatus
    action: ActionRecord | None
    outcome: OutcomeRecord | None


@dataclass(frozen=True)
class OutcomeAttemptRecord:
    outcome_transaction_id: str
    action_id: str
    classification: OutcomeRecordStatus
    payload_fingerprint: str
    received_at: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _clean_id(value: object, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{field} no puede estar vacio")
    return cleaned


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key).strip().lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def _sanitize_operational(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        raise ValueError("Payload operacional excede la profundidad permitida")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 4000:
            return value[:4000]
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Payload operacional contiene un float no finito")
        return value
    if isinstance(value, datetime):
        current = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 100:
                break
            if _is_sensitive_key(key):
                continue
            result[str(key)] = _sanitize_operational(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_sanitize_operational(item, depth=depth + 1) for item in value[:100]]
    raise TypeError(f"Tipo no permitido en payload operacional: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
    encoded = json.dumps(
        _sanitize_operational(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValueError("Payload operacional excede el limite durable")
    return encoded


def _decode_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


class ActionLedger:
    """Owner de una conexion SQLite local y thread-safe."""

    def __init__(self, db_path: str | Path = ":memory:"):
        raw_path = str(db_path)
        if raw_path != ":memory:":
            path = Path(raw_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            raw_path = str(path)
        self.db_path = raw_path
        self._lock = threading.RLock()
        self._closed = False
        try:
            self._connection = sqlite3.connect(
                raw_path,
                check_same_thread=False,
                isolation_level=None,
                timeout=10.0,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 10000")
            if raw_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._initialize_schema()
        except (OSError, sqlite3.Error) as exc:
            raise ActionLedgerError("No se pudo abrir el action ledger") from exc

    def _initialize_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS ledger_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY,
            origin TEXT NOT NULL,
            event_type TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('incomplete', 'completed')),
            action_id TEXT,
            response_json TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS actions (
            action_id TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL UNIQUE,
            action_name TEXT NOT NULL,
            parameters_json TEXT NOT NULL,
            requires_confirmation INTEGER NOT NULL CHECK(requires_confirmation IN (0, 1)),
            task_instance_id TEXT,
            intended_client TEXT NOT NULL,
            outcome_expected INTEGER NOT NULL CHECK(outcome_expected IN (0, 1)),
            created_at TEXT NOT NULL,
            FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
        );
        CREATE TABLE IF NOT EXISTS outcomes (
            action_id TEXT PRIMARY KEY,
            outcome_transaction_id TEXT NOT NULL UNIQUE,
            success INTEGER NOT NULL CHECK(success IN (0, 1)),
            action_name TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            payload_fingerprint TEXT NOT NULL,
            received_at TEXT NOT NULL,
            consumed_at TEXT,
            FOREIGN KEY(action_id) REFERENCES actions(action_id)
        );
        CREATE TABLE IF NOT EXISTS outcome_attempts (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_transaction_id TEXT NOT NULL UNIQUE,
            action_id TEXT NOT NULL,
            classification TEXT NOT NULL,
            payload_fingerprint TEXT NOT NULL,
            received_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_transactions_action_id ON transactions(action_id);
        CREATE INDEX IF NOT EXISTS idx_outcome_attempts_action_id ON outcome_attempts(action_id);
        """
        with self._lock:
            self._connection.executescript(schema)
            self._connection.execute(
                "INSERT OR IGNORE INTO ledger_meta(key, value) VALUES('schema_version', ?)",
                (str(LEDGER_SCHEMA_VERSION),),
            )
            row = self._connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None or row["value"] != str(LEDGER_SCHEMA_VERSION):
                raise ActionLedgerError("Version de action ledger incompatible")

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.execute("COMMIT")

    def _rollback(self) -> None:
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def claim_transaction(
        self,
        transaction_id: str,
        *,
        origin: str,
        event_type: str,
    ) -> TransactionClaimResult:
        transaction_id = _clean_id(transaction_id, "transaction_id")
        origin = _clean_id(origin, "origin")
        event_type = _clean_id(event_type, "event_type")
        created_at = _utc_now()
        with self._lock:
            try:
                self._begin()
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO transactions(
                        transaction_id, origin, event_type, status, created_at
                    ) VALUES(?, ?, ?, 'incomplete', ?)
                    """,
                    (transaction_id, origin, event_type, created_at),
                )
                row = self._connection.execute(
                    "SELECT * FROM transactions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                self._commit()
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo reclamar la transaccion") from exc
        if row is None:
            raise ActionLedgerError("El ledger no devolvio la transaccion reclamada")
        record = self._transaction_from_row(row)
        if cursor.rowcount == 1:
            status = TransactionClaimStatus.CREATED
        elif record.origin != origin or record.event_type != event_type:
            status = TransactionClaimStatus.CONFLICT
        elif record.status == "completed":
            status = TransactionClaimStatus.COMPLETED
        else:
            status = TransactionClaimStatus.INCOMPLETE
        return TransactionClaimResult(status=status, transaction=record)

    def get_transaction(self, transaction_id: str) -> TransactionRecord | None:
        transaction_id = _clean_id(transaction_id, "transaction_id")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return self._transaction_from_row(row) if row is not None else None

    def record_action(
        self,
        *,
        action_id: str,
        transaction_id: str,
        action_name: str,
        parameters: dict[str, Any] | None,
        requires_confirmation: bool,
        task_instance_id: str | None,
        intended_client: str,
        outcome_expected: bool,
    ) -> ActionRecordResult:
        action_id = _clean_id(action_id, "action_id")
        transaction_id = _clean_id(transaction_id, "transaction_id")
        action_name = _clean_id(action_name, "action_name")
        intended_client = _clean_id(intended_client, "intended_client")
        task_instance_id = str(task_instance_id).strip() if task_instance_id else None
        parameters_json = _canonical_json(parameters or {})
        created_at = _utc_now()
        expected = {
            "action_id": action_id,
            "transaction_id": transaction_id,
            "action_name": action_name,
            "parameters_json": parameters_json,
            "requires_confirmation": int(requires_confirmation),
            "task_instance_id": task_instance_id,
            "intended_client": intended_client,
            "outcome_expected": int(outcome_expected),
        }
        with self._lock:
            try:
                self._begin()
                transaction = self._connection.execute(
                    "SELECT status FROM transactions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                existing_by_tx = self._connection.execute(
                    "SELECT * FROM actions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                existing_by_id = self._connection.execute(
                    "SELECT * FROM actions WHERE action_id = ?",
                    (action_id,),
                ).fetchone()
                if transaction is None or transaction["status"] != "incomplete":
                    self._commit()
                    return ActionRecordResult(ActionRecordStatus.CONFLICT, None)
                existing = existing_by_tx or existing_by_id
                if existing is not None:
                    record = self._action_from_row(existing)
                    exact = all(existing[key] == value for key, value in expected.items())
                    self._commit()
                    return ActionRecordResult(
                        ActionRecordStatus.EXISTING if exact else ActionRecordStatus.CONFLICT,
                        record,
                    )
                self._connection.execute(
                    """
                    INSERT INTO actions(
                        action_id, transaction_id, action_name, parameters_json,
                        requires_confirmation, task_instance_id, intended_client,
                        outcome_expected, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_id,
                        transaction_id,
                        action_name,
                        parameters_json,
                        int(requires_confirmation),
                        task_instance_id,
                        intended_client,
                        int(outcome_expected),
                        created_at,
                    ),
                )
                self._connection.execute(
                    "UPDATE transactions SET action_id = ? WHERE transaction_id = ?",
                    (action_id, transaction_id),
                )
                row = self._connection.execute(
                    "SELECT * FROM actions WHERE action_id = ?", (action_id,)
                ).fetchone()
                self._commit()
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo persistir la identidad de accion") from exc
        return ActionRecordResult(ActionRecordStatus.CREATED, self._action_from_row(row))

    def get_action(self, action_id: str) -> ActionRecord | None:
        action_id = _clean_id(action_id, "action_id")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        return self._action_from_row(row) if row is not None else None

    def get_action_for_transaction(self, transaction_id: str) -> ActionRecord | None:
        transaction_id = _clean_id(transaction_id, "transaction_id")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM actions WHERE transaction_id = ?", (transaction_id,)
            ).fetchone()
        return self._action_from_row(row) if row is not None else None

    def record_outcome(
        self,
        *,
        action_id: str,
        outcome_transaction_id: str,
        success: bool,
        action_name: str | None,
        payload: dict[str, Any] | None,
        originating_transaction_id: str | None = None,
    ) -> OutcomeRecordResult:
        action_id = _clean_id(action_id, "action_id")
        outcome_transaction_id = _clean_id(outcome_transaction_id, "outcome_transaction_id")
        if type(success) is not bool:
            raise TypeError("success debe ser bool estricto")
        supplied_name = str(action_name or "").strip()
        supplied_transaction = str(originating_transaction_id or "").strip()
        payload_json = _canonical_json(payload or {})
        received_at = _utc_now()
        with self._lock:
            try:
                self._begin()
                action_row = self._connection.execute(
                    "SELECT * FROM actions WHERE action_id = ?", (action_id,)
                ).fetchone()
                prior_attempt = self._connection.execute(
                    "SELECT * FROM outcome_attempts WHERE outcome_transaction_id = ?",
                    (outcome_transaction_id,),
                ).fetchone()
                if prior_attempt is not None:
                    current_fingerprint = self._outcome_fingerprint(
                        success,
                        supplied_name
                        or (action_row["action_name"] if action_row is not None else ""),
                        payload_json,
                    )
                    same_attempt = (
                        prior_attempt["action_id"] == action_id
                        and prior_attempt["payload_fingerprint"] == current_fingerprint
                    )
                    recorded_action_id = prior_attempt["action_id"]
                    recorded_action_row = self._connection.execute(
                        "SELECT * FROM actions WHERE action_id = ?", (recorded_action_id,)
                    ).fetchone()
                    outcome_row = self._connection.execute(
                        "SELECT * FROM outcomes WHERE action_id = ?", (recorded_action_id,)
                    ).fetchone()
                    self._commit()
                    prior_status = (
                        OutcomeRecordStatus(prior_attempt["classification"])
                        if same_attempt
                        else OutcomeRecordStatus.CONFLICT
                    )
                    return OutcomeRecordResult(
                        prior_status,
                        self._action_from_row(recorded_action_row)
                        if recorded_action_row is not None
                        else None,
                        self._outcome_from_row(outcome_row) if outcome_row is not None else None,
                    )

                if action_row is None:
                    fingerprint = self._outcome_fingerprint(success, supplied_name, payload_json)
                    self._insert_attempt(
                        outcome_transaction_id, action_id, OutcomeRecordStatus.UNKNOWN,
                        fingerprint, received_at,
                    )
                    self._commit()
                    return OutcomeRecordResult(OutcomeRecordStatus.UNKNOWN, None, None)

                action = self._action_from_row(action_row)
                canonical_name = supplied_name or action.action_name
                fingerprint = self._outcome_fingerprint(success, canonical_name, payload_json)
                if (
                    (supplied_name and supplied_name != action.action_name)
                    or (
                        supplied_transaction
                        and supplied_transaction != action.transaction_id
                    )
                ):
                    self._insert_attempt(
                        outcome_transaction_id, action_id, OutcomeRecordStatus.MISMATCH,
                        fingerprint, received_at,
                    )
                    self._commit()
                    return OutcomeRecordResult(OutcomeRecordStatus.MISMATCH, action, None)

                outcome_row = self._connection.execute(
                    "SELECT * FROM outcomes WHERE action_id = ?", (action_id,)
                ).fetchone()
                if outcome_row is not None:
                    status = (
                        OutcomeRecordStatus.DUPLICATE
                        if outcome_row["payload_fingerprint"] == fingerprint
                        else OutcomeRecordStatus.CONFLICT
                    )
                    self._insert_attempt(
                        outcome_transaction_id, action_id, status, fingerprint, received_at
                    )
                    self._commit()
                    return OutcomeRecordResult(status, action, self._outcome_from_row(outcome_row))

                self._connection.execute(
                    """
                    INSERT INTO outcomes(
                        action_id, outcome_transaction_id, success, action_name,
                        payload_json, payload_fingerprint, received_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_id,
                        outcome_transaction_id,
                        int(success),
                        canonical_name,
                        payload_json,
                        fingerprint,
                        received_at,
                    ),
                )
                self._insert_attempt(
                    outcome_transaction_id,
                    action_id,
                    OutcomeRecordStatus.ACCEPTED,
                    fingerprint,
                    received_at,
                )
                outcome_row = self._connection.execute(
                    "SELECT * FROM outcomes WHERE action_id = ?", (action_id,)
                ).fetchone()
                self._commit()
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo registrar el outcome") from exc
        return OutcomeRecordResult(
            OutcomeRecordStatus.ACCEPTED,
            self._action_from_row(action_row),
            self._outcome_from_row(outcome_row),
        )

    def mark_outcome_consumed(self, action_id: str) -> OutcomeConsumptionStatus:
        action_id = _clean_id(action_id, "action_id")
        consumed_at = _utc_now()
        with self._lock:
            try:
                self._begin()
                row = self._connection.execute(
                    "SELECT consumed_at FROM outcomes WHERE action_id = ?", (action_id,)
                ).fetchone()
                if row is None:
                    self._commit()
                    return OutcomeConsumptionStatus.UNKNOWN
                if row["consumed_at"]:
                    self._commit()
                    return OutcomeConsumptionStatus.ALREADY_CONSUMED
                self._connection.execute(
                    "UPDATE outcomes SET consumed_at = ? WHERE action_id = ? AND consumed_at IS NULL",
                    (consumed_at, action_id),
                )
                self._commit()
                return OutcomeConsumptionStatus.CONSUMED
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo marcar el outcome como consumido") from exc

    def get_outcome(self, action_id: str) -> OutcomeRecord | None:
        action_id = _clean_id(action_id, "action_id")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM outcomes WHERE action_id = ?", (action_id,)
            ).fetchone()
        return self._outcome_from_row(row) if row is not None else None

    def get_outcome_attempt(
        self, outcome_transaction_id: str
    ) -> OutcomeAttemptRecord | None:
        outcome_transaction_id = _clean_id(
            outcome_transaction_id, "outcome_transaction_id"
        )
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM outcome_attempts WHERE outcome_transaction_id = ?",
                (outcome_transaction_id,),
            ).fetchone()
        return self._outcome_attempt_from_row(row) if row is not None else None

    def complete_transaction(
        self,
        transaction_id: str,
        response_action: dict[str, Any] | None,
    ) -> TransactionCompletionStatus:
        transaction_id = _clean_id(transaction_id, "transaction_id")
        response_json = _canonical_json({"schema_version": 1, "action": response_action})
        action_id = None
        if isinstance(response_action, dict):
            action_id = str(response_action.get("id_accion") or "").strip() or None
        completed_at = _utc_now()
        with self._lock:
            try:
                self._begin()
                row = self._connection.execute(
                    "SELECT status, response_json FROM transactions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                if row is None:
                    self._commit()
                    return TransactionCompletionStatus.UNKNOWN
                if row["status"] == "completed":
                    status = (
                        TransactionCompletionStatus.ALREADY_COMPLETED
                        if row["response_json"] == response_json
                        else TransactionCompletionStatus.CONFLICT
                    )
                    self._commit()
                    return status
                self._connection.execute(
                    """
                    UPDATE transactions
                    SET status = 'completed', action_id = ?, response_json = ?, completed_at = ?
                    WHERE transaction_id = ? AND status = 'incomplete'
                    """,
                    (action_id, response_json, completed_at, transaction_id),
                )
                self._commit()
                return TransactionCompletionStatus.COMPLETED
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo completar la transaccion") from exc

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._connection.close()
                self._closed = True
            except sqlite3.Error as exc:
                raise ActionLedgerError("No se pudo cerrar el action ledger") from exc

    def _insert_attempt(
        self,
        transaction_id: str,
        action_id: str,
        status: OutcomeRecordStatus,
        fingerprint: str,
        received_at: str,
    ) -> None:
        self._connection.execute(
            """
            INSERT INTO outcome_attempts(
                outcome_transaction_id, action_id, classification,
                payload_fingerprint, received_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (transaction_id, action_id, status.value, fingerprint, received_at),
        )

    @staticmethod
    def _outcome_fingerprint(success: bool, action_name: str, payload_json: str) -> str:
        canonical = _canonical_json(
            {"success": success, "action_name": action_name, "payload": _decode_object(payload_json)}
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _transaction_from_row(row: sqlite3.Row) -> TransactionRecord:
        response_action = None
        if row["response_json"]:
            response = _decode_object(row["response_json"])
            action = response.get("action")
            if isinstance(action, dict):
                response_action = action
        return TransactionRecord(
            transaction_id=row["transaction_id"],
            origin=row["origin"],
            event_type=row["event_type"],
            status=row["status"],
            action_id=row["action_id"],
            response_action=response_action,
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    @staticmethod
    def _action_from_row(row: sqlite3.Row) -> ActionRecord:
        return ActionRecord(
            action_id=row["action_id"],
            transaction_id=row["transaction_id"],
            action_name=row["action_name"],
            parameters=_decode_object(row["parameters_json"]),
            requires_confirmation=bool(row["requires_confirmation"]),
            task_instance_id=row["task_instance_id"],
            intended_client=row["intended_client"],
            outcome_expected=bool(row["outcome_expected"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _outcome_from_row(row: sqlite3.Row) -> OutcomeRecord:
        return OutcomeRecord(
            action_id=row["action_id"],
            outcome_transaction_id=row["outcome_transaction_id"],
            success=bool(row["success"]),
            action_name=row["action_name"],
            payload=_decode_object(row["payload_json"]),
            payload_fingerprint=row["payload_fingerprint"],
            received_at=row["received_at"],
            consumed_at=row["consumed_at"],
        )

    @staticmethod
    def _outcome_attempt_from_row(row: sqlite3.Row) -> OutcomeAttemptRecord:
        return OutcomeAttemptRecord(
            outcome_transaction_id=row["outcome_transaction_id"],
            action_id=row["action_id"],
            classification=OutcomeRecordStatus(row["classification"]),
            payload_fingerprint=row["payload_fingerprint"],
            received_at=row["received_at"],
        )
