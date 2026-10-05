"""
Validador de affordances para Capa 1.

No resuelve nombres ni habla con Unity. Solo decide si una accion fisica aplica
a un `PerceivedObject` ya seleccionado.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictInt, field_validator, model_validator

from .perception_models import PerceivedObject, normalize_text


ACTION_REQUIRED_AFFORDANCES: dict[str, set[str]] = {
    "agarrar_objeto": {"agarrable"},
    "aplicar_fuerza": {"empujable"},
    "interactuar_objeto_3d": {"mirar", "tocar", "activable", "inspeccionable"},
}

INTERACTION_REQUIRED_AFFORDANCES: dict[str, set[str]] = {
    "mirar": {"mirar", "inspeccionable"},
    "observar": {"mirar", "inspeccionable"},
    "inspeccionar": {"mirar", "inspeccionable"},
    "tocar": {"tocar"},
    "golpear": {"empujable", "tocar"},
    "usar": {"activable", "inspeccionable"},
    "activar": {"activable"},
}

ACTION_OPTIONAL_AFFORDANCES: dict[str, set[str]] = {
    "navegar_a_objeto": {"transitable", "punto_refugio", "da_sombra", "cubre", "descanso"},
    "alejarse_de_objeto": {"obstaculo", "peligro", "bloquea_paso"},
}

AFFORDANCE_QUERY_TERMS: dict[str, set[str]] = {
    "cubre": {"cubre", "cubrir", "cubrirme", "cubrirte", "protege", "proteger", "refugio"},
    "da_sombra": {"sombra", "sombreado", "cubrirme", "cubrirte", "refugio"},
    "punto_refugio": {"refugio", "esconder", "esconderme", "cubrirme", "cubrirte"},
    "ilumina": {"luz", "ilumina", "iluminar", "claro"},
    "descanso": {"descansar", "descanso", "reposar"},
    "peligro": {"peligro", "peligroso", "amenaza"},
    "recurso": {"recurso", "material", "suministro"},
    "nuevo": {"nuevo", "nueva", "desconocido", "desconocida", "curioso", "curiosa", "interesante"},
    "clicable": {"clic", "click", "presiona", "boton"},
    "escribible": {"escribe", "campo", "texto"},
}


class SafetyDisposition(str, Enum):
    SAFE = "SAFE"
    CAUTION = "CAUTION"
    UNSAFE = "UNSAFE"
    INDETERMINATE = "INDETERMINATE"


class SafetyReasonCode(str, Enum):
    NO_HAZARD = "NO_HAZARD"
    HAZARD_AVOIDANCE = "HAZARD_AVOIDANCE"
    HAZARD_OBSERVATION = "HAZARD_OBSERVATION"
    HAZARD_APPROACH = "HAZARD_APPROACH"
    HAZARD_CONTACT = "HAZARD_CONTACT"
    HAZARD_MANIPULATION = "HAZARD_MANIPULATION"
    HAZARD_FORCE = "HAZARD_FORCE"
    HAZARD_INTERACTION_UNSPECIFIED = "HAZARD_INTERACTION_UNSPECIFIED"
    HAZARD_ACTION_UNSUPPORTED = "HAZARD_ACTION_UNSUPPORTED"
    SAFETY_ASSESSMENT_FAILED = "SAFETY_ASSESSMENT_FAILED"


_SAFETY_POLICY: dict[SafetyReasonCode, tuple[SafetyDisposition, float]] = {
    SafetyReasonCode.NO_HAZARD: (SafetyDisposition.SAFE, 0.0),
    SafetyReasonCode.HAZARD_AVOIDANCE: (SafetyDisposition.SAFE, 0.0),
    SafetyReasonCode.HAZARD_OBSERVATION: (SafetyDisposition.CAUTION, 5.0),
    SafetyReasonCode.HAZARD_APPROACH: (SafetyDisposition.UNSAFE, 20.0),
    SafetyReasonCode.HAZARD_CONTACT: (SafetyDisposition.UNSAFE, 20.0),
    SafetyReasonCode.HAZARD_MANIPULATION: (SafetyDisposition.UNSAFE, 20.0),
    SafetyReasonCode.HAZARD_FORCE: (SafetyDisposition.UNSAFE, 20.0),
    SafetyReasonCode.HAZARD_INTERACTION_UNSPECIFIED: (SafetyDisposition.INDETERMINATE, 20.0),
    SafetyReasonCode.HAZARD_ACTION_UNSUPPORTED: (SafetyDisposition.INDETERMINATE, 20.0),
    SafetyReasonCode.SAFETY_ASSESSMENT_FAILED: (SafetyDisposition.INDETERMINATE, 20.0),
}


class SafetyAssessmentV1(BaseModel):
    """Autoridad categorica de seguridad para evidencia de accion disponible."""

    contract_version: StrictInt = 1
    disposition: SafetyDisposition
    reason_code: SafetyReasonCode
    ranking_penalty: float

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("contract_version", mode="before")
    @classmethod
    def _strict_contract_version(cls, value: Any) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("contract_version must be the exact integer 1")
        return value

    @field_validator("ranking_penalty", mode="before")
    @classmethod
    def _bounded_finite_penalty(cls, value: Any) -> float:
        if isinstance(value, bool) or type(value) not in (int, float):
            raise ValueError("ranking_penalty must be an ordinary int or float")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("ranking_penalty must be finite")
        if numeric < 0.0 or numeric > 20.0:
            raise ValueError("ranking_penalty must be between 0 and 20")
        return numeric

    @model_validator(mode="after")
    def _canonical_reason_mapping(self) -> "SafetyAssessmentV1":
        expected_disposition, expected_penalty = _SAFETY_POLICY[self.reason_code]
        if self.disposition != expected_disposition:
            raise ValueError("reason_code contradicts safety disposition")
        if self.ranking_penalty != expected_penalty:
            raise ValueError("reason_code contradicts ranking penalty")
        return self

    @property
    def allowed_by_safety(self) -> bool:
        return self.disposition in {SafetyDisposition.SAFE, SafetyDisposition.CAUTION}

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def _assessment_for(reason_code: SafetyReasonCode) -> SafetyAssessmentV1:
    disposition, ranking_penalty = _SAFETY_POLICY[reason_code]
    return SafetyAssessmentV1(
        disposition=disposition,
        reason_code=reason_code,
        ranking_penalty=ranking_penalty,
    )


@dataclass(slots=True)
class AffordanceDecision:
    allowed: bool
    status: str
    action: str | None
    object_id: str
    display_name: str
    safety_assessment: SafetyAssessmentV1
    required: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def risk_penalty(self) -> float:
        """Proyeccion legacy; la evaluacion tipada es la unica autoridad."""

        return self.safety_assessment.ranking_penalty

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "status": self.status,
            "action": self.action,
            "object_id": self.object_id,
            "display_name": self.display_name,
            "required": list(self.required),
            "present": list(self.present),
            "missing": list(self.missing),
            "matched": list(self.matched),
            "risk_penalty": round(float(self.risk_penalty), 4),
            "safety_assessment": self.safety_assessment.to_dict(),
            "reason": self.reason,
        }


class AffordanceResolver:
    """Evalua affordances canonicas para acciones fisicas."""

    def validate(
        self,
        obj: PerceivedObject,
        action: str | None,
        *,
        interaction_type: str | None = None,
    ) -> AffordanceDecision:
        try:
            present = set(obj.affordances)
        except (TypeError, ValueError):
            present = set()
            assessment = _assessment_for(SafetyReasonCode.SAFETY_ASSESSMENT_FAILED)
        else:
            assessment = self._assess_safety_from_affordances(
                present,
                action,
                interaction_type=interaction_type,
            )
        required = self.required_for_action(action, interaction_type=interaction_type)
        matched = sorted(required & present)
        missing = sorted(required - present)

        if required and not matched:
            return AffordanceDecision(
                allowed=False,
                status="not_allowed",
                action=action,
                object_id=obj.id,
                display_name=obj.display_name,
                required=sorted(required),
                present=sorted(present),
                missing=missing,
                matched=[],
                safety_assessment=assessment,
                reason=f"La accion '{action}' no aplica a '{obj.display_name}'.",
            )

        if not assessment.allowed_by_safety:
            return AffordanceDecision(
                allowed=False,
                status="unsafe",
                action=action,
                object_id=obj.id,
                display_name=obj.display_name,
                required=sorted(required),
                present=sorted(present),
                missing=missing,
                matched=matched,
                safety_assessment=assessment,
                reason=(
                    f"'{obj.display_name}' no tiene evidencia suficiente para autorizar esa accion."
                    if assessment.disposition == SafetyDisposition.INDETERMINATE
                    else f"'{obj.display_name}' esta marcado como peligroso para esa accion."
                ),
            )

        return AffordanceDecision(
            allowed=True,
            status="allowed",
            action=action,
            object_id=obj.id,
            display_name=obj.display_name,
            required=sorted(required),
            present=sorted(present),
            missing=missing,
            matched=matched,
            safety_assessment=assessment,
            reason="Affordances compatibles con la accion.",
        )

    def score(
        self,
        obj: PerceivedObject,
        *,
        action: str | None = None,
        requested_affordances: set[str] | None = None,
        interaction_type: str | None = None,
    ) -> float:
        present = set(obj.affordances)
        requested = requested_affordances or set()
        if requested:
            return 100.0 if requested & present else 0.0

        required = self.required_for_action(action, interaction_type=interaction_type)
        if required:
            return 100.0 if required & present else 0.0

        optional = ACTION_OPTIONAL_AFFORDANCES.get(str(action or ""), set())
        if optional and optional & present:
            return 100.0
        return 100.0

    def requested_from_query(self, query: str) -> set[str]:
        normalized_query = normalize_text(query).replace("_", " ")
        tokens = set(normalized_query.split())
        requested: set[str] = set()
        for affordance, terms in AFFORDANCE_QUERY_TERMS.items():
            if tokens & terms or any(term in normalized_query for term in terms):
                requested.add(affordance)
        return requested

    @staticmethod
    def required_for_action(action: str | None, *, interaction_type: str | None = None) -> set[str]:
        normalized_action = str(action or "").strip()
        if normalized_action == "interactuar_objeto_3d" and interaction_type:
            normalized_type = normalize_text(interaction_type)
            return set(INTERACTION_REQUIRED_AFFORDANCES.get(normalized_type, ACTION_REQUIRED_AFFORDANCES[normalized_action]))
        return set(ACTION_REQUIRED_AFFORDANCES.get(normalized_action, set()))

    @staticmethod
    def assess_safety(
        obj: PerceivedObject,
        action: str | None,
        *,
        interaction_type: str | None = None,
    ) -> SafetyAssessmentV1:
        try:
            affordances = set(obj.affordances)
        except (TypeError, ValueError):
            return _assessment_for(SafetyReasonCode.SAFETY_ASSESSMENT_FAILED)

        return AffordanceResolver._assess_safety_from_affordances(
            affordances,
            action,
            interaction_type=interaction_type,
        )

    @staticmethod
    def _assess_safety_from_affordances(
        affordances: set[str],
        action: str | None,
        *,
        interaction_type: str | None = None,
    ) -> SafetyAssessmentV1:

        if "peligro" not in affordances:
            return _assessment_for(SafetyReasonCode.NO_HAZARD)

        normalized_action = normalize_text(action).replace(" ", "_")
        if normalized_action == "alejarse_de_objeto":
            reason_code = SafetyReasonCode.HAZARD_AVOIDANCE
        elif normalized_action == "navegar_a_objeto":
            reason_code = SafetyReasonCode.HAZARD_APPROACH
        elif normalized_action == "agarrar_objeto":
            reason_code = SafetyReasonCode.HAZARD_MANIPULATION
        elif normalized_action == "aplicar_fuerza":
            reason_code = SafetyReasonCode.HAZARD_FORCE
        elif normalized_action == "interactuar_objeto_3d":
            if interaction_type is None or not str(interaction_type).strip():
                reason_code = SafetyReasonCode.HAZARD_INTERACTION_UNSPECIFIED
            else:
                normalized_type = normalize_text(interaction_type).replace(" ", "_")
                if normalized_type in {"mirar", "observar", "inspeccionar"}:
                    reason_code = SafetyReasonCode.HAZARD_OBSERVATION
                elif normalized_type in {"tocar", "golpear"}:
                    reason_code = SafetyReasonCode.HAZARD_CONTACT
                elif normalized_type in {"usar", "activar"}:
                    reason_code = SafetyReasonCode.HAZARD_MANIPULATION
                else:
                    reason_code = SafetyReasonCode.HAZARD_ACTION_UNSUPPORTED
        elif normalized_action in {"describir", "mirar", "observar", "inspeccionar"}:
            reason_code = SafetyReasonCode.HAZARD_OBSERVATION
        elif normalized_action in {"tocar", "golpear"}:
            reason_code = SafetyReasonCode.HAZARD_CONTACT
        elif normalized_action in {"usar", "activar"}:
            reason_code = SafetyReasonCode.HAZARD_MANIPULATION
        else:
            reason_code = SafetyReasonCode.HAZARD_ACTION_UNSUPPORTED
        return _assessment_for(reason_code)

    @staticmethod
    def risk_penalty(
        obj: PerceivedObject,
        action: str | None,
        *,
        interaction_type: str | None = None,
    ) -> float:
        """Compatibilidad para probes historicos; proyecta la autoridad tipada."""

        return AffordanceResolver.assess_safety(
            obj,
            action,
            interaction_type=interaction_type,
        ).ranking_penalty
