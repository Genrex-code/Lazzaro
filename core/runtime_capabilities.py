"""Snapshot inmutable de capacidades efectivas del runtime (ROOT-023)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
import threading
from typing import Iterable
import uuid

from ai_engine.llm_call_control import (
    LLMCallResultV1,
    LLMCallTerminalV1,
    LLMFailureClassV1,
)
from memory.memory_write_types import (
    MemoryWriteIssueCode,
    MemoryWriteReceipt,
    MemoryWriteStatus,
)
from memory.retrieval_types import MemoryRetrievalReceipt, RetrievalHealth


class RuntimeOverall(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class RuntimeVerification(str, Enum):
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"


class CapabilityName(str, Enum):
    ORCHESTRATION = "orchestration"
    LLM_GENERATION = "llm_generation"
    LLM_PLAN = "llm_plan"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"


class CapabilityStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class CapabilityReasonCode(str, Enum):
    NOT_CHECKED = "NOT_CHECKED"
    STARTUP_INCOMPLETE = "STARTUP_INCOMPLETE"
    COMPOSITION_READY = "COMPOSITION_READY"
    SHUTDOWN = "SHUTDOWN"
    OPERATION_SUCCEEDED = "OPERATION_SUCCEEDED"
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    NO_COMPATIBLE_PROVIDER = "NO_COMPATIBLE_PROVIDER"
    NO_EFFECTIVE_PROVIDER = "NO_EFFECTIVE_PROVIDER"
    MEMORY_PARTIAL = "MEMORY_PARTIAL"
    ALL_MEMORY_SOURCES_FAILED = "ALL_MEMORY_SOURCES_FAILED"
    MEMORY_BACKEND_UNAVAILABLE = "MEMORY_BACKEND_UNAVAILABLE"
    MEMORY_LEDGER_UNAVAILABLE = "MEMORY_LEDGER_UNAVAILABLE"
    DURABLE_WRITE_CONFIRMED = "DURABLE_WRITE_CONFIRMED"
    WRITE_FAILED = "WRITE_FAILED"
    WRITE_AMBIGUOUS = "WRITE_AMBIGUOUS"
    WRITE_RECONCILIATION_REQUIRED = "WRITE_RECONCILIATION_REQUIRED"


EXPECTED_CAPABILITIES = tuple(CapabilityName)


@dataclass(frozen=True, slots=True)
class CapabilitySnapshot:
    name: CapabilityName
    status: CapabilityStatus
    reason_code: CapabilityReasonCode
    observed_at_utc: datetime


@dataclass(frozen=True, slots=True)
class RuntimeCapabilitySnapshot:
    generation_id: str
    revision: int
    overall: RuntimeOverall
    verification: RuntimeVerification
    capabilities: tuple[CapabilitySnapshot, ...]
    generation_open: bool
    updated_at_utc: datetime

    def capability(self, name: CapabilityName) -> CapabilitySnapshot:
        for capability in self.capabilities:
            if capability.name is name:
                return capability
        raise KeyError(name)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _initial_capabilities(now: datetime) -> tuple[CapabilitySnapshot, ...]:
    return tuple(
        CapabilitySnapshot(
            name=name,
            status=CapabilityStatus.UNKNOWN,
            reason_code=(
                CapabilityReasonCode.STARTUP_INCOMPLETE
                if name is CapabilityName.ORCHESTRATION
                else CapabilityReasonCode.NOT_CHECKED
            ),
            observed_at_utc=now,
        )
        for name in EXPECTED_CAPABILITIES
    )


def _derive_state(
    capabilities: Iterable[CapabilitySnapshot],
) -> tuple[RuntimeOverall, RuntimeVerification]:
    values = tuple(capabilities)
    by_name = {item.name: item for item in values}
    orchestration = by_name[CapabilityName.ORCHESTRATION]
    if orchestration.status is not CapabilityStatus.AVAILABLE:
        overall = RuntimeOverall.UNAVAILABLE
    elif any(
        item.status in {CapabilityStatus.DEGRADED, CapabilityStatus.UNAVAILABLE}
        for item in values
        if item.name is not CapabilityName.ORCHESTRATION
    ):
        overall = RuntimeOverall.DEGRADED
    else:
        # UNKNOWN is evidence incompleteness, never an implicit degradation.
        overall = RuntimeOverall.READY
    verification = (
        RuntimeVerification.COMPLETE
        if all(item.status is not CapabilityStatus.UNKNOWN for item in values)
        else RuntimeVerification.PARTIAL
    )
    return overall, verification


class RuntimeCapabilityHolder:
    """Autoridad copy-on-write; cada update exige la generación vigente."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        now = _utc_now()
        capabilities = list(_initial_capabilities(now))
        capabilities[0] = replace(
            capabilities[0],
            status=CapabilityStatus.UNAVAILABLE,
            reason_code=CapabilityReasonCode.STARTUP_INCOMPLETE,
        )
        overall, verification = _derive_state(capabilities)
        self._snapshot = RuntimeCapabilitySnapshot(
            generation_id=str(uuid.uuid4()),
            revision=0,
            overall=overall,
            verification=verification,
            capabilities=tuple(capabilities),
            generation_open=False,
            updated_at_utc=now,
        )

    def snapshot(self) -> RuntimeCapabilitySnapshot:
        with self._lock:
            return self._snapshot

    def begin_generation(self) -> "RuntimeCapabilityEvidenceSink":
        now = _utc_now()
        generation_id = str(uuid.uuid4())
        capabilities = _initial_capabilities(now)
        overall, verification = _derive_state(capabilities)
        with self._lock:
            self._snapshot = RuntimeCapabilitySnapshot(
                generation_id=generation_id,
                revision=0,
                overall=overall,
                verification=verification,
                capabilities=capabilities,
                generation_open=True,
                updated_at_utc=now,
            )
        return RuntimeCapabilityEvidenceSink(self, generation_id)

    def close_generation(
        self,
        generation_id: str,
        *,
        reason: CapabilityReasonCode = CapabilityReasonCode.SHUTDOWN,
    ) -> bool:
        if reason not in {
            CapabilityReasonCode.STARTUP_INCOMPLETE,
            CapabilityReasonCode.SHUTDOWN,
        }:
            raise ValueError("invalid orchestration close reason")
        return self._replace(
            generation_id,
            {
                CapabilityName.ORCHESTRATION: (
                    CapabilityStatus.UNAVAILABLE,
                    reason,
                )
            },
            close=True,
        )

    def _is_active(self, generation_id: str) -> bool:
        with self._lock:
            return (
                self._snapshot.generation_id == generation_id
                and self._snapshot.generation_open
            )

    def _degrade_unless_available(
        self,
        generation_id: str,
        name: CapabilityName,
        reason: CapabilityReasonCode,
    ) -> bool:
        now = _utc_now()
        with self._lock:
            current = self._snapshot
            if current.generation_id != generation_id or not current.generation_open:
                return False
            existing = current.capability(name)
            if existing.status in {
                CapabilityStatus.AVAILABLE,
                CapabilityStatus.DEGRADED,
                CapabilityStatus.UNAVAILABLE,
            }:
                return True
            updated = tuple(
                replace(
                    item,
                    status=CapabilityStatus.DEGRADED,
                    reason_code=reason,
                    observed_at_utc=now,
                )
                if item.name is name
                else item
                for item in current.capabilities
            )
            overall, verification = _derive_state(updated)
            self._snapshot = RuntimeCapabilitySnapshot(
                generation_id=current.generation_id,
                revision=current.revision + 1,
                overall=overall,
                verification=verification,
                capabilities=updated,
                generation_open=True,
                updated_at_utc=now,
            )
            return True

    def _replace(
        self,
        generation_id: str,
        changes: dict[
            CapabilityName,
            tuple[CapabilityStatus, CapabilityReasonCode],
        ],
        *,
        close: bool = False,
    ) -> bool:
        now = _utc_now()
        with self._lock:
            current = self._snapshot
            if current.generation_id != generation_id or not current.generation_open:
                return False
            updated = tuple(
                replace(
                    item,
                    status=changes[item.name][0],
                    reason_code=changes[item.name][1],
                    observed_at_utc=now,
                )
                if item.name in changes
                else item
                for item in current.capabilities
            )
            overall, verification = _derive_state(updated)
            self._snapshot = RuntimeCapabilitySnapshot(
                generation_id=current.generation_id,
                revision=current.revision + 1,
                overall=overall,
                verification=verification,
                capabilities=updated,
                generation_open=not close,
                updated_at_utc=now,
            )
            return True


