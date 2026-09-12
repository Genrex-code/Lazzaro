"""Contrato tipado para el resultado operacional de una decision.

DecisionOutcome es la unica autoridad interna para distinguir que resolvio el
turno y por que camino. La proyeccion publica deliberadamente reduce este
contrato a version, disposition y un unico reason_code seguro.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ai_engine.structured_parse_types import StructuredParseStatus


class DecisionDisposition(str, Enum):
    ACTION = "action"
    NO_ACTION = "no_action"
    CLARIFICATION = "clarification"
    BLOCKED = "blocked"
    INVALID = "invalid"
    ERROR = "error"


class DecisionQuality(str, Enum):
    VALIDATED = "validated"
    SALVAGED = "salvaged"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    REJECTED = "rejected"
    FAILED = "failed"
    LEGACY_UNKNOWN = "legacy_unknown"


class DecisionAttempt(str, Enum):
    FIRST = "first"
    REPAIR = "repair"
    LEGACY = "legacy"


class DecisionLayer(str, Enum):
    CORE = "core"
    REASONING = "reasoning"
    PERSONA = "persona"
    MOTOR = "motor"
    ORACLE_PERSONA = "oracle_persona"


class MotorOutcomeStatus(str, Enum):
    READY = "ready"
    REQUIRES_CLARIFICATION = "requires_clarification"
    NOT_FOUND = "not_found"
    NOT_ALLOWED = "not_allowed"
    UNSAFE = "unsafe"


class DecisionReasonCode(str, Enum):
    PRIMARY_VALID = "primary_valid"
    PASSIVE_NO_ACTION = "passive_no_action"
    PARSE_DEGRADED = "parse_degraded"
    PARSE_ERROR = "parse_error"
    SAFE_SALVAGE = "safe_salvage"
    RETRY_REPAIRED = "retry_repaired"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    MOTOR_READY = "motor_ready"
    MOTOR_CLARIFICATION = "motor_clarification"
    MISSING_TARGET = "missing_target"
    AMBIGUOUS_TARGET = "ambiguous_target"
    TARGET_NOT_FOUND = "target_not_found"
    ACTION_NOT_ALLOWED = "action_not_allowed"
    UNSAFE_BLOCK = "unsafe_block"
    MOTOR_RESOLUTION_FAILED = "motor_resolution_failed"
    UNKNOWN_ACTION = "unknown_action"
    INVALID_PARAMETERS = "invalid_parameters"
    ORACLE_RESULT_UNAVAILABLE = "oracle_result_unavailable"
    CHANNEL_BLOCKED = "channel_blocked"
    PIPELINE_ERROR = "pipeline_error"
    LEGACY_UNTYPED = "legacy_untyped"


class DecisionOutcome(BaseModel):
    """Resultado interno, ortogonal y versionado de una sola decision."""

    contract_version: StrictInt = 1
    disposition: DecisionDisposition
    quality: DecisionQuality
    attempt: DecisionAttempt
    layer: DecisionLayer
    reason_codes: tuple[DecisionReasonCode, ...] = Field(min_length=1)
    retry_count: StrictInt = Field(ge=0, le=1)
    primary_parse_status: StructuredParseStatus | None = None
    final_parse_status: StructuredParseStatus | None = None
    motor_status: MotorOutcomeStatus | None = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_invariants(self) -> "DecisionOutcome":
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("contract_version must be the integer 1")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason_codes must not contain duplicates")
        if (self.attempt is DecisionAttempt.REPAIR) != (self.retry_count == 1):
            raise ValueError("repair attempt and retry_count=1 must coincide")
        if self.attempt is DecisionAttempt.LEGACY:
            if self.quality is not DecisionQuality.LEGACY_UNKNOWN:
                raise ValueError("legacy attempt requires legacy_unknown quality")
        elif self.quality is DecisionQuality.LEGACY_UNKNOWN:
            raise ValueError("legacy_unknown quality requires legacy attempt")
        if (self.primary_parse_status is None) != (self.final_parse_status is None):
            raise ValueError("parse statuses must be both present or both absent")
        if self.retry_count == 0 and self.primary_parse_status != self.final_parse_status:
            raise ValueError("a first attempt cannot change parse status")
        if self.retry_count == 1 and self.primary_parse_status is not StructuredParseStatus.ERROR:
            raise ValueError("current repair policy requires a primary parse error")
        if (
            self.quality is DecisionQuality.VALIDATED
            and self.final_parse_status is not None
            and self.final_parse_status is not StructuredParseStatus.VALID
        ):
            raise ValueError("validated LLM output requires final VALID parse")
        if (
            self.quality is DecisionQuality.SALVAGED
            and self.final_parse_status is not StructuredParseStatus.DEGRADED
        ):
            raise ValueError("salvaged output requires final DEGRADED parse")
        if self.quality is DecisionQuality.DETERMINISTIC_FALLBACK and self.is_primary:
            raise ValueError("deterministic fallback cannot be primary")
        if self.quality is DecisionQuality.REJECTED and self.disposition is not DecisionDisposition.INVALID:
            raise ValueError("rejected quality requires INVALID disposition")
        if self.quality is DecisionQuality.FAILED and self.disposition is not DecisionDisposition.ERROR:
            raise ValueError("failed quality requires ERROR disposition")
        if self.motor_status is not None:
            if self.layer is not DecisionLayer.MOTOR:
                raise ValueError("motor_status is only valid for the Motor layer")
            expected = {
                MotorOutcomeStatus.READY: DecisionDisposition.ACTION,
                MotorOutcomeStatus.REQUIRES_CLARIFICATION: DecisionDisposition.CLARIFICATION,
                MotorOutcomeStatus.NOT_FOUND: DecisionDisposition.BLOCKED,
                MotorOutcomeStatus.NOT_ALLOWED: DecisionDisposition.BLOCKED,
                MotorOutcomeStatus.UNSAFE: DecisionDisposition.BLOCKED,
            }[self.motor_status]
            if self.disposition is not expected:
                raise ValueError("motor_status contradicts decision disposition")
        return self

    @property
    def is_primary(self) -> bool:
        return (
            self.attempt is DecisionAttempt.FIRST
            and self.quality is DecisionQuality.VALIDATED
        )

    @property
    def fallback_used(self) -> bool:
        return self.quality in {
            DecisionQuality.SALVAGED,
            DecisionQuality.DETERMINISTIC_FALLBACK,
        } or self.attempt is DecisionAttempt.REPAIR

    @property
    def operationally_resolved(self) -> bool:
        return self.disposition in {
            DecisionDisposition.ACTION,
            DecisionDisposition.NO_ACTION,
            DecisionDisposition.CLARIFICATION,
            DecisionDisposition.BLOCKED,
        }

    @property
    def public_reason_code(self) -> DecisionReasonCode:
        return self.reason_codes[0]

    def public_projection(self) -> dict[str, object]:
        return {
            "version": 1,
            "disposition": self.disposition.value,
            "reason_code": self.public_reason_code.value,
        }

    def evolve(self, **updates: object) -> "DecisionOutcome":
        """Crea una variante volviendo a ejecutar todas las invariantes."""
        data = self.model_dump(mode="python")
        data.update(updates)
        return type(self).model_validate(data)

    @classmethod
    def legacy_for_action(cls, action: object) -> "DecisionOutcome":
        name = str(action or "none").strip()
        return cls(
            disposition=(
                DecisionDisposition.NO_ACTION
                if not name or name == "none"
                else DecisionDisposition.ACTION
            ),
            quality=DecisionQuality.LEGACY_UNKNOWN,
            attempt=DecisionAttempt.LEGACY,
            layer=DecisionLayer.CORE,
            reason_codes=(DecisionReasonCode.LEGACY_UNTYPED,),
            retry_count=0,
        )
