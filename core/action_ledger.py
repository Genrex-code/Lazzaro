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
import logging
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any

from core.logger import get_logger
from core.safe_error_logging import log_safe_boundary_failure
from core.transport_delivery import (
    DeliveryDirectiveV1,
    DeliveryReconciliationResultV1,
    DeliveryResolutionV1,
    DurableReceiptKindV1,
    DurableReceiptStatusV1,
    RequestIdentityRelationshipV1,
    classify_request_identity_relationship,
    validate_request_identity,
)

logger = get_logger(__name__)


LEDGER_SCHEMA_VERSION = 5
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


class TurnAbortedError(ActionLedgerError):
    """El turno termino en fallo limpio y no debe ejecutarse otra vez."""

    def __init__(self, failure_class: str = "TurnAborted"):
        self.failure_class = str(failure_class or "TurnAborted")
        super().__init__("El turno fallo de forma controlada; replay bloqueado")


class TurnReconciliationRequiredError(TransactionIncompleteError):
    """El turno tiene efectos parciales o ambiguos que requieren reconciliacion."""


class TurnProcessingError(ActionLedgerError):
    """Fallo central tipado sin exponer texto interno a la API."""

    def __init__(self, component: str, failure_class: str):
        self.component = str(component or "orchestrator")
        self.failure_class = str(failure_class or "TurnProcessingError")
        super().__init__(f"Fallo central del turno en {self.component}")


class TransactionConflictError(ActionLedgerError):
    """Un ID canonico fue reutilizado con contenido incompatible."""


class TransactionClaimStatus(str, Enum):
    CREATED = "created"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    CONFLICT = "conflict"
    UNVERIFIABLE = "unverifiable"


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
    INVALID_STATE = "invalid_state"
    INVALID_ATTESTATION = "invalid_attestation"


class OutcomeReplayStatus(str, Enum):
    MATCH = "match"
    CONFLICT = "conflict"
    MISSING_RECEIPT = "missing_receipt"


class ActionState(str, Enum):
    """Estados durables del lifecycle; no describen calidad material del efecto."""

    LEGACY_UNKNOWN = "legacy_unknown"
    CREATED = "created"
    AWAITING_AUTHORIZATION = "awaiting_authorization"
    AUTHORIZED = "authorized"
    DISPATCHED = "dispatched"
    EXECUTING = "executing"
    REPORTED_SUCCESS = "reported_success"
    REPORTED_FAILURE = "reported_failure"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class ActionTransitionEvent(str, Enum):
    ACTION_RECORDED = "action_recorded"
    REQUIRE_AUTHORIZATION = "require_authorization"
    AUTO_AUTHORIZE = "auto_authorize"
    AUTHORIZE = "authorize"
    REJECT = "reject"
    DISPATCH = "dispatch"
    START_EXECUTION = "start_execution"
    REPORT_SUCCESS = "report_success"
    REPORT_FAILURE = "report_failure"
    SKIP = "skip"


class TransitionClassification(str, Enum):
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    INVALID_TRANSITION = "invalid_transition"
    UNKNOWN_ACTION = "unknown_action"
    CONFLICT = "conflict"


class TransactionCompletionStatus(str, Enum):
    COMPLETED = "completed"
    ALREADY_COMPLETED = "already_completed"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class OutcomeConsumptionStatus(str, Enum):
    CONSUMED = "consumed"
    ALREADY_CONSUMED = "already_consumed"
    UNKNOWN = "unknown"


class TurnUoWStatus(str, Enum):
    LEGACY_UNKNOWN = "legacy_unknown"
    OPEN = "open"
    COMMITTED = "committed"
    ABORTED_CLEAN = "aborted_clean"
    PARTIAL = "partial"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class TurnEffectStatus(str, Enum):
    PLANNED = "planned"
    STARTED = "started"
    APPLIED = "applied"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"
    ROLLED_BACK = "rolled_back"
    SKIPPED = "skipped"


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
    request_identity_version: int | None = None
    request_fingerprint_sha256: str | None = None


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
    decision_valid: bool | None = None
    action_state: ActionState = ActionState.LEGACY_UNKNOWN
    state_updated_at: str | None = None
    authority_decision_id: str | None = None
    authority_reference: dict[str, Any] | None = None
    action_binding_sha256: str | None = None


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


@dataclass(frozen=True)
class OutcomeReplayValidation:
    status: OutcomeReplayStatus
    prior_classification: OutcomeRecordStatus | None


@dataclass(frozen=True)
class TurnUoWRecord:
    transaction_id: str
    status: TurnUoWStatus
    phase: str
    failure_class: str | None
    reconciliation_required: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TurnEffectRecord:
    transaction_id: str
    effect_key: str
    effect_kind: str
    phase: str
    status: TurnEffectStatus
    required: bool
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class ActionTransitionRecord:
    transition_id: int
    action_id: str
    state_before: ActionState | None
    state_after: ActionState
    event: ActionTransitionEvent | None
    receipt_id: str | None
    created_at: str


@dataclass(frozen=True)
class ActionTransitionResult:
    action_id: str
    state_before: ActionState | None
    state_after: ActionState | None
    classification: TransitionClassification
    timestamp: str
    receipt_id: str | None = None


@dataclass(frozen=True)
class TransitionReceiptRecord:
    receipt_id: str
    action_id: str
    event: ActionTransitionEvent
    fingerprint: str
    classification: TransitionClassification
    state_before: ActionState | None
    state_after: ActionState | None
    created_at: str


@dataclass(frozen=True)
class ExecutionPermit:
    """Permiso explícito emitido sólo para una acción durable en EXECUTING."""

    action_id: str
    state: ActionState
    issued_at: str


