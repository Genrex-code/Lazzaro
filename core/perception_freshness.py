"""Typed temporal facts and policy evaluation for ROOT-029."""

from __future__ import annotations

from enum import Enum
import time
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

from ai_engine.perception_contracts import PerceptionParseReceiptV1
from core.perception_lifecycle import (
    GenerationRelationV1,
    LifecycleStatusV1,
    PerceptionLifecycleReceiptV1,
)


class TemporalEvidenceKindV1(str, Enum):
    UTC_CAPTURE_TIME = "UTC_CAPTURE_TIME"
    RECEIVE_TIME_PROXY = "RECEIVE_TIME_PROXY"
    ABSENT = "ABSENT"


class FreshnessStatusV1(str, Enum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    INDETERMINATE = "INDETERMINATE"
    PARTIAL = "PARTIAL"


class FreshnessReasonCodeV1(str, Enum):
    WITHIN_POLICY = "WITHIN_POLICY"
    TOO_OLD = "TOO_OLD"
    TOO_FAR_IN_FUTURE = "TOO_FAR_IN_FUTURE"
    POLICY_MISSING = "POLICY_MISSING"
    TEMPORAL_EVIDENCE_MISSING = "TEMPORAL_EVIDENCE_MISSING"
    RECEIVE_TIME_PROXY_ONLY = "RECEIVE_TIME_PROXY_ONLY"
    SUPERSEDED = "SUPERSEDED"
    HISTORY_UNKNOWN = "HISTORY_UNKNOWN"
    LIFECYCLE_INCOMPARABLE = "LIFECYCLE_INCOMPARABLE"


class SupersessionStatusV1(str, Enum):
    SUPERSEDED_BY_KNOWN_CAPTURE = "SUPERSEDED_BY_KNOWN_CAPTURE"
    NO_KNOWN_SUPERSEDING_CAPTURE = "NO_KNOWN_SUPERSEDING_CAPTURE"
    HISTORY_UNKNOWN = "HISTORY_UNKNOWN"


class PerceptionIngestionMetadataV1(BaseModel):
    contract_version: StrictInt = 1
    local_ingest_id: StrictStr
    received_at_utc_ms: StrictInt

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @field_validator("received_at_utc_ms", mode="before")
    @classmethod
    def _received(cls, value: Any) -> int:
        if type(value) is not int:
            raise ValueError("received_at_utc_ms must be a strict integer")
        return value


class FreshnessPolicyV1(BaseModel):
    contract_version: StrictInt = 1
    policy_id: StrictStr
    max_capture_age_ms: StrictInt = Field(ge=0)
    max_future_skew_ms: StrictInt = Field(ge=0)
    require_no_known_superseding_capture: StrictBool = True

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @field_validator("max_capture_age_ms", "max_future_skew_ms", mode="before")
    @classmethod
    def _strict_nonnegative(cls, value: Any, info: Any) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{info.field_name} must be a nonnegative strict integer")
        return value

    @field_validator("policy_id")
    @classmethod
    def _policy_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("policy_id cannot be empty")
        return cleaned


class PerceptionFreshnessReceiptV1(BaseModel):
    contract_version: StrictInt = 1
    local_ingest_id: StrictStr
    producer_instance_id: StrictStr | None
    stream_id: StrictStr | None
    producer_epoch_id: StrictStr | None
    producer_capture_id: StrictStr | None
    generation: StrictInt | None
    policy_id: StrictStr | None
    evidence_kind: TemporalEvidenceKindV1
    status: FreshnessStatusV1
    reason_code: FreshnessReasonCodeV1
    supersession: SupersessionStatusV1
    captured_at_utc_ms: StrictInt | None
    received_at_utc_ms: StrictInt
    evaluated_at_utc_ms: StrictInt
    capture_age_ms: StrictInt | None
    receiver_age_ms: StrictInt
    transport_delay_ms: StrictInt | None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value


class UtcClock(Protocol):
    def now_utc_ms(self) -> int: ...


class SystemUtcClock:
    def now_utc_ms(self) -> int:
        return time.time_ns() // 1_000_000


class PerceptionFreshnessEvaluator:
    """Stateless evaluator; all time and policy inputs are explicit."""

    def evaluate(
        self,
        parse_receipt: PerceptionParseReceiptV1,
        ingestion: PerceptionIngestionMetadataV1,
        lifecycle: PerceptionLifecycleReceiptV1,
        *,
        policy: FreshnessPolicyV1 | None,
        evaluated_at_utc_ms: int,
    ) -> PerceptionFreshnessReceiptV1:
        if type(evaluated_at_utc_ms) is not int:
            raise TypeError("evaluated_at_utc_ms must be a strict integer")
        data = parse_receipt.data
        receiver_age = evaluated_at_utc_ms - ingestion.received_at_utc_ms
        supersession = self._supersession(lifecycle)

        if data is None:
            return self._receipt(
                parse_receipt,
                ingestion,
                lifecycle,
                policy=policy,
                evaluated=evaluated_at_utc_ms,
                evidence=TemporalEvidenceKindV1.RECEIVE_TIME_PROXY,
                status=FreshnessStatusV1.PARTIAL,
                reason=FreshnessReasonCodeV1.RECEIVE_TIME_PROXY_ONLY,
                supersession=supersession,
                captured=None,
                capture_age=None,
                receiver_age=receiver_age,
                transport_delay=None,
            )

        captured = data.captured_at_utc_ms
        capture_age = evaluated_at_utc_ms - captured
        transport_delay = ingestion.received_at_utc_ms - captured
        if policy is None:
            status = FreshnessStatusV1.INDETERMINATE
            reason = FreshnessReasonCodeV1.POLICY_MISSING
        elif lifecycle.status in {
            LifecycleStatusV1.ABSENT,
            LifecycleStatusV1.CONTRADICTORY,
        }:
            status = FreshnessStatusV1.INDETERMINATE
            reason = FreshnessReasonCodeV1.LIFECYCLE_INCOMPARABLE
        elif supersession is SupersessionStatusV1.HISTORY_UNKNOWN:
            status = FreshnessStatusV1.INDETERMINATE
            reason = FreshnessReasonCodeV1.HISTORY_UNKNOWN
        elif (
            policy.require_no_known_superseding_capture
            and supersession is SupersessionStatusV1.SUPERSEDED_BY_KNOWN_CAPTURE
        ):
            status = FreshnessStatusV1.NOT_SATISFIED
            reason = FreshnessReasonCodeV1.SUPERSEDED
        elif capture_age < -policy.max_future_skew_ms:
            status = FreshnessStatusV1.NOT_SATISFIED
            reason = FreshnessReasonCodeV1.TOO_FAR_IN_FUTURE
        elif capture_age > policy.max_capture_age_ms:
            status = FreshnessStatusV1.NOT_SATISFIED
            reason = FreshnessReasonCodeV1.TOO_OLD
        else:
            status = FreshnessStatusV1.SATISFIED
            reason = FreshnessReasonCodeV1.WITHIN_POLICY

        return self._receipt(
            parse_receipt,
            ingestion,
            lifecycle,
            policy=policy,
            evaluated=evaluated_at_utc_ms,
            evidence=TemporalEvidenceKindV1.UTC_CAPTURE_TIME,
            status=status,
            reason=reason,
            supersession=supersession,
            captured=captured,
            capture_age=capture_age,
            receiver_age=receiver_age,
            transport_delay=transport_delay,
        )

    @staticmethod
    def _supersession(
        lifecycle: PerceptionLifecycleReceiptV1,
    ) -> SupersessionStatusV1:
        if lifecycle.status is LifecycleStatusV1.HISTORY_UNKNOWN:
            return SupersessionStatusV1.HISTORY_UNKNOWN
        if (
            lifecycle.generation_relation is GenerationRelationV1.OUT_OF_ORDER
            or (
                lifecycle.generation is not None
                and lifecycle.highest_observed_generation is not None
                and lifecycle.generation < lifecycle.highest_observed_generation
            )
        ):
            return SupersessionStatusV1.SUPERSEDED_BY_KNOWN_CAPTURE
        return SupersessionStatusV1.NO_KNOWN_SUPERSEDING_CAPTURE

    @staticmethod
    def _receipt(
        parse_receipt: PerceptionParseReceiptV1,
        ingestion: PerceptionIngestionMetadataV1,
        lifecycle: PerceptionLifecycleReceiptV1,
        *,
        policy: FreshnessPolicyV1 | None,
        evaluated: int,
        evidence: TemporalEvidenceKindV1,
        status: FreshnessStatusV1,
        reason: FreshnessReasonCodeV1,
        supersession: SupersessionStatusV1,
        captured: int | None,
        capture_age: int | None,
        receiver_age: int,
        transport_delay: int | None,
    ) -> PerceptionFreshnessReceiptV1:
        data = parse_receipt.data
        return PerceptionFreshnessReceiptV1(
            local_ingest_id=parse_receipt.local_ingest_id,
            producer_instance_id=data.producer_instance_id if data else None,
            stream_id=data.stream_id if data else None,
            producer_epoch_id=data.producer_epoch_id if data else None,
            producer_capture_id=data.producer_capture_id if data else None,
            generation=data.generation if data else None,
            policy_id=policy.policy_id if policy else None,
            evidence_kind=evidence,
            status=status,
            reason_code=reason,
            supersession=supersession,
            captured_at_utc_ms=captured,
            received_at_utc_ms=ingestion.received_at_utc_ms,
            evaluated_at_utc_ms=evaluated,
            capture_age_ms=capture_age,
            receiver_age_ms=receiver_age,
            transport_delay_ms=transport_delay,
        )
