"""Saga idempotente y verificacion exacta para mutaciones de memoria ROOT-015."""

from __future__ import annotations

from collections.abc import Callable
from hashlib import sha256
import json
import threading
from typing import Any, ClassVar

from core.logger import get_logger
from memory.memory_types import MemoryDocument
from memory.memory_write_ledger import MemoryWriteLedger, MemoryWriteOperation
from memory.memory_write_types import (
    ExactLookupReceipt,
    ExactLookupStatus,
    ExactMemoryRow,
    MemoryWriteIssueCode,
    MemoryWriteKind,
    MemoryWriteReceipt,
    MemoryWriteState,
    MemoryWriteStatus,
    merge_write_issue_codes,
)


logger = get_logger(__name__)


class MemoryWriteCoordinator:
    """Coordina journal, efecto Chroma y verificacion sin una transaccion distribuida."""

    KEY_VERSION = 1
    EFFECT_VERSION = 1
    _operation_locks_guard: ClassVar[threading.Lock] = threading.Lock()
    _operation_locks: ClassVar[dict[str, threading.RLock]] = {}

    def __init__(self, client: Any, ledger: MemoryWriteLedger) -> None:
        self.client = client
        self.ledger = ledger

    def close(self) -> None:
        self.ledger.close()

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def _digest(cls, prefix: str, value: Any) -> str:
        encoded = cls._canonical_json(value).encode("utf-8")
        return f"{prefix}:v{cls.KEY_VERSION}:{sha256(encoded).hexdigest()}"

    @classmethod
    def derive_idempotency_key(
        cls,
        operation_kind: MemoryWriteKind,
        source_identity: Any,
        *,
        namespace: str = "lazzaro.memory.write",
    ) -> str:
        return cls._digest(
            "mwk",
            {
                "version": cls.KEY_VERSION,
                "namespace": namespace,
                "operation_kind": operation_kind.value,
                "source_identity": source_identity,
            },
        )

    @classmethod
    def derive_logical_key(
        cls,
        operation_kind: MemoryWriteKind,
        source_identity: Any,
        *,
        namespace: str = "lazzaro.memory.logical",
    ) -> str:
        return cls._digest(
            "mwl",
            {
                "version": cls.KEY_VERSION,
                "namespace": namespace,
                "operation_kind": operation_kind.value,
                "source_identity": source_identity,
            },
        )

    @classmethod
    def intent_fingerprint(cls, operation_kind: MemoryWriteKind, intent: Any) -> str:
        return cls._digest(
            "mwi",
            {
                "version": cls.KEY_VERSION,
                "operation_kind": operation_kind.value,
                "intent": intent,
            },
        )

    @classmethod
    def effect_fingerprint(cls, document: MemoryDocument | ExactMemoryRow) -> str:
        if isinstance(document, MemoryDocument):
            memory_id = document.id
            content = document.contenido
            metadata = document.metadata
        else:
            memory_id = document.id
            content = document.document
            metadata = document.metadata
        return cls._digest(
            "mwe",
            {
                "version": cls.EFFECT_VERSION,
                "memory_kind": (metadata or {}).get("tipo"),
                "target_id": memory_id,
                "document": content,
                "persisted_metadata": metadata,
            },
        )

    @classmethod
    def _operation_lock(cls, operation_id: str) -> threading.RLock:
        with cls._operation_locks_guard:
            lock = cls._operation_locks.get(operation_id)
            if lock is None:
                lock = threading.RLock()
                cls._operation_locks[operation_id] = lock
            return lock

    def create_memory(
        self,
        *,
        operation_kind: MemoryWriteKind,
        source_identity: Any,
        intent: Any,
        build_document: Callable[[str, str], MemoryDocument],
        logical_identity: Any | None = None,
        legacy_candidates: Callable[[], ExactLookupReceipt] | None = None,
    ) -> MemoryWriteReceipt:
        return self._create_memory(
            operation_kind=operation_kind,
            source_identity=source_identity,
            intent=intent,
            build_document=build_document,
            logical_identity=logical_identity,
            legacy_candidates=legacy_candidates,
            finalize=True,
        )

    def _create_memory(
        self,
        *,
        operation_kind: MemoryWriteKind,
        source_identity: Any,
        intent: Any,
        build_document: Callable[[str, str], MemoryDocument],
        logical_identity: Any | None,
        legacy_candidates: Callable[[], ExactLookupReceipt] | None,
        finalize: bool,
        source_ids: tuple[str, ...] = (),
    ) -> MemoryWriteReceipt:
        try:
            idempotency_key = self.derive_idempotency_key(operation_kind, source_identity)
            logical_key = (
                self.derive_logical_key(operation_kind, logical_identity)
                if logical_identity is not None
                else None
            )
            intent_fingerprint = self.intent_fingerprint(operation_kind, intent)
        except (TypeError, ValueError, OverflowError):
            return self._invalid_receipt(operation_kind)

        existing = None
        try:
            existing = self.ledger.get_by_key(idempotency_key)
        except Exception as exc:
            self._log_failure(operation_kind, "claim_lookup", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
            return self._journal_claim_failure(operation_kind, idempotency_key)

        legacy = None
        if existing is None and legacy_candidates is not None:
            try:
                legacy = legacy_candidates()
            except Exception as exc:
                self._log_failure(operation_kind, "legacy_lookup", MemoryWriteIssueCode.BACKEND_FAILURE, exc)
                legacy = ExactLookupReceipt(status=ExactLookupStatus.FAILED)

        adopted_row = legacy.rows[0] if legacy is not None and len(legacy.rows) == 1 else None
        adopted_timestamp = None
        if adopted_row is not None and isinstance(adopted_row.metadata, dict):
            raw_timestamp = adopted_row.metadata.get("timestamp")
            if isinstance(raw_timestamp, str) and raw_timestamp:
                adopted_timestamp = raw_timestamp

        try:
            claim = self.ledger.claim(
                operation_kind=operation_kind,
                idempotency_key=idempotency_key,
                intent_fingerprint=intent_fingerprint,
                logical_key=logical_key,
                source_ids=source_ids,
                target_id=adopted_row.id if adopted_row is not None else None,
                target_timestamp_utc=adopted_timestamp,
            )
        except Exception as exc:
            self._log_failure(operation_kind, "claim", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
            return self._journal_claim_failure(operation_kind, idempotency_key)

        if claim.conflict:
            return MemoryWriteReceipt(
                operation_id=claim.operation.operation_id,
                idempotency_key=idempotency_key,
                operation_kind=operation_kind,
                status=MemoryWriteStatus.FAILED,
                target_id=claim.operation.target_id,
                reason_codes=(MemoryWriteIssueCode.IDEMPOTENCY_CONFLICT,),
                durable=claim.replayed,
                replayed=claim.replayed,
            )

        with self._operation_lock(claim.operation.operation_id):
            current = self.ledger.get_operation(claim.operation.operation_id) or claim.operation
            if current.state is MemoryWriteState.COMMITTED and current.final_status is not None:
                status = (
                    MemoryWriteStatus.ALREADY_EXISTS
                    if claim.logical_adoption and not claim.replayed
                    else current.final_status
                )
                return self._receipt(
                    current,
                    idempotency_key,
                    status=status,
                    replayed=claim.replayed,
                    changed_count=0,
                )
            if current.state is MemoryWriteState.FAILED and current.final_status is not None:
                return self._receipt(current, idempotency_key, replayed=claim.replayed)

            if existing is None and legacy is not None:
                if legacy.status is ExactLookupStatus.FAILED:
                    return self._mark_ambiguous(
                        current,
                        idempotency_key,
                        operation_kind,
                        (MemoryWriteIssueCode.BACKEND_FAILURE,),
                        replayed=claim.replayed,
                    )
                if len(legacy.rows) > 1:
                    return self._mark_ambiguous(
                        current,
                        idempotency_key,
                        operation_kind,
                        (MemoryWriteIssueCode.RECONCILIATION_REQUIRED,),
                        replayed=claim.replayed,
                    )
                if adopted_row is not None and current.target_id == adopted_row.id:
                    expected = self.effect_fingerprint(adopted_row)
                    try:
                        current = self.ledger.set_expected_fingerprint(current.operation_id, expected)
                        if finalize:
                            current = self.ledger.transition(
                                current.operation_id,
                                state=MemoryWriteState.COMMITTED,
                                final_status=MemoryWriteStatus.ALREADY_EXISTS,
                                reason_codes=(MemoryWriteIssueCode.LEGACY_EFFECT_ADOPTED,),
                            )
                            return self._receipt(
                                current,
                                idempotency_key,
                                replayed=claim.replayed,
                            )
                        current = self.ledger.transition(
                            current.operation_id,
                            state=MemoryWriteState.TARGET_VERIFIED,
                            reason_codes=(MemoryWriteIssueCode.LEGACY_EFFECT_ADOPTED,),
                        )
                        return self._receipt(
                            current,
                            idempotency_key,
                            status=MemoryWriteStatus.ALREADY_EXISTS,
                            replayed=claim.replayed,
                        )
                    except Exception as exc:
                        self._log_failure(operation_kind, "legacy_finalize", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
                        return self._ambiguous_after_journal_failure(
                            current, idempotency_key, operation_kind, claim.replayed
                        )

            if current.expected_fingerprint:
                recorded_target = self._lookup_exact((current.target_id,))
                if recorded_target.status is ExactLookupStatus.FAILED:
                    return self._mark_ambiguous(
                        current,
                        idempotency_key,
                        operation_kind,
                        (MemoryWriteIssueCode.VERIFICATION_FAILURE,),
                        replayed=claim.replayed,
                    )
                if recorded_target.rows:
                    if (
                        self.effect_fingerprint(recorded_target.rows[0])
                        != current.expected_fingerprint
                    ):
                        return self._mark_ambiguous(
                            current,
                            idempotency_key,
                            operation_kind,
                            (MemoryWriteIssueCode.TARGET_MISMATCH,),
                            replayed=claim.replayed,
                        )
                    prior_outcome = (
                        MemoryWriteStatus.ALREADY_EXISTS
                        if MemoryWriteIssueCode.LEGACY_EFFECT_ADOPTED
                        in current.reason_codes
                        else MemoryWriteStatus.CREATED
                    )
                    return self._finalize_verified_target(
                        current,
                        idempotency_key,
                        operation_kind,
                        replayed=claim.replayed,
                        finalize=finalize,
                        outcome=prior_outcome,
                        invocation_changed_count=0,
                    )

            try:
                document = build_document(current.target_id, current.target_timestamp_utc)
                if document.id != current.target_id:
                    raise ValueError("document target differs from durable target")
                expected = self.effect_fingerprint(document)
            except Exception as exc:
                self._log_failure(operation_kind, "build", MemoryWriteIssueCode.INVALID_INTENT, exc)
                return self._mark_failed(
                    current,
                    idempotency_key,
                    operation_kind,
                    (MemoryWriteIssueCode.INVALID_INTENT,),
                    replayed=claim.replayed,
                )

            if current.expected_fingerprint and current.expected_fingerprint != expected:
                return self._mark_failed(
                    current,
                    idempotency_key,
                    operation_kind,
                    (MemoryWriteIssueCode.IDEMPOTENCY_CONFLICT,),
                    replayed=claim.replayed,
                )
            try:
                current = self.ledger.set_expected_fingerprint(current.operation_id, expected)
            except Exception as exc:
                self._log_failure(operation_kind, "prepare", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
                return self._journal_after_claim_failure(current, idempotency_key, operation_kind, claim.replayed)

            before = self._lookup_exact((current.target_id,))
            if before.status is ExactLookupStatus.FAILED:
                return self._mark_ambiguous(
                    current,
                    idempotency_key,
                    operation_kind,
                    (MemoryWriteIssueCode.VERIFICATION_FAILURE,),
                    replayed=claim.replayed,
                )
            if before.rows:
                if self.effect_fingerprint(before.rows[0]) != expected:
                    return self._mark_ambiguous(
                        current,
                        idempotency_key,
                        operation_kind,
                        (MemoryWriteIssueCode.TARGET_MISMATCH,),
                        replayed=claim.replayed,
                    )
                return self._finalize_verified_target(
                    current,
                    idempotency_key,
                    operation_kind,
                    replayed=claim.replayed,
                    finalize=finalize,
                    outcome=MemoryWriteStatus.CREATED,
                    invocation_changed_count=0,
                )

            try:
                current = self.ledger.transition(
                    current.operation_id,
                    state=MemoryWriteState.WRITE_ATTEMPTED,
                )
            except Exception as exc:
                self._log_failure(operation_kind, "before_write", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
                return self._journal_after_claim_failure(current, idempotency_key, operation_kind, claim.replayed)

            backend_failed = False
            try:
                added = self.client.agregar(
                    documentos=[document.contenido],
                    metadatos=[document.metadata],
                    ids=[document.id],
                )
                backend_failed = added is not True
            except Exception as exc:
                backend_failed = True
                self._log_failure(operation_kind, "create", MemoryWriteIssueCode.BACKEND_FAILURE, exc)

            after = self._lookup_exact((current.target_id,))
            if after.status is ExactLookupStatus.FAILED:
                return self._mark_ambiguous(
                    current,
                    idempotency_key,
                    operation_kind,
                    (MemoryWriteIssueCode.VERIFICATION_FAILURE,),
                    replayed=claim.replayed,
                )
            if after.rows:
                if self.effect_fingerprint(after.rows[0]) != expected:
                    return self._mark_ambiguous(
                        current,
                        idempotency_key,
                        operation_kind,
                        (MemoryWriteIssueCode.TARGET_MISMATCH,),
                        replayed=claim.replayed,
                    )
                return self._finalize_verified_target(
                    current,
                    idempotency_key,
                    operation_kind,
                    replayed=claim.replayed,
                    finalize=finalize,
                    outcome=MemoryWriteStatus.CREATED,
                    invocation_changed_count=1,
                )

            reasons = (
                (MemoryWriteIssueCode.BACKEND_FAILURE,)
                if backend_failed
                else (MemoryWriteIssueCode.VERIFICATION_FAILURE,)
            )
            return self._mark_retryable_absence(
                current,
                idempotency_key,
                operation_kind,
                reasons,
                replayed=claim.replayed,
            )

    def consolidate_memory(
        self,
        *,
        source_ids: tuple[str, ...],
        intent: Any,
        build_document: Callable[[str, str], MemoryDocument],
        legacy_candidates: Callable[[], ExactLookupReceipt] | None = None,
    ) -> MemoryWriteReceipt:
        canonical_sources = tuple(sorted({str(item) for item in source_ids if str(item)}))
        source_identity = {
            "schema_policy_version": 1,
            "source_ids": canonical_sources,
        }
        interim = self._create_memory(
            operation_kind=MemoryWriteKind.CONSOLIDATION,
            source_identity=source_identity,
            intent=intent,
            build_document=build_document,
            logical_identity=source_identity,
            legacy_candidates=legacy_candidates,
            finalize=False,
            source_ids=canonical_sources,
        )
        if interim.status in {MemoryWriteStatus.FAILED, MemoryWriteStatus.AMBIGUOUS}:
            return interim
        operation = self.ledger.get_operation(interim.operation_id)
        if operation is None:
            return self._ambiguous_after_journal_failure(
                None,
                interim.idempotency_key,
                MemoryWriteKind.CONSOLIDATION,
                interim.replayed,
            )
        if operation.state is MemoryWriteState.COMMITTED:
            return interim

        with self._operation_lock(operation.operation_id):
            refreshed = self.ledger.get_operation(operation.operation_id)
            if refreshed is not None:
                operation = refreshed
            if operation.state is MemoryWriteState.COMMITTED and operation.final_status is not None:
                return self._receipt(
                    operation,
                    interim.idempotency_key,
                    replayed=True,
                    changed_count=0,
                )
            try:
                operation = self.ledger.transition(
                    operation.operation_id,
                    state=MemoryWriteState.CLEANUP_PENDING,
                )
            except Exception as exc:
                self._log_failure(MemoryWriteKind.CONSOLIDATION, "cleanup_prepare", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
                return self._ambiguous_after_journal_failure(
                    operation,
                    interim.idempotency_key,
                    MemoryWriteKind.CONSOLIDATION,
                    interim.replayed,
                )

            remaining_ids = operation.remaining_source_ids or operation.source_ids
            before = self._lookup_exact(remaining_ids)
            if before.status is ExactLookupStatus.FAILED:
                return self._mark_ambiguous(
                    operation,
                    interim.idempotency_key,
                    MemoryWriteKind.CONSOLIDATION,
                    (MemoryWriteIssueCode.VERIFICATION_FAILURE,),
                    replayed=interim.replayed,
                )
            present_before = tuple(row.id for row in before.rows)
            original_matched = operation.matched_count or len(present_before)
            if present_before:
                try:
                    self.client.eliminar(list(present_before))
                except Exception as exc:
                    self._log_failure(MemoryWriteKind.CONSOLIDATION, "cleanup", MemoryWriteIssueCode.BACKEND_FAILURE, exc)

            after = self._lookup_exact(remaining_ids)
            if after.status is ExactLookupStatus.FAILED:
                return self._mark_ambiguous(
                    operation,
                    interim.idempotency_key,
                    MemoryWriteKind.CONSOLIDATION,
                    (MemoryWriteIssueCode.VERIFICATION_FAILURE,),
                    replayed=interim.replayed,
                )
            still_present = tuple(row.id for row in after.rows)
            changed = max(0, original_matched - len(still_present))
            if still_present:
                try:
                    operation = self.ledger.transition(
                        operation.operation_id,
                        state=MemoryWriteState.RECONCILIATION_REQUIRED,
                        reason_codes=(
                            MemoryWriteIssueCode.PARTIAL_DELETE,
                            MemoryWriteIssueCode.RECONCILIATION_REQUIRED,
                        ),
                        matched_count=original_matched,
                        changed_count=changed,
                        reconciliation_required=True,
                        remaining_source_ids=still_present,
                    )
                except Exception:
                    pass
                return self._receipt(
                    operation,
                    interim.idempotency_key,
                    status=MemoryWriteStatus.AMBIGUOUS,
                    replayed=interim.replayed,
                    extra_reasons=(
                        MemoryWriteIssueCode.PARTIAL_DELETE,
                        MemoryWriteIssueCode.RECONCILIATION_REQUIRED,
                    ),
                )

            try:
                operation = self.ledger.transition(
                    operation.operation_id,
                    state=MemoryWriteState.COMMITTED,
                    final_status=interim.status,
                    matched_count=original_matched,
                    changed_count=changed,
                    reconciliation_required=False,
                    remaining_source_ids=(),
                )
            except Exception as exc:
                self._log_failure(MemoryWriteKind.CONSOLIDATION, "cleanup_finalize", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
                return self._ambiguous_after_journal_failure(
                    operation,
                    interim.idempotency_key,
                    MemoryWriteKind.CONSOLIDATION,
                    interim.replayed,
                )
            return self._receipt(
                operation,
                interim.idempotency_key,
                replayed=interim.replayed,
            )

    def update_memory_metadata(
        self,
        *,
        source_identity: Any,
        target_id: str,
        metadata: dict[str, Any],
    ) -> MemoryWriteReceipt:
        """UPDATE verificado: matched/changed salen de exact reads antes/despues."""
        kind = MemoryWriteKind.UPDATE
        try:
            key = self.derive_idempotency_key(kind, source_identity)
            intent_fp = self.intent_fingerprint(
                kind,
                {"target_id": target_id, "metadata": metadata},
            )
            claim = self.ledger.claim(
                operation_kind=kind,
                idempotency_key=key,
                intent_fingerprint=intent_fp,
                target_id=str(target_id),
            )
        except (TypeError, ValueError, OverflowError):
            return self._invalid_receipt(kind)
        except Exception as exc:
            self._log_failure(kind, "claim", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
            return self._journal_claim_failure(kind, locals().get("key", ""))
        if claim.conflict:
            return MemoryWriteReceipt(
                operation_id=claim.operation.operation_id,
                idempotency_key=key,
                operation_kind=kind,
                status=MemoryWriteStatus.FAILED,
                target_id=claim.operation.target_id,
                reason_codes=(MemoryWriteIssueCode.IDEMPOTENCY_CONFLICT,),
                durable=claim.replayed,
                replayed=claim.replayed,
            )
        with self._operation_lock(claim.operation.operation_id):
            operation = self.ledger.get_operation(claim.operation.operation_id) or claim.operation
            if operation.state is MemoryWriteState.COMMITTED and operation.final_status:
                return self._receipt(operation, key, replayed=True, changed_count=0)
            before = self._lookup_exact((operation.target_id,))
            if before.status is ExactLookupStatus.FAILED:
                return self._mark_ambiguous(
                    operation, key, kind, (MemoryWriteIssueCode.VERIFICATION_FAILURE,), replayed=claim.replayed
                )
            if not before.rows:
                try:
                    operation = self.ledger.transition(
                        operation.operation_id,
                        state=MemoryWriteState.FAILED,
                        final_status=MemoryWriteStatus.FAILED,
                        matched_count=0,
                        changed_count=0,
                        reason_codes=(MemoryWriteIssueCode.VERIFICATION_FAILURE,),
                    )
                    return self._receipt(operation, key, replayed=claim.replayed)
                except Exception:
                    return self._journal_after_claim_failure(
                        operation, key, kind, claim.replayed
                    )
            previous = before.rows[0]
            expected_metadata = dict(previous.metadata or {})
            expected_metadata.update(metadata)
            expected_row = ExactMemoryRow(
                id=previous.id,
                document=previous.document,
                metadata=expected_metadata,
            )
            expected = self.effect_fingerprint(expected_row)
            try:
                operation = self.ledger.set_expected_fingerprint(operation.operation_id, expected)
                operation = self.ledger.transition(
                    operation.operation_id,
                    state=MemoryWriteState.WRITE_ATTEMPTED,
                    matched_count=1,
                )
            except Exception:
                return self._journal_after_claim_failure(
                    operation, key, kind, claim.replayed
                )
            try:
                self.client.actualizar_metadatos(
                    ids=[operation.target_id],
                    metadatos=[dict(metadata)],
                )
            except Exception as exc:
                self._log_failure(kind, "update", MemoryWriteIssueCode.BACKEND_FAILURE, exc)
            after = self._lookup_exact((operation.target_id,))
            if after.status is ExactLookupStatus.FAILED:
                return self._mark_ambiguous(
                    operation, key, kind, (MemoryWriteIssueCode.VERIFICATION_FAILURE,), replayed=claim.replayed
                )
            if not after.rows or self.effect_fingerprint(after.rows[0]) != expected:
                return self._mark_ambiguous(
                    operation, key, kind, (MemoryWriteIssueCode.VERIFICATION_FAILURE,), replayed=claim.replayed
                )
            changed = int(self.effect_fingerprint(previous) != expected)
            try:
                operation = self.ledger.transition(
                    operation.operation_id,
                    state=MemoryWriteState.COMMITTED,
                    final_status=MemoryWriteStatus.CREATED,
                    matched_count=1,
                    changed_count=changed,
                    reconciliation_required=False,
                )
            except Exception:
                return self._ambiguous_after_journal_failure(
                    operation, key, kind, claim.replayed
                )
            return self._receipt(operation, key, replayed=claim.replayed)

    def delete_memories(
        self,
        *,
        source_identity: Any,
        ids: tuple[str, ...],
    ) -> MemoryWriteReceipt:
        """DELETE verificado; un ID ya ausente no cuenta como cambio nuevo."""
        kind = MemoryWriteKind.DELETE
        requested = tuple(sorted({str(item) for item in ids if str(item)}))
        try:
            key = self.derive_idempotency_key(kind, source_identity)
            intent_fp = self.intent_fingerprint(kind, {"target_ids": requested})
            claim = self.ledger.claim(
                operation_kind=kind,
                idempotency_key=key,
                intent_fingerprint=intent_fp,
                source_ids=requested,
            )
        except (TypeError, ValueError, OverflowError):
            return self._invalid_receipt(kind)
        except Exception as exc:
            self._log_failure(kind, "claim", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
            return self._journal_claim_failure(kind, locals().get("key", ""))
        if claim.conflict:
            return MemoryWriteReceipt(
                operation_id=claim.operation.operation_id,
                idempotency_key=key,
                operation_kind=kind,
                status=MemoryWriteStatus.FAILED,
                target_id=claim.operation.target_id,
                reason_codes=(MemoryWriteIssueCode.IDEMPOTENCY_CONFLICT,),
                durable=claim.replayed,
                replayed=claim.replayed,
            )
        with self._operation_lock(claim.operation.operation_id):
            operation = self.ledger.get_operation(claim.operation.operation_id) or claim.operation
            if operation.state is MemoryWriteState.COMMITTED and operation.final_status:
                return self._receipt(operation, key, replayed=True, changed_count=0)
            before = self._lookup_exact(requested)
            if before.status is ExactLookupStatus.FAILED:
                return self._mark_ambiguous(
                    operation, key, kind, (MemoryWriteIssueCode.VERIFICATION_FAILURE,), replayed=claim.replayed
                )
            present = tuple(row.id for row in before.rows)
            if not present:
                try:
                    operation = self.ledger.transition(
                        operation.operation_id,
                        state=MemoryWriteState.COMMITTED,
                        final_status=MemoryWriteStatus.ALREADY_EXISTS,
                        matched_count=0,
                        changed_count=0,
                    )
                    return self._receipt(operation, key, replayed=claim.replayed)
                except Exception:
                    return self._journal_after_claim_failure(
                        operation, key, kind, claim.replayed
                    )
            expected = self._digest("mwd", {"absent_ids": present})
            try:
                operation = self.ledger.set_expected_fingerprint(operation.operation_id, expected)
                operation = self.ledger.transition(
                    operation.operation_id,
                    state=MemoryWriteState.WRITE_ATTEMPTED,
                    matched_count=len(present),
                    remaining_source_ids=present,
                )
            except Exception:
                return self._journal_after_claim_failure(
                    operation, key, kind, claim.replayed
                )
            try:
                self.client.eliminar(list(present))
            except Exception as exc:
                self._log_failure(kind, "delete", MemoryWriteIssueCode.BACKEND_FAILURE, exc)
            after = self._lookup_exact(present)
            if after.status is ExactLookupStatus.FAILED:
                return self._mark_ambiguous(
                    operation, key, kind, (MemoryWriteIssueCode.VERIFICATION_FAILURE,), replayed=claim.replayed
                )
            remaining = tuple(row.id for row in after.rows)
            changed = len(present) - len(remaining)
            if remaining:
                try:
                    operation = self.ledger.transition(
                        operation.operation_id,
                        state=MemoryWriteState.RECONCILIATION_REQUIRED,
                        reason_codes=(
                            MemoryWriteIssueCode.PARTIAL_DELETE,
                            MemoryWriteIssueCode.RECONCILIATION_REQUIRED,
                        ),
                        matched_count=len(present),
                        changed_count=changed,
                        reconciliation_required=True,
                        remaining_source_ids=remaining,
                    )
                except Exception:
                    return self._ambiguous_after_journal_failure(
                        operation, key, kind, claim.replayed
                    )
                return self._receipt(
                    operation,
                    key,
                    status=MemoryWriteStatus.AMBIGUOUS,
                    replayed=claim.replayed,
                )
            try:
                operation = self.ledger.transition(
                    operation.operation_id,
                    state=MemoryWriteState.COMMITTED,
                    final_status=MemoryWriteStatus.CREATED,
                    matched_count=len(present),
                    changed_count=changed,
                    remaining_source_ids=(),
                    reconciliation_required=False,
                )
            except Exception:
                return self._ambiguous_after_journal_failure(
                    operation, key, kind, claim.replayed
                )
            return self._receipt(operation, key, replayed=claim.replayed)

    def reconcile_pending(self, limit: int = 100) -> tuple[MemoryWriteReceipt, ...]:
        """Reconcilia solo evidencia disponible; nunca reconstruye payload desde hashes."""

        receipts: list[MemoryWriteReceipt] = []
        try:
            operations = self.ledger.list_nonterminal(limit=limit)
        except Exception:
            return ()
        for operation in operations:
            try:
                idempotency_key = self.ledger.primary_key_for_operation(
                    operation.operation_id
                )
            except Exception:
                idempotency_key = ""
            lookup = self._lookup_exact((operation.target_id,))
            if lookup.status is ExactLookupStatus.FAILED:
                receipts.append(
                    self._receipt(
                        operation,
                        idempotency_key,
                        status=MemoryWriteStatus.AMBIGUOUS,
                        extra_reasons=(MemoryWriteIssueCode.VERIFICATION_FAILURE,),
                    )
                )
                continue
            if not lookup.rows or not operation.expected_fingerprint:
                # R1: queda reanudable y conserva target/timestamp; no se inventa payload.
                receipts.append(
                    self._receipt(
                        operation,
                        idempotency_key,
                        status=MemoryWriteStatus.AMBIGUOUS,
                        extra_reasons=(MemoryWriteIssueCode.RECONCILIATION_REQUIRED,),
                    )
                )
                continue
            if self.effect_fingerprint(lookup.rows[0]) != operation.expected_fingerprint:
                receipts.append(
                    self._mark_ambiguous(
                        operation,
                        idempotency_key,
                        operation.operation_kind,
                        (MemoryWriteIssueCode.TARGET_MISMATCH,),
                        replayed=False,
                    )
                )
                continue
            if operation.operation_kind is MemoryWriteKind.CONSOLIDATION:
                receipts.append(
                    self._receipt(
                        operation,
                        idempotency_key,
                        status=MemoryWriteStatus.AMBIGUOUS,
                        extra_reasons=(MemoryWriteIssueCode.RECONCILIATION_REQUIRED,),
                    )
                )
                continue
            try:
                operation = self.ledger.transition(
                    operation.operation_id,
                    state=MemoryWriteState.COMMITTED,
                    final_status=MemoryWriteStatus.CREATED,
                    reconciliation_required=False,
                )
                receipts.append(self._receipt(operation, idempotency_key))
            except Exception:
                receipts.append(
                    self._ambiguous_after_journal_failure(
                        operation,
                        idempotency_key,
                        operation.operation_kind,
                        False,
                    )
                )
        return tuple(receipts)

    def _lookup_exact(self, ids: tuple[str, ...]) -> ExactLookupReceipt:
        requested = tuple(dict.fromkeys(str(item) for item in ids if str(item)))
        if not requested:
            return ExactLookupReceipt(status=ExactLookupStatus.ABSENT, requested_ids=())
        exact = getattr(self.client, "obtener_por_ids", None)
        if callable(exact):
            try:
                result = exact(list(requested))
            except Exception as exc:
                self._log_failure(MemoryWriteKind.SEMANTIC, "exact_lookup", MemoryWriteIssueCode.VERIFICATION_FAILURE, exc)
                return ExactLookupReceipt(status=ExactLookupStatus.FAILED, requested_ids=requested)
            if isinstance(result, ExactLookupReceipt):
                return result

        # Adapter de compatibilidad para fakes/list stores: sigue siendo lectura exacta,
        # nunca query vectorial. El ChromaClient productivo siempre usa collection.get(ids=...).
        listar = getattr(self.client, "listar", None)
        if not callable(listar):
            return ExactLookupReceipt(status=ExactLookupStatus.FAILED, requested_ids=requested)
        try:
            raw = listar()
        except Exception:
            return ExactLookupReceipt(status=ExactLookupStatus.FAILED, requested_ids=requested)
        if raw is None:
            return ExactLookupReceipt(status=ExactLookupStatus.FAILED, requested_ids=requested)
        raw_ids = raw.get("ids") or []
        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []
        wanted = set(requested)
        rows: list[ExactMemoryRow] = []
        for index, memory_id in enumerate(raw_ids):
            memory_id = str(memory_id)
            if memory_id not in wanted:
                continue
            document = str(documents[index]) if index < len(documents) else ""
            metadata = metadatas[index] if index < len(metadatas) else None
            rows.append(
                ExactMemoryRow(
                    id=memory_id,
                    document=document,
                    metadata=dict(metadata) if isinstance(metadata, dict) else None,
                )
            )
        rows.sort(key=lambda row: requested.index(row.id))
        return ExactLookupReceipt(
            status=ExactLookupStatus.PRESENT if rows else ExactLookupStatus.ABSENT,
            requested_ids=requested,
            rows=tuple(rows),
        )

    def _finalize_verified_target(
        self,
        operation: MemoryWriteOperation,
        idempotency_key: str,
        operation_kind: MemoryWriteKind,
        *,
        replayed: bool,
        finalize: bool,
        outcome: MemoryWriteStatus,
        invocation_changed_count: int,
    ) -> MemoryWriteReceipt:
        try:
            operation = self.ledger.transition(
                operation.operation_id,
                state=MemoryWriteState.TARGET_VERIFIED,
            )
            if finalize:
                operation = self.ledger.transition(
                    operation.operation_id,
                    state=MemoryWriteState.COMMITTED,
                    final_status=outcome,
                    matched_count=0,
                    changed_count=1,
                    reconciliation_required=False,
                )
        except Exception as exc:
            self._log_failure(operation_kind, "finalize", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
            return self._ambiguous_after_journal_failure(
                operation, idempotency_key, operation_kind, replayed
            )
        return self._receipt(
            operation,
            idempotency_key,
            status=outcome,
            replayed=replayed,
            changed_count=invocation_changed_count,
        )

    def _mark_failed(
        self,
        operation: MemoryWriteOperation,
        idempotency_key: str,
        operation_kind: MemoryWriteKind,
        reasons: tuple[MemoryWriteIssueCode, ...],
        *,
        replayed: bool,
    ) -> MemoryWriteReceipt:
        try:
            operation = self.ledger.transition(
                operation.operation_id,
                state=MemoryWriteState.FAILED,
                final_status=MemoryWriteStatus.FAILED,
                reason_codes=reasons,
                reconciliation_required=False,
            )
            return self._receipt(operation, idempotency_key, replayed=replayed)
        except Exception as exc:
            self._log_failure(operation_kind, "fail_finalize", MemoryWriteIssueCode.JOURNAL_FAILURE, exc)
            return self._journal_after_claim_failure(
                operation, idempotency_key, operation_kind, replayed
            )

    def _mark_retryable_absence(
        self,
        operation: MemoryWriteOperation,
        idempotency_key: str,
        operation_kind: MemoryWriteKind,
        reasons: tuple[MemoryWriteIssueCode, ...],
        *,
        replayed: bool,
    ) -> MemoryWriteReceipt:
        """Efecto ausente probado: la llamada falla, pero conserva un intento reanudable."""
        try:
            operation = self.ledger.transition(
                operation.operation_id,
                state=MemoryWriteState.INTENT_RECORDED,
                reason_codes=reasons,
                reconciliation_required=False,
            )
        except Exception:
            return self._journal_after_claim_failure(
                operation, idempotency_key, operation_kind, replayed
            )
        return MemoryWriteReceipt(
            operation_id=operation.operation_id,
            idempotency_key=idempotency_key,
            operation_kind=operation_kind,
            status=MemoryWriteStatus.FAILED,
            target_id=operation.target_id,
            matched_count=operation.matched_count,
            changed_count=operation.changed_count,
            reason_codes=operation.reason_codes,
            durable=False,
            reconciliation_required=False,
            replayed=replayed,
        )

    def _mark_ambiguous(
        self,
        operation: MemoryWriteOperation,
        idempotency_key: str,
        operation_kind: MemoryWriteKind,
        reasons: tuple[MemoryWriteIssueCode, ...],
        *,
        replayed: bool,
    ) -> MemoryWriteReceipt:
        all_reasons = merge_write_issue_codes(
            reasons, (MemoryWriteIssueCode.RECONCILIATION_REQUIRED,)
        )
        try:
            operation = self.ledger.transition(
                operation.operation_id,
                state=MemoryWriteState.RECONCILIATION_REQUIRED,
                reason_codes=all_reasons,
                reconciliation_required=True,
            )
        except Exception:
            return self._ambiguous_after_journal_failure(
                operation, idempotency_key, operation_kind, replayed
            )
        return self._receipt(
            operation,
            idempotency_key,
            status=MemoryWriteStatus.AMBIGUOUS,
            replayed=replayed,
        )

    def _receipt(
        self,
        operation: MemoryWriteOperation,
        idempotency_key: str,
        *,
        status: MemoryWriteStatus | None = None,
        replayed: bool = False,
        extra_reasons: tuple[MemoryWriteIssueCode, ...] = (),
        matched_count: int | None = None,
        changed_count: int | None = None,
    ) -> MemoryWriteReceipt:
        actual_status = status or operation.final_status or MemoryWriteStatus.AMBIGUOUS
        return MemoryWriteReceipt(
            operation_id=operation.operation_id,
            idempotency_key=idempotency_key,
            operation_kind=operation.operation_kind,
            status=actual_status,
            target_id=operation.target_id,
            matched_count=(operation.matched_count if matched_count is None else matched_count),
            changed_count=(operation.changed_count if changed_count is None else changed_count),
            reason_codes=merge_write_issue_codes(operation.reason_codes, extra_reasons),
            durable=operation.state in {MemoryWriteState.COMMITTED, MemoryWriteState.FAILED},
            reconciliation_required=(
                operation.reconciliation_required
                or actual_status is MemoryWriteStatus.AMBIGUOUS
            ),
            replayed=replayed,
        )

    @staticmethod
    def _invalid_receipt(operation_kind: MemoryWriteKind) -> MemoryWriteReceipt:
        return MemoryWriteReceipt(
            operation_id="",
            idempotency_key="",
            operation_kind=operation_kind,
            status=MemoryWriteStatus.FAILED,
            reason_codes=(MemoryWriteIssueCode.INVALID_INTENT,),
        )

    @staticmethod
    def _journal_claim_failure(
        operation_kind: MemoryWriteKind,
        idempotency_key: str,
    ) -> MemoryWriteReceipt:
        return MemoryWriteReceipt(
            operation_id="",
            idempotency_key=idempotency_key,
            operation_kind=operation_kind,
            status=MemoryWriteStatus.FAILED,
            reason_codes=(MemoryWriteIssueCode.JOURNAL_FAILURE,),
            durable=False,
        )

    @staticmethod
    def _journal_after_claim_failure(
        operation: MemoryWriteOperation,
        idempotency_key: str,
        operation_kind: MemoryWriteKind,
        replayed: bool,
    ) -> MemoryWriteReceipt:
        return MemoryWriteReceipt(
            operation_id=operation.operation_id,
            idempotency_key=idempotency_key,
            operation_kind=operation_kind,
            status=MemoryWriteStatus.FAILED,
            target_id=operation.target_id,
            reason_codes=(MemoryWriteIssueCode.JOURNAL_FAILURE,),
            durable=False,
            replayed=replayed,
        )

    @staticmethod
    def _ambiguous_after_journal_failure(
        operation: MemoryWriteOperation | None,
        idempotency_key: str,
        operation_kind: MemoryWriteKind,
        replayed: bool,
    ) -> MemoryWriteReceipt:
        return MemoryWriteReceipt(
            operation_id=operation.operation_id if operation else "",
            idempotency_key=idempotency_key,
            operation_kind=operation_kind,
            status=MemoryWriteStatus.AMBIGUOUS,
            target_id=operation.target_id if operation else None,
            matched_count=operation.matched_count if operation else 0,
            changed_count=operation.changed_count if operation else 0,
            reason_codes=(
                MemoryWriteIssueCode.JOURNAL_FAILURE,
                MemoryWriteIssueCode.RECONCILIATION_REQUIRED,
            ),
            durable=False,
            reconciliation_required=True,
            replayed=replayed,
        )

    @staticmethod
    def _log_failure(
        operation_kind: MemoryWriteKind,
        state: str,
        reason: MemoryWriteIssueCode,
        error: BaseException,
    ) -> None:
        logger.warning(
            "Memory write degraded. operation_kind=%s state=%s reason_code=%s error_type=%s",
            operation_kind.value,
            state,
            reason.value,
            type(error).__name__,
        )