_ALLOWED_TRANSITIONS: dict[
    tuple[ActionState, ActionTransitionEvent], ActionState
] = {
    (ActionState.CREATED, ActionTransitionEvent.REQUIRE_AUTHORIZATION):
        ActionState.AWAITING_AUTHORIZATION,
    (ActionState.CREATED, ActionTransitionEvent.AUTO_AUTHORIZE): ActionState.AUTHORIZED,
    (ActionState.CREATED, ActionTransitionEvent.SKIP): ActionState.SKIPPED,
    (ActionState.AWAITING_AUTHORIZATION, ActionTransitionEvent.AUTHORIZE):
        ActionState.AUTHORIZED,
    (ActionState.AWAITING_AUTHORIZATION, ActionTransitionEvent.REJECT): ActionState.REJECTED,
    (ActionState.AUTHORIZED, ActionTransitionEvent.DISPATCH): ActionState.DISPATCHED,
    (ActionState.AUTHORIZED, ActionTransitionEvent.START_EXECUTION): ActionState.EXECUTING,
    (ActionState.DISPATCHED, ActionTransitionEvent.START_EXECUTION): ActionState.EXECUTING,
    (ActionState.EXECUTING, ActionTransitionEvent.REPORT_SUCCESS):
        ActionState.REPORTED_SUCCESS,
    (ActionState.EXECUTING, ActionTransitionEvent.REPORT_FAILURE):
        ActionState.REPORTED_FAILURE,
    # Compatibilidad temporal con clientes ROOT-003 que aún no emiten START_EXECUTION.
    (ActionState.DISPATCHED, ActionTransitionEvent.REPORT_SUCCESS):
        ActionState.REPORTED_SUCCESS,
    (ActionState.DISPATCHED, ActionTransitionEvent.REPORT_FAILURE):
        ActionState.REPORTED_FAILURE,
}

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
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                raw_path,
                check_same_thread=False,
                isolation_level=None,
                timeout=10.0,
            )
            self._connection = connection
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 10000")
            if raw_path != ":memory:":
                connection.execute("PRAGMA journal_mode = WAL")
            self._initialize_schema()
        except BaseException as exc:
            if connection is not None:
                try:
                    connection.close()
                except BaseException as cleanup_error:
                    try:
                        log_safe_boundary_failure(
                            logger,
                            component="action_ledger.open.rollback",
                            code="LEDGER_UNAVAILABLE",
                            error=cleanup_error,
                            level=logging.WARNING,
                        )
                    except Exception:
                        pass
            self._closed = True
            if isinstance(exc, (OSError, sqlite3.Error)):
                raise ActionLedgerError("No se pudo abrir el action ledger") from exc
            raise

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
            completed_at TEXT,
            request_identity_version INTEGER,
            request_fingerprint_sha256 TEXT
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
            decision_valid INTEGER CHECK(decision_valid IN (0, 1) OR decision_valid IS NULL),
            action_state TEXT NOT NULL DEFAULT 'legacy_unknown',
            state_updated_at TEXT,
            authority_decision_id TEXT,
            authority_reference_json TEXT,
            action_binding_sha256 TEXT,
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
            meta = self._connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()
            if meta is not None and meta["value"] == "1":
                self._migrate_v1_to_v2()
                meta = self._connection.execute(
                    "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
                ).fetchone()
            if meta is not None and meta["value"] == "2":
                self._migrate_v2_to_v3()
                meta = self._connection.execute(
                    "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
                ).fetchone()
            if meta is not None and meta["value"] == "3":
                self._migrate_v3_to_v4()
                meta = self._connection.execute(
                    "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
                ).fetchone()
            if meta is not None and meta["value"] == "4":
                self._migrate_v4_to_v5()
                meta = self._connection.execute(
                    "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
                ).fetchone()
            if meta is not None and meta["value"] != str(LEDGER_SCHEMA_VERSION):
                raise ActionLedgerError("Version de action ledger incompatible")
            self._create_v2_tables()
            self._create_v3_tables()
            self._connection.execute(
                "INSERT OR IGNORE INTO ledger_meta(key, value) VALUES('schema_version', ?)",
                (str(LEDGER_SCHEMA_VERSION),),
            )
            row = self._connection.execute(
                "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None or row["value"] != str(LEDGER_SCHEMA_VERSION):
                raise ActionLedgerError("Version de action ledger incompatible")

    def _migrate_v1_to_v2(self) -> None:
        """Migra sin inferir autorización, dispatch ni éxito de filas históricas."""
        try:
            self._begin()
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(actions)").fetchall()
            }
            if "decision_valid" not in columns:
                self._connection.execute(
                    "ALTER TABLE actions ADD COLUMN decision_valid INTEGER"
                )
            if "action_state" not in columns:
                self._connection.execute(
                    "ALTER TABLE actions ADD COLUMN action_state TEXT NOT NULL "
                    "DEFAULT 'legacy_unknown'"
                )
            if "state_updated_at" not in columns:
                self._connection.execute(
                    "ALTER TABLE actions ADD COLUMN state_updated_at TEXT"
                )
            self._connection.execute(
                "UPDATE actions SET action_state = 'legacy_unknown', "
                "decision_valid = NULL WHERE action_state IS NULL "
                "OR action_state = ''"
            )
            self._connection.execute(
                "UPDATE ledger_meta SET value = ? WHERE key = 'schema_version'",
                ("2",),
            )
            self._commit()
        except (OSError, sqlite3.Error):
            self._rollback()
            raise

    def _create_v2_tables(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS action_transitions (
                transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_id TEXT NOT NULL,
                state_before TEXT,
                state_after TEXT NOT NULL,
                event TEXT,
                receipt_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(action_id) REFERENCES actions(action_id)
            );
            CREATE TABLE IF NOT EXISTS transition_receipts (
                receipt_id TEXT PRIMARY KEY,
                action_id TEXT NOT NULL,
                event TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                classification TEXT NOT NULL,
                state_before TEXT,
                state_after TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_action_transitions_action_id
                ON action_transitions(action_id, transition_id);
            """
        )

    def _migrate_v2_to_v3(self) -> None:
        """Agrega el journal UoW sin inferir estado de turnos historicos."""
        try:
            self._begin()
            self._create_v3_tables()
            self._connection.execute(
                "UPDATE ledger_meta SET value = ? WHERE key = 'schema_version'",
                ("3",),
            )
            self._commit()
        except (OSError, sqlite3.Error):
            self._rollback()
            raise

    def _create_v3_tables(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_uows (
                transaction_id TEXT PRIMARY KEY,
                status TEXT NOT NULL CHECK(status IN (
                    'open', 'committed', 'aborted_clean', 'partial',
                    'reconciliation_required'
                )),
                phase TEXT NOT NULL,
                failure_class TEXT,
                reconciliation_required INTEGER NOT NULL
                    CHECK(reconciliation_required IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
            )
            """
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_effects (
                transaction_id TEXT NOT NULL,
                effect_key TEXT NOT NULL,
                effect_kind TEXT NOT NULL CHECK(effect_kind IN (
                    'event', 'decision', 'execution', 'outcome', 'bookkeeping'
                )),
                phase TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN (
                    'planned', 'started', 'applied', 'failed', 'ambiguous',
                    'rolled_back', 'skipped'
                )),
                required INTEGER NOT NULL CHECK(required IN (0, 1)),
                started_at TEXT,
                finished_at TEXT,
                PRIMARY KEY(transaction_id, effect_key),
                FOREIGN KEY(transaction_id) REFERENCES turn_uows(transaction_id)
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_effects_status "
            "ON turn_effects(transaction_id, status)"
        )

    def _migrate_v3_to_v4(self) -> None:
        """Agrega evidencia ROOT-029 sin reinterpretar acciones historicas."""
        try:
            self._begin()
            columns = {
                row["name"]
                for row in self._connection.execute("PRAGMA table_info(actions)").fetchall()
            }
            for name in (
                "authority_decision_id",
                "authority_reference_json",
                "action_binding_sha256",
            ):
                if name not in columns:
                    self._connection.execute(
                        f"ALTER TABLE actions ADD COLUMN {name} TEXT"
                    )
            self._connection.execute(
                "UPDATE ledger_meta SET value = ? WHERE key = 'schema_version'",
                ("4",),
            )
            self._commit()
        except (OSError, sqlite3.Error):
            self._rollback()
            raise

    def _migrate_v4_to_v5(self) -> None:
        """Agrega identidad ROOT-054 sin atribuir fingerprints a filas legacy."""
        try:
            self._begin()
            columns = {
                row["name"]
                for row in self._connection.execute(
                    "PRAGMA table_info(transactions)"
                ).fetchall()
            }
            if "request_identity_version" not in columns:
                self._connection.execute(
                    "ALTER TABLE transactions ADD COLUMN request_identity_version INTEGER"
                )
            if "request_fingerprint_sha256" not in columns:
                self._connection.execute(
                    "ALTER TABLE transactions ADD COLUMN request_fingerprint_sha256 TEXT"
                )
            self._connection.execute(
                "UPDATE ledger_meta SET value = ? WHERE key = 'schema_version'",
                (str(LEDGER_SCHEMA_VERSION),),
            )
            self._commit()
        except (OSError, sqlite3.Error):
            self._rollback()
            raise

    def _begin(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._connection.execute("COMMIT")

    def _rollback(self) -> None:
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    @staticmethod
    def _existing_transaction_claim_status(
        record: TransactionRecord,
        *,
        origin: str,
        event_type: str,
        identity_version: int | None,
        identity_fingerprint: str | None,
    ) -> TransactionClaimStatus:
        if record.origin != origin or record.event_type != event_type:
            return TransactionClaimStatus.CONFLICT
        relationship = classify_request_identity_relationship(
            stored_version=record.request_identity_version,
            stored_fingerprint=record.request_fingerprint_sha256,
            incoming_version=identity_version,
            incoming_fingerprint=identity_fingerprint,
        )
        if relationship is RequestIdentityRelationshipV1.VERIFIED_CONFLICT:
            return TransactionClaimStatus.CONFLICT
        if relationship is RequestIdentityRelationshipV1.UNVERIFIABLE:
            return TransactionClaimStatus.UNVERIFIABLE
        if record.status == "completed":
            return TransactionClaimStatus.COMPLETED
        return TransactionClaimStatus.INCOMPLETE

    def claim_transaction(
        self,
        transaction_id: str,
        *,
        origin: str,
        event_type: str,
        request_identity_version: int | None = None,
        request_fingerprint_sha256: str | None = None,
    ) -> TransactionClaimResult:
        transaction_id = _clean_id(transaction_id, "transaction_id")
        origin = _clean_id(origin, "origin")
        event_type = _clean_id(event_type, "event_type")
        identity_version, identity_fingerprint = validate_request_identity(
            request_identity_version,
            request_fingerprint_sha256,
        )
        created_at = _utc_now()
        with self._lock:
            try:
                self._begin()
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO transactions(
                        transaction_id, origin, event_type, status, created_at,
                        request_identity_version, request_fingerprint_sha256
                    ) VALUES(?, ?, ?, 'incomplete', ?, ?, ?)
                    """,
                    (
                        transaction_id,
                        origin,
                        event_type,
                        created_at,
                        identity_version,
                        identity_fingerprint,
                    ),
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
        else:
            status = self._existing_transaction_claim_status(
                record,
                origin=origin,
                event_type=event_type,
                identity_version=identity_version,
                identity_fingerprint=identity_fingerprint,
            )
        return TransactionClaimResult(status=status, transaction=record)

    def claim_turn_transaction(
        self,
        transaction_id: str,
        *,
        origin: str,
        event_type: str,
        phase: str = "claimed",
        request_identity_version: int | None = None,
        request_fingerprint_sha256: str | None = None,
    ) -> TransactionClaimResult:
        """Reclama identidad y crea la UoW nueva dentro del mismo COMMIT."""
        transaction_id = _clean_id(transaction_id, "transaction_id")
        origin = _clean_id(origin, "origin")
        event_type = _clean_id(event_type, "event_type")
        identity_version, identity_fingerprint = validate_request_identity(
            request_identity_version,
            request_fingerprint_sha256,
        )
        phase = _clean_id(phase, "phase")
        created_at = _utc_now()
        with self._lock:
            try:
                self._begin()
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO transactions(
                        transaction_id, origin, event_type, status, created_at,
                        request_identity_version, request_fingerprint_sha256
                    ) VALUES(?, ?, ?, 'incomplete', ?, ?, ?)
                    """,
                    (
                        transaction_id,
                        origin,
                        event_type,
                        created_at,
                        identity_version,
                        identity_fingerprint,
                    ),
                )
                if cursor.rowcount == 1:
                    self._connection.execute(
                        """
                        INSERT INTO turn_uows(
                            transaction_id, status, phase, failure_class,
                            reconciliation_required, created_at, updated_at
                        ) VALUES(?, 'open', ?, NULL, 0, ?, ?)
                        """,
                        (transaction_id, phase, created_at, created_at),
                    )
                row = self._connection.execute(
                    "SELECT * FROM transactions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                self._commit()
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo reclamar el turno atomico") from exc
        if row is None:
            raise ActionLedgerError("El ledger no devolvio el turno reclamado")
        record = self._transaction_from_row(row)
        if cursor.rowcount == 1:
            status = TransactionClaimStatus.CREATED
        else:
            status = self._existing_transaction_claim_status(
                record,
                origin=origin,
                event_type=event_type,
                identity_version=identity_version,
                identity_fingerprint=identity_fingerprint,
            )
        return TransactionClaimResult(status=status, transaction=record)

    def get_transaction(self, transaction_id: str) -> TransactionRecord | None:
        transaction_id = _clean_id(transaction_id, "transaction_id")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return self._transaction_from_row(row) if row is not None else None

    def reconcile_delivery(
        self,
        transaction_id: str,
        *,
        origin: str,
        event_type: str,
        request_identity_version: int | None,
        request_fingerprint_sha256: str | None,
    ) -> DeliveryReconciliationResultV1:
        """Proyecta evidencia durable sin reclamar, reintentar ni mutar el ledger."""

        transaction_id = _clean_id(transaction_id, "transaction_id")
        origin = _clean_id(origin, "origin")
        event_type = _clean_id(event_type, "event_type")
        identity_version, identity_fingerprint = validate_request_identity(
            request_identity_version,
            request_fingerprint_sha256,
        )
        record = self.get_transaction(transaction_id)
        if record is None:
            if identity_version is None:
                return DeliveryReconciliationResultV1(
                    transaction_id,
                    DeliveryResolutionV1.UNVERIFIABLE,
                    DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
                )
            return DeliveryReconciliationResultV1(
                transaction_id,
                DeliveryResolutionV1.NOT_FOUND,
                DeliveryDirectiveV1.RETRY_SAME_TX,
            )
        relationship = classify_request_identity_relationship(
            stored_version=record.request_identity_version,
            stored_fingerprint=record.request_fingerprint_sha256,
            incoming_version=identity_version,
            incoming_fingerprint=identity_fingerprint,
        )
        if relationship is RequestIdentityRelationshipV1.UNVERIFIABLE:
            return DeliveryReconciliationResultV1(
                transaction_id,
                DeliveryResolutionV1.UNVERIFIABLE,
                DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
            )
        if (
            record.origin != origin
            or record.event_type != event_type
            or relationship is RequestIdentityRelationshipV1.VERIFIED_CONFLICT
        ):
            return DeliveryReconciliationResultV1(
                transaction_id,
                DeliveryResolutionV1.REQUIRES_RECONCILIATION,
                DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
            )

        if record.status != "completed":
            uow = self.get_turn_uow(transaction_id)
            if uow is None:
                resolution = DeliveryResolutionV1.UNKNOWN
                directive = DeliveryDirectiveV1.REQUIRES_RECONCILIATION
            elif uow.status is TurnUoWStatus.OPEN:
                resolution = DeliveryResolutionV1.IN_PROGRESS
                directive = DeliveryDirectiveV1.WAIT
            elif uow.status is TurnUoWStatus.ABORTED_CLEAN:
                resolution = DeliveryResolutionV1.FAILED_TERMINAL
                directive = DeliveryDirectiveV1.TERMINAL
            else:
                resolution = DeliveryResolutionV1.REQUIRES_RECONCILIATION
                directive = DeliveryDirectiveV1.REQUIRES_RECONCILIATION
            return DeliveryReconciliationResultV1(
                transaction_id,
                resolution,
                directive,
            )

        if event_type in {"confirmacion_accion", "inicio_ejecucion_accion"}:
            receipt = self.get_transition_receipt(f"lifecycle:{transaction_id}")
            if receipt is None:
                return DeliveryReconciliationResultV1(
                    transaction_id,
                    DeliveryResolutionV1.REQUIRES_RECONCILIATION,
                    DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
                    DurableReceiptKindV1.ACTION_LIFECYCLE,
                    DurableReceiptStatusV1.MISSING,
                )
            if receipt.classification in {
                TransitionClassification.APPLIED,
                TransitionClassification.ALREADY_APPLIED,
            }:
                receipt_status = (
                    DurableReceiptStatusV1.APPLIED
                    if receipt.classification is TransitionClassification.APPLIED
                    else DurableReceiptStatusV1.ALREADY_APPLIED
                )
                if event_type == "confirmacion_accion":
                    action = self.get_action(receipt.action_id)
                    if action is None:
                        return DeliveryReconciliationResultV1(
                            transaction_id,
                            DeliveryResolutionV1.REQUIRES_RECONCILIATION,
                            DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
                            DurableReceiptKindV1.ACTION_LIFECYCLE,
                            DurableReceiptStatusV1.MISSING,
                        )
                    return DeliveryReconciliationResultV1(
                        transaction_id,
                        DeliveryResolutionV1.COMPLETED_WITH_ACTION,
                        DeliveryDirectiveV1.RESOLVED,
                        DurableReceiptKindV1.ACTION_LIFECYCLE,
                        receipt_status,
                        action_id=action.action_id,
                    )
                return DeliveryReconciliationResultV1(
                    transaction_id,
                    DeliveryResolutionV1.COMPLETED_NO_ACTION,
                    DeliveryDirectiveV1.RESOLVED,
                    DurableReceiptKindV1.ACTION_LIFECYCLE,
                    receipt_status,
                )
            return DeliveryReconciliationResultV1(
                transaction_id,
                DeliveryResolutionV1.FAILED_TERMINAL,
                DeliveryDirectiveV1.TERMINAL,
                DurableReceiptKindV1.ACTION_LIFECYCLE,
                DurableReceiptStatusV1.REJECTED,
            )

        if event_type == "resultado_accion":
            attempt = self.get_outcome_attempt(transaction_id)
            if attempt is None:
                return DeliveryReconciliationResultV1(
                    transaction_id,
                    DeliveryResolutionV1.REQUIRES_RECONCILIATION,
                    DeliveryDirectiveV1.REQUIRES_RECONCILIATION,
                    DurableReceiptKindV1.OUTCOME,
                    DurableReceiptStatusV1.MISSING,
                )
            if attempt.classification in {
                OutcomeRecordStatus.ACCEPTED,
                OutcomeRecordStatus.DUPLICATE,
            }:
                receipt_status = (
                    DurableReceiptStatusV1.ACCEPTED
                    if attempt.classification is OutcomeRecordStatus.ACCEPTED
                    else DurableReceiptStatusV1.DUPLICATE
                )
                return DeliveryReconciliationResultV1(
                    transaction_id,
                    DeliveryResolutionV1.COMPLETED_NO_ACTION,
                    DeliveryDirectiveV1.RESOLVED,
                    DurableReceiptKindV1.OUTCOME,
                    receipt_status,
                )
            return DeliveryReconciliationResultV1(
                transaction_id,
                DeliveryResolutionV1.FAILED_TERMINAL,
                DeliveryDirectiveV1.TERMINAL,
                DurableReceiptKindV1.OUTCOME,
                DurableReceiptStatusV1.REJECTED,
            )

        if record.response_action is not None:
            return DeliveryReconciliationResultV1(
                transaction_id,
                DeliveryResolutionV1.COMPLETED_WITH_ACTION,
                DeliveryDirectiveV1.RESOLVED,
                DurableReceiptKindV1.TRANSACTION,
                DurableReceiptStatusV1.COMMITTED,
                response_action=record.response_action,
            )
        return DeliveryReconciliationResultV1(
            transaction_id,
            DeliveryResolutionV1.COMPLETED_NO_ACTION,
            DeliveryDirectiveV1.RESOLVED,
            DurableReceiptKindV1.TRANSACTION,
            DurableReceiptStatusV1.COMMITTED,
        )

    def begin_turn_uow(self, transaction_id: str, *, phase: str = "claimed") -> TurnUoWRecord:
        """Crea el UoW de un claim nuevo sin inferir filas para turnos historicos."""
        transaction_id = _clean_id(transaction_id, "transaction_id")
        phase = _clean_id(phase, "phase")
        now = _utc_now()
        with self._lock:
            try:
                self._begin()
                transaction = self._connection.execute(
                    "SELECT status FROM transactions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                if transaction is None or transaction["status"] != "incomplete":
                    self._rollback()
                    raise ActionLedgerError("El UoW requiere una transaccion incomplete")
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO turn_uows(
                        transaction_id, status, phase, failure_class,
                        reconciliation_required, created_at, updated_at
                    ) VALUES(?, 'open', ?, NULL, 0, ?, ?)
                    """,
                    (transaction_id, phase, now, now),
                )
                row = self._connection.execute(
                    "SELECT * FROM turn_uows WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                self._commit()
            except ActionLedgerError:
                raise
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo iniciar el UoW del turno") from exc
        if row is None:
            raise ActionLedgerError("El ledger no devolvio el UoW iniciado")
        return self._turn_uow_from_row(row)

    def get_turn_uow(self, transaction_id: str) -> TurnUoWRecord | None:
        transaction_id = _clean_id(transaction_id, "transaction_id")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM turn_uows WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return self._turn_uow_from_row(row) if row is not None else None

    def get_turn_uow_status(self, transaction_id: str) -> TurnUoWStatus:
        """Distingue datos historicos sin fabricar una fila UoW retroactiva."""
        transaction_id = _clean_id(transaction_id, "transaction_id")
        with self._lock:
            transaction = self._connection.execute(
                "SELECT 1 FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
            row = self._connection.execute(
                "SELECT status FROM turn_uows WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        if row is not None:
            return TurnUoWStatus(row["status"])
        if transaction is not None:
            return TurnUoWStatus.LEGACY_UNKNOWN
        raise ActionLedgerError("Transaccion desconocida")

    def start_turn_effect(
        self,
        transaction_id: str,
        effect_key: str,
        *,
        effect_kind: str,
        phase: str,
        required: bool,
    ) -> TurnEffectRecord:
        """Persiste STARTED antes de ejecutar un efecto no atomico con SQLite."""
        transaction_id = _clean_id(transaction_id, "transaction_id")
        effect_key = _clean_id(effect_key, "effect_key")
        effect_kind = _clean_id(effect_kind, "effect_kind").lower()
        phase = _clean_id(phase, "phase")
        if effect_kind not in {"event", "decision", "execution", "outcome", "bookkeeping"}:
            raise ValueError("effect_kind no reconocido")
        if type(required) is not bool:
            raise TypeError("required debe ser bool estricto")
        now = _utc_now()
        with self._lock:
            try:
                self._begin()
                uow = self._connection.execute(
                    "SELECT status FROM turn_uows WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                if uow is None or uow["status"] != TurnUoWStatus.OPEN.value:
                    self._rollback()
                    raise ActionLedgerError("No se puede iniciar un efecto fuera de UoW OPEN")
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO turn_effects(
                        transaction_id, effect_key, effect_kind, phase, status,
                        required, started_at, finished_at
                    ) VALUES(?, ?, ?, ?, 'started', ?, ?, NULL)
                    """,
                    (transaction_id, effect_key, effect_kind, phase, int(required), now),
                )
                row = self._connection.execute(
                    "SELECT * FROM turn_effects WHERE transaction_id = ? AND effect_key = ?",
                    (transaction_id, effect_key),
                ).fetchone()
                self._connection.execute(
                    "UPDATE turn_uows SET phase = ?, updated_at = ? WHERE transaction_id = ?",
                    (phase, now, transaction_id),
                )
                self._commit()
            except ActionLedgerError:
                raise
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo iniciar el efecto del turno") from exc
        if row is None:
            raise ActionLedgerError("El ledger no devolvio el efecto iniciado")
        return self._turn_effect_from_row(row)

    def finish_turn_effect(
        self,
        transaction_id: str,
        effect_key: str,
        status: TurnEffectStatus,
    ) -> TurnEffectRecord:
        """Cierra un efecto; APPLIED nunca se presume antes del retorno del owner."""
        transaction_id = _clean_id(transaction_id, "transaction_id")
        effect_key = _clean_id(effect_key, "effect_key")
        status = TurnEffectStatus(status)
        if status in {TurnEffectStatus.PLANNED, TurnEffectStatus.STARTED}:
            raise ValueError("finish_turn_effect exige un estado terminal")
        now = _utc_now()
        with self._lock:
            try:
                self._begin()
                cursor = self._connection.execute(
                    """
                    UPDATE turn_effects
                    SET status = ?, finished_at = ?
                    WHERE transaction_id = ? AND effect_key = ? AND status = 'started'
                    """,
                    (status.value, now, transaction_id, effect_key),
                )
                row = self._connection.execute(
                    "SELECT * FROM turn_effects WHERE transaction_id = ? AND effect_key = ?",
                    (transaction_id, effect_key),
                ).fetchone()
                if cursor.rowcount == 1:
                    self._connection.execute(
                        "UPDATE turn_uows SET updated_at = ? "
                        "WHERE transaction_id = ? AND status = 'open'",
                        (now, transaction_id),
                    )
                self._commit()
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo cerrar el efecto del turno") from exc
        if row is None:
            raise ActionLedgerError("Efecto UoW desconocido")
        if cursor.rowcount == 0 and row["status"] != status.value:
            raise ActionLedgerError("Efecto UoW ya posee otro estado terminal")
        if cursor.rowcount not in {0, 1}:
            raise ActionLedgerError("Actualizacion de efecto UoW no determinista")
        return self._turn_effect_from_row(row)

    def get_turn_effects(self, transaction_id: str) -> list[TurnEffectRecord]:
        transaction_id = _clean_id(transaction_id, "transaction_id")
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM turn_effects WHERE transaction_id = ? ORDER BY started_at, effect_key",
                (transaction_id,),
            ).fetchall()
        return [self._turn_effect_from_row(row) for row in rows]

    def fail_turn_uow(
        self,
        transaction_id: str,
        *,
        status: TurnUoWStatus,
        failure_class: str,
    ) -> TurnUoWRecord:
        """Finaliza un intento fallido sin completar la identidad ROOT-003."""
        transaction_id = _clean_id(transaction_id, "transaction_id")
        status = TurnUoWStatus(status)
        if status not in {
            TurnUoWStatus.ABORTED_CLEAN,
            TurnUoWStatus.PARTIAL,
            TurnUoWStatus.RECONCILIATION_REQUIRED,
        }:
            raise ValueError("Estado final de fallo UoW invalido")
        failure_class = _clean_id(failure_class, "failure_class")[:160]
        now = _utc_now()
        reconciliation = status in {
            TurnUoWStatus.PARTIAL,
            TurnUoWStatus.RECONCILIATION_REQUIRED,
        }
        with self._lock:
            try:
                self._begin()
                if status == TurnUoWStatus.RECONCILIATION_REQUIRED:
                    self._connection.execute(
                        """
                        UPDATE turn_effects SET status = 'ambiguous', finished_at = ?
                        WHERE transaction_id = ? AND status = 'started'
                        """,
                        (now, transaction_id),
                    )
                cursor = self._connection.execute(
                    """
                    UPDATE turn_uows
                    SET status = ?, phase = 'failed', failure_class = ?,
                        reconciliation_required = ?, updated_at = ?
                    WHERE transaction_id = ? AND status = 'open'
                    """,
                    (status.value, failure_class, int(reconciliation), now, transaction_id),
                )
                row = self._connection.execute(
                    "SELECT * FROM turn_uows WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                self._commit()
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo finalizar el UoW fallido") from exc
        if row is None:
            raise ActionLedgerError("UoW desconocido")
        if cursor.rowcount not in {0, 1}:
            raise ActionLedgerError("Actualizacion UoW no determinista")
        return self._turn_uow_from_row(row)

    def mark_abandoned_turn_uow(self, transaction_id: str) -> TurnUoWRecord:
        """Convierte un OPEN recuperado tras el claim en estado no reintentable."""
        return self.fail_turn_uow(
            transaction_id,
            status=TurnUoWStatus.RECONCILIATION_REQUIRED,
            failure_class="AbandonedOpenTurn",
        )

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
        decision_valid: bool | None = None,
        authority_decision_id: str | None = None,
        authority_reference: dict[str, Any] | None = None,
        action_binding_sha256: str | None = None,
    ) -> ActionRecordResult:
        action_id = _clean_id(action_id, "action_id")
        transaction_id = _clean_id(transaction_id, "transaction_id")
        action_name = _clean_id(action_name, "action_name")
        intended_client = _clean_id(intended_client, "intended_client")
        task_instance_id = str(task_instance_id).strip() if task_instance_id else None
        parameters_json = _canonical_json(parameters or {})
        authority_decision_id = (
            _clean_id(authority_decision_id, "authority_decision_id")
            if authority_decision_id is not None
            else None
        )
        action_binding_sha256 = (
            _clean_id(action_binding_sha256, "action_binding_sha256")
            if action_binding_sha256 is not None
            else None
        )
        authority_reference_json = (
            _canonical_json(authority_reference)
            if authority_reference is not None
            else None
        )
        supplied_authority = (
            authority_decision_id is not None,
            authority_reference_json is not None,
            action_binding_sha256 is not None,
        )
        if any(supplied_authority) and not all(supplied_authority):
            raise ValueError("authority binding fields must be supplied together")
        if decision_valid is not None and type(decision_valid) is not bool:
            raise TypeError("decision_valid debe ser bool estricto o None")
        created_at = _utc_now()
        initial_state = (
            ActionState.CREATED
            if decision_valid is not None
            else ActionState.LEGACY_UNKNOWN
        )
        expected = {
            "action_id": action_id,
            "transaction_id": transaction_id,
            "action_name": action_name,
            "parameters_json": parameters_json,
            "requires_confirmation": int(requires_confirmation),
            "task_instance_id": task_instance_id,
            "intended_client": intended_client,
            "outcome_expected": int(outcome_expected),
            "decision_valid": None if decision_valid is None else int(decision_valid),
            "action_state": initial_state.value,
            "authority_decision_id": authority_decision_id,
            "authority_reference_json": authority_reference_json,
            "action_binding_sha256": action_binding_sha256,
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
                        outcome_expected, created_at, decision_valid, action_state,
                        state_updated_at, authority_decision_id,
                        authority_reference_json, action_binding_sha256
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        None if decision_valid is None else int(decision_valid),
                        initial_state.value,
                        created_at if initial_state == ActionState.CREATED else None,
                        authority_decision_id,
                        authority_reference_json,
                        action_binding_sha256,
                    ),
                )
                if initial_state == ActionState.CREATED:
                    self._connection.execute(
                        """
                        INSERT INTO action_transitions(
                            action_id, state_before, state_after, event,
                            receipt_id, created_at
                        ) VALUES(?, NULL, ?, ?, NULL, ?)
                        """,
                        (
                            action_id,
                            ActionState.CREATED.value,
                            ActionTransitionEvent.ACTION_RECORDED.value,
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

    def get_action_state(self, action_id: str) -> ActionState | None:
        action = self.get_action(action_id)
        return action.action_state if action is not None else None

    def get_action_transitions(self, action_id: str) -> list[ActionTransitionRecord]:
        action_id = _clean_id(action_id, "action_id")
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM action_transitions WHERE action_id = ? "
                "ORDER BY transition_id",
                (action_id,),
            ).fetchall()
        return [self._transition_from_row(row) for row in rows]

    def get_transition_receipt(self, receipt_id: str) -> TransitionReceiptRecord | None:
        receipt_id = _clean_id(receipt_id, "receipt_id")
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM transition_receipts WHERE receipt_id = ?",
                (receipt_id,),
            ).fetchone()
        if row is None:
            return None
        return TransitionReceiptRecord(
            receipt_id=row["receipt_id"],
            action_id=row["action_id"],
            event=ActionTransitionEvent(row["event"]),
            fingerprint=row["fingerprint"],
            classification=TransitionClassification(row["classification"]),
            state_before=self._optional_state(row["state_before"]),
            state_after=self._optional_state(row["state_after"]),
            created_at=row["created_at"],
        )

    def transition_action(
        self,
        action_id: str,
        event: ActionTransitionEvent | str,
        *,
        receipt_id: str | None = None,
    ) -> ActionTransitionResult:
        """Aplica una transición completa dentro de un único BEGIN IMMEDIATE."""
        action_id = _clean_id(action_id, "action_id")
        event = ActionTransitionEvent(event)
        if event == ActionTransitionEvent.ACTION_RECORDED:
            raise ValueError("action_recorded sólo puede producirlo record_action")
        cleaned_receipt = (
            _clean_id(receipt_id, "receipt_id") if receipt_id is not None else None
        )
        timestamp = _utc_now()
        fingerprint = sha256(
            f"{action_id}\0{event.value}".encode("utf-8")
        ).hexdigest()
        with self._lock:
            try:
                self._begin()
                if cleaned_receipt is not None:
                    prior = self._connection.execute(
                        "SELECT * FROM transition_receipts WHERE receipt_id = ?",
                        (cleaned_receipt,),
                    ).fetchone()
                    if prior is not None:
                        self._commit()
                        if (
                            prior["action_id"] != action_id
                            or prior["fingerprint"] != fingerprint
                        ):
                            return ActionTransitionResult(
                                action_id=action_id,
                                state_before=self._optional_state(prior["state_before"]),
                                state_after=self._optional_state(prior["state_after"]),
                                classification=TransitionClassification.CONFLICT,
                                timestamp=prior["created_at"],
                                receipt_id=cleaned_receipt,
                            )
                        prior_classification = TransitionClassification(
                            prior["classification"]
                        )
                        return ActionTransitionResult(
                            action_id=action_id,
                            state_before=self._optional_state(prior["state_before"]),
                            state_after=self._optional_state(prior["state_after"]),
                            classification=(
                                TransitionClassification.ALREADY_APPLIED
                                if prior_classification
                                in {
                                    TransitionClassification.APPLIED,
                                    TransitionClassification.ALREADY_APPLIED,
                                }
                                else prior_classification
                            ),
                            timestamp=prior["created_at"],
                            receipt_id=cleaned_receipt,
                        )

                action_row = self._connection.execute(
                    "SELECT * FROM actions WHERE action_id = ?", (action_id,)
                ).fetchone()
                if action_row is None:
                    result = ActionTransitionResult(
                        action_id,
                        None,
                        None,
                        TransitionClassification.UNKNOWN_ACTION,
                        timestamp,
                        cleaned_receipt,
                    )
                    self._insert_transition_receipt_locked(
                        result, event, fingerprint
                    )
                    self._commit()
                    return result

                before = ActionState(action_row["action_state"])
                target = _ALLOWED_TRANSITIONS.get((before, event))
                if target is None:
                    prior_applied = self._connection.execute(
                        "SELECT 1 FROM action_transitions "
                        "WHERE action_id = ? AND event = ? LIMIT 1",
                        (action_id, event.value),
                    ).fetchone()
                    terminal = before in {
                        ActionState.REPORTED_SUCCESS,
                        ActionState.REPORTED_FAILURE,
                        ActionState.REJECTED,
                        ActionState.SKIPPED,
                    }
                    classification = (
                        TransitionClassification.ALREADY_APPLIED
                        if prior_applied is not None and not terminal
                        else TransitionClassification.INVALID_TRANSITION
                    )
                    result = ActionTransitionResult(
                        action_id,
                        before,
                        before,
                        classification,
                        timestamp,
                        cleaned_receipt,
                    )
                    self._insert_transition_receipt_locked(
                        result, event, fingerprint
                    )
                    self._commit()
                    return result

                self._connection.execute(
                    "UPDATE actions SET action_state = ?, state_updated_at = ? "
                    "WHERE action_id = ? AND action_state = ?",
                    (target.value, timestamp, action_id, before.value),
                )
                self._connection.execute(
                    """
                    INSERT INTO action_transitions(
                        action_id, state_before, state_after, event,
                        receipt_id, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?)
                    """,
                    (
                        action_id,
                        before.value,
                        target.value,
                        event.value,
                        cleaned_receipt,
                        timestamp,
                    ),
                )
                result = ActionTransitionResult(
                    action_id,
                    before,
                    target,
                    TransitionClassification.APPLIED,
                    timestamp,
                    cleaned_receipt,
                )
                self._insert_transition_receipt_locked(result, event, fingerprint)
                self._commit()
                return result
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo aplicar la transición") from exc

    def issue_execution_permit(self, action_id: str) -> ExecutionPermit | None:
        """Emite un permit descriptivo sólo si el ledger ya está en EXECUTING."""
        action_id = _clean_id(action_id, "action_id")
        with self._lock:
            row = self._connection.execute(
                "SELECT action_state, state_updated_at FROM actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        if row is None or row["action_state"] != ActionState.EXECUTING.value:
            return None
        return ExecutionPermit(
            action_id=action_id,
            state=ActionState.EXECUTING,
            issued_at=row["state_updated_at"] or _utc_now(),
        )

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
                    canonical_transaction = supplied_transaction or (
                        action_row["transaction_id"] if action_row is not None else ""
                    )
                    current_fingerprint = self._outcome_fingerprint(
                        success,
                        supplied_name
                        or (action_row["action_name"] if action_row is not None else ""),
                        payload_json,
                        originating_transaction_id=canonical_transaction,
                    )
                    legacy_fingerprint = self._outcome_fingerprint(
                        success,
                        supplied_name
                        or (action_row["action_name"] if action_row is not None else ""),
                        payload_json,
                    )
                    legacy_match_is_safe = (
                        prior_attempt["payload_fingerprint"] == legacy_fingerprint
                        and (
                            not supplied_transaction
                            or (
                                action_row is not None
                                and supplied_transaction == action_row["transaction_id"]
                            )
                        )
                    )
                    same_attempt = (
                        prior_attempt["action_id"] == action_id
                        and (
                            prior_attempt["payload_fingerprint"] == current_fingerprint
                            or legacy_match_is_safe
                        )
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
                    fingerprint = self._outcome_fingerprint(
                        success,
                        supplied_name,
                        payload_json,
                        originating_transaction_id=supplied_transaction,
                    )
                    self._insert_attempt(
                        outcome_transaction_id, action_id, OutcomeRecordStatus.UNKNOWN,
                        fingerprint, received_at,
                    )
                    self._commit()
                    return OutcomeRecordResult(OutcomeRecordStatus.UNKNOWN, None, None)

                action = self._action_from_row(action_row)
                canonical_name = supplied_name or action.action_name
                canonical_transaction = supplied_transaction or action.transaction_id
                fingerprint = self._outcome_fingerprint(
                    success,
                    canonical_name,
                    payload_json,
                    originating_transaction_id=canonical_transaction,
                )
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
                    legacy_fingerprint = self._outcome_fingerprint(
                        success,
                        canonical_name,
                        payload_json,
                    )
                    status = (
                        OutcomeRecordStatus.DUPLICATE
                        if outcome_row["payload_fingerprint"]
                        in {fingerprint, legacy_fingerprint}
                        else OutcomeRecordStatus.CONFLICT
                    )
                    self._insert_attempt(
                        outcome_transaction_id, action_id, status, fingerprint, received_at
                    )
                    self._commit()
                    return OutcomeRecordResult(status, action, self._outcome_from_row(outcome_row))

                transition_event = (
                    ActionTransitionEvent.REPORT_SUCCESS
                    if success
                    else ActionTransitionEvent.REPORT_FAILURE
                )
                current_state = action.action_state
                terminal_state = _ALLOWED_TRANSITIONS.get(
                    (current_state, transition_event)
                )
                if (
                    current_state != ActionState.LEGACY_UNKNOWN
                    and terminal_state is None
                ):
                    self._insert_attempt(
                        outcome_transaction_id,
                        action_id,
                        OutcomeRecordStatus.INVALID_STATE,
                        fingerprint,
                        received_at,
                    )
                    self._commit()
                    return OutcomeRecordResult(
                        OutcomeRecordStatus.INVALID_STATE,
                        action,
                        None,
                    )

                if terminal_state is not None:
                    receipt_id = f"outcome:{outcome_transaction_id}"
                    self._connection.execute(
                        "UPDATE actions SET action_state = ?, state_updated_at = ? "
                        "WHERE action_id = ? AND action_state = ?",
                        (
                            terminal_state.value,
                            received_at,
                            action_id,
                            current_state.value,
                        ),
                    )
                    self._connection.execute(
                        """
                        INSERT INTO action_transitions(
                            action_id, state_before, state_after, event,
                            receipt_id, created_at
                        ) VALUES(?, ?, ?, ?, ?, ?)
                        """,
                        (
                            action_id,
                            current_state.value,
                            terminal_state.value,
                            transition_event.value,
                            receipt_id,
                            received_at,
                        ),
                    )
                    transition_fingerprint = sha256(
                        f"{action_id}\0{transition_event.value}".encode("utf-8")
                    ).hexdigest()
                    self._insert_transition_receipt_locked(
                        ActionTransitionResult(
                            action_id,
                            current_state,
                            terminal_state,
                            TransitionClassification.APPLIED,
                            received_at,
                            receipt_id,
                        ),
                        transition_event,
                        transition_fingerprint,
                    )

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
                action_row = self._connection.execute(
                    "SELECT * FROM actions WHERE action_id = ?", (action_id,)
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

    def validate_outcome_replay(
        self,
        *,
        action_id: str,
        outcome_transaction_id: str,
        success: bool,
        action_name: str | None,
        payload: dict[str, Any] | None,
        originating_transaction_id: str | None = None,
    ) -> OutcomeReplayValidation:
        """Compara un replay completado sin crear ni modificar estado durable."""

        action_id = _clean_id(action_id, "action_id")
        outcome_transaction_id = _clean_id(
            outcome_transaction_id, "outcome_transaction_id"
        )
        if type(success) is not bool:
            raise TypeError("success debe ser bool estricto")
        supplied_name = str(action_name or "").strip()
        supplied_transaction = str(originating_transaction_id or "").strip()
        payload_json = _canonical_json(payload or {})

        with self._lock:
            prior_attempt = self._connection.execute(
                "SELECT * FROM outcome_attempts WHERE outcome_transaction_id = ?",
                (outcome_transaction_id,),
            ).fetchone()
            if prior_attempt is None:
                return OutcomeReplayValidation(
                    OutcomeReplayStatus.MISSING_RECEIPT,
                    None,
                )

            prior_classification = OutcomeRecordStatus(
                prior_attempt["classification"]
            )
            if prior_attempt["action_id"] != action_id:
                return OutcomeReplayValidation(
                    OutcomeReplayStatus.CONFLICT,
                    prior_classification,
                )

            action_row = self._connection.execute(
                "SELECT * FROM actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
            canonical_name = supplied_name or (
                action_row["action_name"] if action_row is not None else ""
            )
            canonical_transaction = supplied_transaction or (
                action_row["transaction_id"] if action_row is not None else ""
            )
            current_fingerprint = self._outcome_fingerprint(
                success,
                canonical_name,
                payload_json,
                originating_transaction_id=canonical_transaction,
            )
            legacy_fingerprint = self._outcome_fingerprint(
                success,
                canonical_name,
                payload_json,
            )
            legacy_match_is_safe = (
                prior_attempt["payload_fingerprint"] == legacy_fingerprint
                and (
                    not supplied_transaction
                    or (
                        action_row is not None
                        and supplied_transaction == action_row["transaction_id"]
                    )
                )
            )
            exact_fingerprint = (
                prior_attempt["payload_fingerprint"] == current_fingerprint
            )
            if not exact_fingerprint and not legacy_match_is_safe:
                return OutcomeReplayValidation(
                    OutcomeReplayStatus.CONFLICT,
                    prior_classification,
                )

            if (
                supplied_transaction
                and action_row is not None
                and supplied_transaction != action_row["transaction_id"]
                and not (
                    prior_classification is OutcomeRecordStatus.MISMATCH
                    and exact_fingerprint
                )
            ):
                return OutcomeReplayValidation(
                    OutcomeReplayStatus.CONFLICT,
                    prior_classification,
                )

            if prior_classification is OutcomeRecordStatus.ACCEPTED:
                outcome_row = self._connection.execute(
                    "SELECT * FROM outcomes WHERE action_id = ?",
                    (action_id,),
                ).fetchone()
                if (
                    outcome_row is None
                    or outcome_row["outcome_transaction_id"]
                    != outcome_transaction_id
                    or outcome_row["payload_fingerprint"]
                    != prior_attempt["payload_fingerprint"]
                ):
                    return OutcomeReplayValidation(
                        OutcomeReplayStatus.MISSING_RECEIPT,
                        prior_classification,
                    )

        return OutcomeReplayValidation(
            OutcomeReplayStatus.MATCH,
            prior_classification,
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

    def commit_turn_uow(
        self,
        transaction_id: str,
        response_action: dict[str, Any] | None,
    ) -> TransactionCompletionStatus:
        """Confirma UoW y completion ROOT-003 dentro de un solo COMMIT SQLite."""
        transaction_id = _clean_id(transaction_id, "transaction_id")
        response_json = _canonical_json({"schema_version": 1, "action": response_action})
        action_id = None
        if isinstance(response_action, dict):
            action_id = str(response_action.get("id_accion") or "").strip() or None
        completed_at = _utc_now()
        with self._lock:
            try:
                self._begin()
                transaction = self._connection.execute(
                    "SELECT status, response_json FROM transactions WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                uow = self._connection.execute(
                    "SELECT status FROM turn_uows WHERE transaction_id = ?",
                    (transaction_id,),
                ).fetchone()
                if transaction is None or uow is None:
                    self._commit()
                    return TransactionCompletionStatus.UNKNOWN
                if transaction["status"] == "completed":
                    status = (
                        TransactionCompletionStatus.ALREADY_COMPLETED
                        if transaction["response_json"] == response_json
                        and uow["status"] == TurnUoWStatus.COMMITTED.value
                        else TransactionCompletionStatus.CONFLICT
                    )
                    self._commit()
                    return status
                if uow["status"] != TurnUoWStatus.OPEN.value:
                    self._commit()
                    return TransactionCompletionStatus.CONFLICT
                unresolved = self._connection.execute(
                    """
                    SELECT effect_key FROM turn_effects
                    WHERE transaction_id = ? AND required = 1
                      AND status NOT IN ('applied', 'skipped')
                    LIMIT 1
                    """,
                    (transaction_id,),
                ).fetchone()
                if unresolved is not None:
                    self._commit()
                    return TransactionCompletionStatus.CONFLICT
                self._connection.execute(
                    """
                    UPDATE transactions
                    SET status = 'completed', action_id = ?, response_json = ?, completed_at = ?
                    WHERE transaction_id = ? AND status = 'incomplete'
                    """,
                    (action_id, response_json, completed_at, transaction_id),
                )
                self._connection.execute(
                    """
                    UPDATE turn_uows
                    SET status = 'committed', phase = 'committed', failure_class = NULL,
                        reconciliation_required = 0, updated_at = ?
                    WHERE transaction_id = ? AND status = 'open'
                    """,
                    (completed_at, transaction_id),
                )
                self._commit()
                return TransactionCompletionStatus.COMPLETED
            except (OSError, sqlite3.Error) as exc:
                self._rollback()
                raise ActionLedgerError("No se pudo confirmar el UoW del turno") from exc

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

    def _insert_transition_receipt_locked(
        self,
        result: ActionTransitionResult,
        event: ActionTransitionEvent,
        fingerprint: str,
    ) -> None:
        if result.receipt_id is None:
            return
        self._connection.execute(
            """
            INSERT INTO transition_receipts(
                receipt_id, action_id, event, fingerprint, classification,
                state_before, state_after, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.receipt_id,
                result.action_id,
                event.value,
                fingerprint,
                result.classification.value,
                result.state_before.value if result.state_before is not None else None,
                result.state_after.value if result.state_after is not None else None,
                result.timestamp,
            ),
        )

    @staticmethod
    def _optional_state(value: str | None) -> ActionState | None:
        return ActionState(value) if value else None

    @staticmethod
    def _outcome_fingerprint(
        success: bool,
        action_name: str,
        payload_json: str,
        *,
        originating_transaction_id: str | None = None,
    ) -> str:
        identity = {
            "success": success,
            "action_name": action_name,
            "payload": _decode_object(payload_json),
        }
        if originating_transaction_id is not None:
            identity["originating_transaction_id"] = originating_transaction_id
        canonical = _canonical_json(identity)
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
            request_identity_version=row["request_identity_version"],
            request_fingerprint_sha256=row["request_fingerprint_sha256"],
        )

    @staticmethod
    def _turn_uow_from_row(row: sqlite3.Row) -> TurnUoWRecord:
        return TurnUoWRecord(
            transaction_id=row["transaction_id"],
            status=TurnUoWStatus(row["status"]),
            phase=row["phase"],
            failure_class=row["failure_class"],
            reconciliation_required=bool(row["reconciliation_required"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _turn_effect_from_row(row: sqlite3.Row) -> TurnEffectRecord:
        return TurnEffectRecord(
            transaction_id=row["transaction_id"],
            effect_key=row["effect_key"],
            effect_kind=row["effect_kind"],
            phase=row["phase"],
            status=TurnEffectStatus(row["status"]),
            required=bool(row["required"]),
            started_at=row["started_at"],
            finished_at=row["finished_at"],
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
            decision_valid=(
                None if row["decision_valid"] is None else bool(row["decision_valid"])
            ),
            action_state=ActionState(row["action_state"]),
            state_updated_at=row["state_updated_at"],
            authority_decision_id=row["authority_decision_id"],
            authority_reference=(
                _decode_object(row["authority_reference_json"])
                if row["authority_reference_json"]
                else None
            ),
            action_binding_sha256=row["action_binding_sha256"],
        )

    @staticmethod
    def _transition_from_row(row: sqlite3.Row) -> ActionTransitionRecord:
        return ActionTransitionRecord(
            transition_id=int(row["transition_id"]),
            action_id=row["action_id"],
            state_before=(
                ActionState(row["state_before"]) if row["state_before"] else None
            ),
            state_after=ActionState(row["state_after"]),
            event=(ActionTransitionEvent(row["event"]) if row["event"] else None),
            receipt_id=row["receipt_id"],
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