_STRUCTURAL_LLM_FAILURES = frozenset(
    {
        LLMFailureClassV1.AUTH_FAILURE,
        LLMFailureClassV1.CONFIGURATION_ERROR,
        LLMFailureClassV1.PROJECTION_UNSUPPORTED,
    }
)


class RuntimeCapabilityEvidenceSink:
    """Vista ligada a generación para productores de evidencia tipada."""

    def __init__(self, holder: RuntimeCapabilityHolder, generation_id: str) -> None:
        self._holder = holder
        self.generation_id = generation_id

    def snapshot(self) -> RuntimeCapabilitySnapshot:
        return self._holder.snapshot()

    def mark_orchestration_available(self) -> bool:
        return self._holder._replace(
            self.generation_id,
            {
                CapabilityName.ORCHESTRATION: (
                    CapabilityStatus.AVAILABLE,
                    CapabilityReasonCode.COMPOSITION_READY,
                )
            },
        )

    def observe_memory_composition(
        self,
        *,
        backend_available: bool,
        ledger_open: bool,
    ) -> bool:
        if not self._holder._is_active(self.generation_id):
            return False
        changes: dict[CapabilityName, tuple[CapabilityStatus, CapabilityReasonCode]] = {}
        if not backend_available:
            changes[CapabilityName.MEMORY_READ] = (
                CapabilityStatus.UNAVAILABLE,
                CapabilityReasonCode.MEMORY_BACKEND_UNAVAILABLE,
            )
            changes[CapabilityName.MEMORY_WRITE] = (
                CapabilityStatus.UNAVAILABLE,
                CapabilityReasonCode.MEMORY_BACKEND_UNAVAILABLE,
            )
        elif not ledger_open:
            changes[CapabilityName.MEMORY_WRITE] = (
                CapabilityStatus.UNAVAILABLE,
                CapabilityReasonCode.MEMORY_LEDGER_UNAVAILABLE,
            )
        if not changes:
            return True
        return self._holder._replace(self.generation_id, changes)

    def observe_llm_health_boolean(self, healthy: bool) -> bool:
        if healthy:
            return self._set(
                CapabilityName.LLM_GENERATION,
                CapabilityStatus.AVAILABLE,
                CapabilityReasonCode.OPERATION_SUCCEEDED,
            )
        return self._degrade_unknown_but_preserve_available(
            CapabilityName.LLM_GENERATION,
            CapabilityReasonCode.HEALTH_CHECK_FAILED,
        )

    def observe_llm(self, operation: str, result: LLMCallResultV1) -> bool:
        if not self._holder._is_active(self.generation_id):
            return False
        if not isinstance(result, LLMCallResultV1):
            return False
        normalized_operation = str(operation).strip().lower()
        if result.succeeded:
            changes = {
                CapabilityName.LLM_GENERATION: (
                    CapabilityStatus.AVAILABLE,
                    CapabilityReasonCode.OPERATION_SUCCEEDED,
                )
            }
            if normalized_operation == "plan":
                changes[CapabilityName.LLM_PLAN] = (
                    CapabilityStatus.AVAILABLE,
                    CapabilityReasonCode.OPERATION_SUCCEEDED,
                )
            return self._holder._replace(self.generation_id, changes)

        target = (
            CapabilityName.LLM_PLAN
            if normalized_operation == "plan"
            else CapabilityName.LLM_GENERATION
        )
        if (
            normalized_operation == "plan"
            and result.terminal is LLMCallTerminalV1.NO_COMPATIBLE_PROVIDER
        ):
            return self._set(
                target,
                CapabilityStatus.UNAVAILABLE,
                CapabilityReasonCode.NO_COMPATIBLE_PROVIDER,
            )

        failures = tuple(
            receipt.failure_class
            for receipt in result.receipts
            if receipt.failure_class is not None
        )
        exhaustive_terminal = result.terminal in {
            LLMCallTerminalV1.NON_RETRYABLE_FAILURE,
            LLMCallTerminalV1.ALL_PROVIDERS_FAILED,
        }
        structurally_unavailable = (
            exhaustive_terminal
            and bool(failures)
            and all(failure in _STRUCTURAL_LLM_FAILURES for failure in failures)
        )
        if structurally_unavailable:
            return self._set(
                target,
                CapabilityStatus.UNAVAILABLE,
                CapabilityReasonCode.NO_EFFECTIVE_PROVIDER,
            )

        # El health de startup es evidencia negativa explícita. Para el resto,
        # una solicitud transitoria fallida no diagnostica la capacidad global.
        if normalized_operation == "health":
            return self._degrade_unknown_but_preserve_available(
                target,
                CapabilityReasonCode.HEALTH_CHECK_FAILED,
            )
        return True

    def observe_memory_read(self, receipt: MemoryRetrievalReceipt) -> bool:
        if not self._holder._is_active(self.generation_id):
            return False
        if not isinstance(receipt, MemoryRetrievalReceipt):
            return False
        mapping = {
            RetrievalHealth.OK: (
                CapabilityStatus.AVAILABLE,
                CapabilityReasonCode.OPERATION_SUCCEEDED,
            ),
            RetrievalHealth.OK_EMPTY: (
                CapabilityStatus.AVAILABLE,
                CapabilityReasonCode.OPERATION_SUCCEEDED,
            ),
            RetrievalHealth.PARTIAL: (
                CapabilityStatus.DEGRADED,
                CapabilityReasonCode.MEMORY_PARTIAL,
            ),
            RetrievalHealth.FAILED: (
                CapabilityStatus.UNAVAILABLE,
                CapabilityReasonCode.ALL_MEMORY_SOURCES_FAILED,
            ),
        }
        status, reason = mapping[receipt.health]
        return self._set(CapabilityName.MEMORY_READ, status, reason)

    def observe_memory_write(self, receipt: MemoryWriteReceipt) -> bool:
        if not self._holder._is_active(self.generation_id):
            return False
        if not isinstance(receipt, MemoryWriteReceipt):
            return False
        reasons = set(receipt.reason_codes)
        request_rejections = {
            MemoryWriteIssueCode.INVALID_INTENT,
            MemoryWriteIssueCode.IDEMPOTENCY_CONFLICT,
        }
        if reasons and reasons.issubset(request_rejections):
            return True
        if receipt.reconciliation_required:
            return self._set(
                CapabilityName.MEMORY_WRITE,
                CapabilityStatus.DEGRADED,
                CapabilityReasonCode.WRITE_RECONCILIATION_REQUIRED,
            )
        if (
            receipt.status in {MemoryWriteStatus.CREATED, MemoryWriteStatus.ALREADY_EXISTS}
            and receipt.durable
        ):
            return self._set(
                CapabilityName.MEMORY_WRITE,
                CapabilityStatus.AVAILABLE,
                CapabilityReasonCode.DURABLE_WRITE_CONFIRMED,
            )
        if receipt.status is MemoryWriteStatus.AMBIGUOUS:
            reason = CapabilityReasonCode.WRITE_AMBIGUOUS
        else:
            reason = CapabilityReasonCode.WRITE_FAILED
        return self._set(
            CapabilityName.MEMORY_WRITE,
            CapabilityStatus.DEGRADED,
            reason,
        )

    def _degrade_unknown_but_preserve_available(
        self,
        name: CapabilityName,
        reason: CapabilityReasonCode,
    ) -> bool:
        return self._holder._degrade_unless_available(
            self.generation_id,
            name,
            reason,
        )

    def _set(
        self,
        name: CapabilityName,
        status: CapabilityStatus,
        reason: CapabilityReasonCode,
    ) -> bool:
        return self._holder._replace(
            self.generation_id,
            {name: (status, reason)},
        )
