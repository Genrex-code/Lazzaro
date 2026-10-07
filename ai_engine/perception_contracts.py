"""Contratos ROOT-029 para datos e identidad perceptiva.

Los modelos de este modulo contienen hechos del productor. No expresan frescura,
seguridad ni autorizacion fisica. La compatibilidad legacy queda en un recibo
explicito y nunca se promueve a evidencia fisica V1.
"""

from __future__ import annotations

from enum import Enum
import math
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)


MAX_GENERATION = (2**63) - 1


def _strict_version(value: Any) -> int:
    if type(value) is not int or value != 1:
        raise ValueError("contract_version must be the exact integer 1")
    return value


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise ValueError(f"{field_name} must be an ordinary int or float")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _clean_required(value: str, *, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    return cleaned


def _clean_optional(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _clean_required(value, field_name=field_name)


class PerceptionSourceKindV1(str, Enum):
    UNITY = "UNITY"
    DESKTOP = "DESKTOP"
    OCR = "OCR"
    UI_TREE = "UI_TREE"
    OTHER = "OTHER"


class PerceptionParseStatusV1(str, Enum):
    CANONICAL = "CANONICAL"
    LEGACY_DEGRADED = "LEGACY_DEGRADED"
    INVALID = "INVALID"


class PerceptionParseReasonV1(str, Enum):
    CANONICAL_ACCEPTED = "CANONICAL_ACCEPTED"
    LEGACY_OR_INCOMPLETE = "LEGACY_OR_INCOMPLETE"
    CONTRACT_VALIDATION_FAILED = "CONTRACT_VALIDATION_FAILED"
    NO_PERCEPTION = "NO_PERCEPTION"


class IdentityStatusV1(str, Enum):
    RESOLVED = "RESOLVED"
    PARTIAL = "PARTIAL"
    ABSENT = "ABSENT"
    AMBIGUOUS = "AMBIGUOUS"
    CONTRADICTORY = "CONTRADICTORY"


class RecordMultiplicityRelationV1(str, Enum):
    UNIQUE = "UNIQUE"
    DUPLICATE_RECORD = "DUPLICATE_RECORD"
    AMBIGUOUS_DUPLICATE = "AMBIGUOUS_DUPLICATE"
    CONTRADICTORY = "CONTRADICTORY"


class Vector3V1(BaseModel):
    x: float
    y: float
    z: float

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("x", "y", "z", mode="before")
    @classmethod
    def _strict_finite(cls, value: Any, info: Any) -> float:
        return _finite_number(value, field_name=info.field_name)


class Vector2V1(BaseModel):
    x: float
    y: float

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("x", "y", mode="before")
    @classmethod
    def _strict_finite(cls, value: Any, info: Any) -> float:
        return _finite_number(value, field_name=info.field_name)


class PerceivedObjectDataV1(BaseModel):
    contract_version: StrictInt = 1
    producer_object_id: StrictStr | None = None
    runtime_object_id: StrictStr | None = None
    display_name: StrictStr
    aliases: tuple[StrictStr, ...] = ()
    position: Vector3V1 | None = None
    screen_position: Vector2V1 | None = None
    distance: float | None = None
    affordances: tuple[StrictStr, ...] = ()
    confidence: float | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        return _strict_version(value)

    @field_validator("producer_object_id", "runtime_object_id")
    @classmethod
    def _optional_ids(cls, value: str | None, info: Any) -> str | None:
        return _clean_optional(value, field_name=info.field_name)

    @field_validator("display_name")
    @classmethod
    def _display(cls, value: str) -> str:
        return _clean_required(value, field_name="display_name")

    @field_validator("aliases", "affordances")
    @classmethod
    def _clean_sequences(cls, values: tuple[str, ...], info: Any) -> tuple[str, ...]:
        cleaned: list[str] = []
        for value in values:
            item = _clean_required(value, field_name=info.field_name)
            if item not in cleaned:
                cleaned.append(item)
        return tuple(cleaned)

    @field_validator("distance", mode="before")
    @classmethod
    def _distance(cls, value: Any) -> float | None:
        if value is None:
            return None
        number = _finite_number(value, field_name="distance")
        if number < 0:
            raise ValueError("distance must be greater than or equal to zero")
        return number

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> float | None:
        if value is None:
            return None
        number = _finite_number(value, field_name="confidence")
        if number < 0 or number > 1:
            raise ValueError("confidence must be between zero and one")
        return number


class PerceptionDataV1(BaseModel):
    contract_version: StrictInt = 1
    source_kind: PerceptionSourceKindV1
    producer_instance_id: StrictStr
    stream_id: StrictStr
    producer_epoch_id: StrictStr
    logical_world_id: StrictStr | None = None
    producer_capture_id: StrictStr
    generation: StrictInt = Field(ge=0, le=MAX_GENERATION)
    captured_at_utc_ms: StrictInt
    objects: tuple[PerceivedObjectDataV1, ...]

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        return _strict_version(value)

    @field_validator("generation", "captured_at_utc_ms", mode="before")
    @classmethod
    def _strict_ints(cls, value: Any, info: Any) -> int:
        if type(value) is not int:
            raise ValueError(f"{info.field_name} must be a strict integer")
        return value

    @field_validator(
        "producer_instance_id",
        "stream_id",
        "producer_epoch_id",
        "producer_capture_id",
    )
    @classmethod
    def _required_ids(cls, value: str, info: Any) -> str:
        return _clean_required(value, field_name=info.field_name)

    @field_validator("logical_world_id")
    @classmethod
    def _world(cls, value: str | None) -> str | None:
        return _clean_optional(value, field_name="logical_world_id")


class PerceptionParseReceiptV1(BaseModel):
    contract_version: StrictInt = 1
    local_ingest_id: StrictStr
    status: PerceptionParseStatusV1
    reason_code: PerceptionParseReasonV1
    data: PerceptionDataV1 | None = None
    compatibility_used: bool = False

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        return _strict_version(value)

    @field_validator("local_ingest_id")
    @classmethod
    def _ingest_id(cls, value: str) -> str:
        return _clean_required(value, field_name="local_ingest_id")

    @model_validator(mode="after")
    def _coherent_status(self) -> "PerceptionParseReceiptV1":
        if self.status is PerceptionParseStatusV1.CANONICAL:
            if self.data is None or self.compatibility_used:
                raise ValueError("canonical receipt requires data without compatibility")
        elif self.data is not None:
            raise ValueError("non-canonical receipt cannot carry canonical data")
        if self.status is PerceptionParseStatusV1.LEGACY_DEGRADED and not self.compatibility_used:
            raise ValueError("legacy receipt must expose compatibility use")
        return self


class PerceptionIdentityReceiptV1(BaseModel):
    contract_version: StrictInt = 1
    local_ingest_id: StrictStr
    producer_instance_id: StrictStr | None
    stream_id: StrictStr | None
    producer_epoch_id: StrictStr | None
    producer_capture_id: StrictStr | None
    generation: StrictInt | None
    target_runtime_object_id: StrictStr | None
    target_producer_object_id: StrictStr | None
    status: IdentityStatusV1
    multiplicity: RecordMultiplicityRelationV1
    action_relevant_records_agree: bool

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _version(cls, value: Any) -> int:
        return _strict_version(value)

    @field_validator("generation", mode="before")
    @classmethod
    def _generation(cls, value: Any) -> int | None:
        if value is None:
            return None
        if type(value) is not int or value < 0 or value > MAX_GENERATION:
            raise ValueError("generation must be a bounded strict integer")
        return value


_CANONICAL_TOP_LEVEL = {
    "source_kind",
    "producer_instance_id",
    "stream_id",
    "producer_epoch_id",
    "producer_capture_id",
    "generation",
    "captured_at_utc_ms",
}


def parse_perception_payload(
    payload: Any,
    *,
    local_ingest_id: str,
) -> PerceptionParseReceiptV1:
    """Parsea solo el contrato moderno; legacy queda explicitamente degradado."""

    if not isinstance(payload, dict):
        return PerceptionParseReceiptV1(
            local_ingest_id=local_ingest_id,
            status=PerceptionParseStatusV1.LEGACY_DEGRADED,
            reason_code=PerceptionParseReasonV1.NO_PERCEPTION,
            compatibility_used=True,
        )
    if not _CANONICAL_TOP_LEVEL.issubset(payload):
        return PerceptionParseReceiptV1(
            local_ingest_id=local_ingest_id,
            status=PerceptionParseStatusV1.LEGACY_DEGRADED,
            reason_code=PerceptionParseReasonV1.LEGACY_OR_INCOMPLETE,
            compatibility_used=True,
        )

    raw_objects = payload.get("objects", payload.get("objetos_normalizados"))
    if isinstance(raw_objects, list):
        raw_objects = [
            {
                "contract_version": item.get("contract_version", 1),
                "producer_object_id": item.get("producer_object_id"),
                "runtime_object_id": item.get("runtime_object_id"),
                "display_name": item.get("display_name"),
                "aliases": item.get("aliases", ()),
                "position": item.get("position"),
                "screen_position": item.get("screen_position"),
                "distance": item.get("distance"),
                "affordances": item.get("affordances", ()),
                "confidence": item.get("confidence"),
            }
            if isinstance(item, dict)
            else item
            for item in raw_objects
        ]
    source_value = payload.get("source_kind")
    if isinstance(source_value, str):
        source_value = source_value.strip().upper()
    canonical = {
        "contract_version": payload.get("contract_version", 1),
        "source_kind": source_value,
        "producer_instance_id": payload.get("producer_instance_id"),
        "stream_id": payload.get("stream_id"),
        "producer_epoch_id": payload.get("producer_epoch_id"),
        "logical_world_id": payload.get("logical_world_id"),
        "producer_capture_id": payload.get("producer_capture_id"),
        "generation": payload.get("generation"),
        "captured_at_utc_ms": payload.get("captured_at_utc_ms"),
        "objects": raw_objects,
    }
    try:
        data = PerceptionDataV1.model_validate(canonical)
    except (ValidationError, TypeError, ValueError):
        return PerceptionParseReceiptV1(
            local_ingest_id=local_ingest_id,
            status=PerceptionParseStatusV1.INVALID,
            reason_code=PerceptionParseReasonV1.CONTRACT_VALIDATION_FAILED,
        )
    return PerceptionParseReceiptV1(
        local_ingest_id=local_ingest_id,
        status=PerceptionParseStatusV1.CANONICAL,
        reason_code=PerceptionParseReasonV1.CANONICAL_ACCEPTED,
        data=data,
    )


def evaluate_perception_identity(
    parse_receipt: PerceptionParseReceiptV1,
    *,
    runtime_object_id: str | None,
    producer_object_id: str | None = None,
    lifecycle_resolved: bool = False,
) -> PerceptionIdentityReceiptV1:
    """Evalua identidad exacta sin usar nombres, aliases ni proximidad."""

    data = parse_receipt.data
    if data is None or not runtime_object_id:
        return PerceptionIdentityReceiptV1(
            local_ingest_id=parse_receipt.local_ingest_id,
            producer_instance_id=data.producer_instance_id if data else None,
            stream_id=data.stream_id if data else None,
            producer_epoch_id=data.producer_epoch_id if data else None,
            producer_capture_id=data.producer_capture_id if data else None,
            generation=data.generation if data else None,
            target_runtime_object_id=runtime_object_id,
            target_producer_object_id=producer_object_id,
            status=IdentityStatusV1.ABSENT,
            multiplicity=RecordMultiplicityRelationV1.UNIQUE,
            action_relevant_records_agree=False,
        )

    runtime_matches = [obj for obj in data.objects if obj.runtime_object_id == runtime_object_id]
    if not runtime_matches:
        status = IdentityStatusV1.ABSENT
        multiplicity = RecordMultiplicityRelationV1.UNIQUE
        agree = False
    else:
        producer_ids = {obj.producer_object_id for obj in runtime_matches if obj.producer_object_id}
        if len(producer_ids) > 1 or (
            producer_object_id is not None
            and any(obj.producer_object_id != producer_object_id for obj in runtime_matches)
        ):
            status = IdentityStatusV1.CONTRADICTORY
            multiplicity = RecordMultiplicityRelationV1.CONTRADICTORY
            agree = False
        else:
            relevant = {
                (
                    obj.position,
                    obj.distance,
                    obj.affordances,
                    obj.confidence,
                )
                for obj in runtime_matches
            }
            agree = len(relevant) == 1
            if len(runtime_matches) > 1 and not agree:
                status = IdentityStatusV1.AMBIGUOUS
                multiplicity = RecordMultiplicityRelationV1.AMBIGUOUS_DUPLICATE
            else:
                multiplicity = (
                    RecordMultiplicityRelationV1.DUPLICATE_RECORD
                    if len(runtime_matches) > 1
                    else RecordMultiplicityRelationV1.UNIQUE
                )
                status = (
                    IdentityStatusV1.RESOLVED
                    if lifecycle_resolved
                    else IdentityStatusV1.PARTIAL
                )

    return PerceptionIdentityReceiptV1(
        local_ingest_id=parse_receipt.local_ingest_id,
        producer_instance_id=data.producer_instance_id,
        stream_id=data.stream_id,
        producer_epoch_id=data.producer_epoch_id,
        producer_capture_id=data.producer_capture_id,
        generation=data.generation,
        target_runtime_object_id=runtime_object_id,
        target_producer_object_id=producer_object_id,
        status=status,
        multiplicity=multiplicity,
        action_relevant_records_agree=agree,
    )
