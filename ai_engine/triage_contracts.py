"""Contrato interno discriminado para decisiones de Triage.

``TriageDecisionV1`` es la autoridad semantica. La proyeccion legacy conserva
la superficie historica del router, incluida ``confidence``, sin convertirla
en parte del contrato versionado.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from ai_engine.intent_types import IntentType, Route, normalize_intent, route_for_intent


class TriageOutcomeV1(str, Enum):
    DECIDED = "DECIDED"
    DEGRADED = "DEGRADED"
    UNCERTAIN = "UNCERTAIN"
    FAILED = "FAILED"


class TriageSourceV1(str, Enum):
    """Autoridad que produjo ``semantic_intent``."""

    EVENT_RULE = "EVENT_RULE"
    LEXICAL = "LEXICAL"
    MODEL = "MODEL"
    FALLBACK = "FALLBACK"


class TriageOperationalOverrideV1(str, Enum):
    MOVEMENT_BLOCKED = "MOVEMENT_BLOCKED"
    EVENT_ROUTE_OVERRIDE = "EVENT_ROUTE_OVERRIDE"


class TriageModelStatusV1(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    UNAVAILABLE = "UNAVAILABLE"
    ACCEPTED = "ACCEPTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    ERROR = "ERROR"
    INCOMPATIBLE_LABEL = "INCOMPATIBLE_LABEL"
    VETOED = "VETOED"


class TriageFallbackStatusV1(str, Enum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    ACCEPTED = "ACCEPTED"
    NO_MATCH = "NO_MATCH"
    ERROR = "ERROR"
    VETOED = "VETOED"


class TriageReasonCodeV1(str, Enum):
    EVENT_RULE_ACCEPTED = "EVENT_RULE_ACCEPTED"
    EMPTY_COMMAND = "EMPTY_COMMAND"
    UNSUPPORTED_EVENT = "UNSUPPORTED_EVENT"
    LEXICAL_RULE_ACCEPTED = "LEXICAL_RULE_ACCEPTED"
    MODEL_ACCEPTED = "MODEL_ACCEPTED"
    FALLBACK_ACCEPTED = "FALLBACK_ACCEPTED"
    NO_ACCEPTABLE_CANDIDATE = "NO_ACCEPTABLE_CANDIDATE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_ERROR = "MODEL_ERROR"
    MODEL_LABEL_INCOMPATIBLE = "MODEL_LABEL_INCOMPATIBLE"
    FALLBACK_ERROR = "FALLBACK_ERROR"
    MOVEMENT_BLOCKED = "MOVEMENT_BLOCKED"


_TECHNICAL_MODEL_FAILURES = {
    TriageModelStatusV1.UNAVAILABLE,
    TriageModelStatusV1.ERROR,
    TriageModelStatusV1.INCOMPATIBLE_LABEL,
}
_MODEL_STATUSES_WITH_REQUIRED_LABEL = {
    TriageModelStatusV1.ACCEPTED,
    TriageModelStatusV1.LOW_CONFIDENCE,
    TriageModelStatusV1.INCOMPATIBLE_LABEL,
    TriageModelStatusV1.VETOED,
}


class TriageDecisionV1(BaseModel):
    """Resultado inmutable de una clasificacion de Capa 0."""

    contract_version: StrictInt = 1
    outcome: TriageOutcomeV1
    source: TriageSourceV1 | None
    semantic_intent: IntentType | None
    route: Route | None
    operational_override: TriageOperationalOverrideV1 | None = None
    reason_code: TriageReasonCodeV1
    model_status: TriageModelStatusV1
    fallback_status: TriageFallbackStatusV1
    raw_label: str | None = Field(default=None, min_length=1, max_length=128)
    accepted_rule_id: str | None = Field(default=None, min_length=1, max_length=128)
    human_reason: str = Field(max_length=500)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def validate_invariants(self) -> "TriageDecisionV1":
        if type(self.contract_version) is not int or self.contract_version != 1:
            raise ValueError("contract_version must be the integer 1")

        has_decision = self.outcome in {
            TriageOutcomeV1.DECIDED,
            TriageOutcomeV1.DEGRADED,
        }
        if has_decision:
            if self.semantic_intent is None or self.source is None or self.route is None:
                raise ValueError("decided/degraded outcomes require intent, source and route")
        elif any(
            value is not None
            for value in (
                self.semantic_intent,
                self.source,
                self.route,
                self.operational_override,
            )
        ):
            raise ValueError("uncertain/failed outcomes cannot assert a decision")

        if self.model_status in _MODEL_STATUSES_WITH_REQUIRED_LABEL and self.raw_label is None:
            raise ValueError("the model status requires a raw label")
        if self.model_status in {
            TriageModelStatusV1.NOT_ATTEMPTED,
            TriageModelStatusV1.UNAVAILABLE,
        } and self.raw_label is not None:
            raise ValueError("the model status cannot carry a raw label")

        if self.semantic_intent is IntentType.UNKNOWN:
            if not (
                self.outcome is TriageOutcomeV1.DECIDED
                and self.source is TriageSourceV1.MODEL
                and self.model_status is TriageModelStatusV1.ACCEPTED
                and self.fallback_status is TriageFallbackStatusV1.NOT_ATTEMPTED
                and self.raw_label is not None
                and self.raw_label.strip().upper() == IntentType.UNKNOWN.value
            ):
                raise ValueError("semantic UNKNOWN is only valid as an accepted literal model class")

        if self.outcome is TriageOutcomeV1.DEGRADED and not (
            self.source is TriageSourceV1.FALLBACK
            and self.fallback_status is TriageFallbackStatusV1.ACCEPTED
            and self.model_status in _TECHNICAL_MODEL_FAILURES
        ):
            raise ValueError("degraded requires a fallback after technical model failure")

        if self.source is TriageSourceV1.MODEL:
            if (
                self.model_status is not TriageModelStatusV1.ACCEPTED
                or self.fallback_status is not TriageFallbackStatusV1.NOT_ATTEMPTED
                or self.raw_label is None
                or normalize_intent(self.raw_label) is not self.semantic_intent
            ):
                raise ValueError("MODEL source contradicts model evidence")
        elif self.source is TriageSourceV1.FALLBACK:
            if self.fallback_status is not TriageFallbackStatusV1.ACCEPTED:
                raise ValueError("FALLBACK source requires an accepted fallback")
            if self.outcome is TriageOutcomeV1.DECIDED and self.model_status not in {
                TriageModelStatusV1.LOW_CONFIDENCE,
                TriageModelStatusV1.VETOED,
            }:
                raise ValueError("normal fallback decisions require low-confidence or veto evidence")
        elif self.source in {TriageSourceV1.LEXICAL, TriageSourceV1.EVENT_RULE}:
            if self.accepted_rule_id is None:
                raise ValueError("rule sources require accepted_rule_id")
            if (
                self.model_status is not TriageModelStatusV1.NOT_ATTEMPTED
                or self.fallback_status is not TriageFallbackStatusV1.NOT_ATTEMPTED
                or self.raw_label is not None
            ):
                raise ValueError("rule decisions cannot claim model or fallback attempts")

        if self.source not in {TriageSourceV1.LEXICAL, TriageSourceV1.EVENT_RULE}:
            if self.accepted_rule_id is not None:
                raise ValueError("accepted_rule_id is only valid for rule decisions")

        if self.operational_override is None and self.semantic_intent is not None:
            if self.route is not route_for_intent(self.semantic_intent):
                raise ValueError("route must be canonical without an operational override")
        elif self.operational_override is TriageOperationalOverrideV1.MOVEMENT_BLOCKED:
            if not (
                self.semantic_intent in {IntentType.MOTOR_SIMPLE, IntentType.MOTOR_REFERENCE}
                and self.source in {
                    TriageSourceV1.LEXICAL,
                    TriageSourceV1.MODEL,
                    TriageSourceV1.FALLBACK,
                }
                and self.route is Route.NOOP
                and self.reason_code is TriageReasonCodeV1.MOVEMENT_BLOCKED
            ):
                raise ValueError("MOVEMENT_BLOCKED has an invalid semantic or route projection")
        elif self.operational_override is TriageOperationalOverrideV1.EVENT_ROUTE_OVERRIDE:
            if not (
                self.outcome is TriageOutcomeV1.DECIDED
                and self.source is TriageSourceV1.EVENT_RULE
                and self.semantic_intent is IntentType.CORE_SYSTEM
                and self.route in {Route.NOOP, Route.MOTOR}
                and self.reason_code is TriageReasonCodeV1.EVENT_RULE_ACCEPTED
            ):
                raise ValueError("EVENT_ROUTE_OVERRIDE is limited to reviewed CORE_SYSTEM routes")

        self._validate_reason_code()
        return self

    def _validate_reason_code(self) -> None:
        if self.operational_override is TriageOperationalOverrideV1.MOVEMENT_BLOCKED:
            return
        if self.outcome is TriageOutcomeV1.UNCERTAIN:
            if self.reason_code is not TriageReasonCodeV1.NO_ACCEPTABLE_CANDIDATE:
                raise ValueError("uncertain outcomes require NO_ACCEPTABLE_CANDIDATE")
            if self.model_status not in {
                TriageModelStatusV1.LOW_CONFIDENCE,
                TriageModelStatusV1.VETOED,
            } or self.fallback_status not in {
                TriageFallbackStatusV1.NO_MATCH,
                TriageFallbackStatusV1.VETOED,
            }:
                raise ValueError("uncertain outcome contradicts causal statuses")
            return
        if self.outcome is TriageOutcomeV1.FAILED:
            expected = self._expected_failure_reason()
            if self.reason_code is not expected:
                raise ValueError("failed outcome contradicts failure-reason precedence")
            return
        if self.source is TriageSourceV1.MODEL:
            expected = TriageReasonCodeV1.MODEL_ACCEPTED
        elif self.source is TriageSourceV1.FALLBACK:
            expected = TriageReasonCodeV1.FALLBACK_ACCEPTED
        elif self.source is TriageSourceV1.LEXICAL:
            expected = TriageReasonCodeV1.LEXICAL_RULE_ACCEPTED
        elif self.accepted_rule_id == "event.empty_command":
            expected = TriageReasonCodeV1.EMPTY_COMMAND
        elif self.accepted_rule_id == "event.unsupported":
            expected = TriageReasonCodeV1.UNSUPPORTED_EVENT
        else:
            expected = TriageReasonCodeV1.EVENT_RULE_ACCEPTED
        if self.reason_code is not expected:
            raise ValueError("reason_code contradicts the accepted source")

    def _expected_failure_reason(self) -> TriageReasonCodeV1:
        if self.fallback_status is TriageFallbackStatusV1.ERROR:
            return TriageReasonCodeV1.FALLBACK_ERROR
        if self.fallback_status not in {
            TriageFallbackStatusV1.NO_MATCH,
            TriageFallbackStatusV1.VETOED,
        }:
            raise ValueError("failed outcome requires a terminal fallback blocker")
        mapping = {
            TriageModelStatusV1.UNAVAILABLE: TriageReasonCodeV1.MODEL_UNAVAILABLE,
            TriageModelStatusV1.ERROR: TriageReasonCodeV1.MODEL_ERROR,
            TriageModelStatusV1.INCOMPATIBLE_LABEL: (
                TriageReasonCodeV1.MODEL_LABEL_INCOMPATIBLE
            ),
        }
        try:
            return mapping[self.model_status]
        except KeyError as exc:
            raise ValueError("failed outcome lacks a technical model failure") from exc

    def evolve(self, **updates: Any) -> "TriageDecisionV1":
        data = self.model_dump(mode="python")
        data.update(updates)
        return type(self).model_validate(data)

    def to_legacy_dict(self, *, confidence: Any) -> dict[str, Any]:
        """Unica proyeccion compatible al dict historico de Capa 0."""

        if self.operational_override is TriageOperationalOverrideV1.MOVEMENT_BLOCKED:
            legacy_intent = IntentType.NOOP.value
            legacy_route = Route.NOOP.value
        elif self.reason_code is TriageReasonCodeV1.UNSUPPORTED_EVENT:
            legacy_intent = IntentType.UNKNOWN.value
            legacy_route = Route.NOOP.value
        elif self.semantic_intent is None or self.route is None:
            legacy_intent = IntentType.UNKNOWN.value
            legacy_route = Route.PERSONA.value
        else:
            legacy_intent = self.semantic_intent.value
            legacy_route = self.route.value

        requires_memory = (
            legacy_route in {Route.PERSONA.value, Route.ORACLE.value}
            or legacy_intent
            in {
                IntentType.PERCEPTION.value,
                IntentType.MEMORY_QUERY.value,
                IntentType.MEMORY_UPDATE.value,
            }
        )
        return {
            "intent": legacy_intent,
            "route": legacy_route,
            "confidence": round(float(confidence), 2),
            "requires_memory": requires_memory,
            "requires_oracle": legacy_route == Route.ORACLE.value,
            "reason": self.human_reason,
            "classification": self.model_dump(mode="json"),
        }
