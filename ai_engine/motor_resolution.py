"""
Estructuras auditables para resolucion de objetivos de Capa 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ResolutionStatus = Literal["ready", "requires_clarification", "not_found", "not_allowed", "unsafe"]


@dataclass(slots=True)
class CandidateMatch:
    target_id: str
    display_name: str
    confidence: float
    score_components: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    legacy_nombre_objeto: str | None = None
    affordance_decision: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "display_name": self.display_name,
            "legacy_nombre_objeto": self.legacy_nombre_objeto or self.display_name,
            "confidence": round(float(self.confidence), 4),
            "score_components": dict(self.score_components),
            "affordance_decision": dict(self.affordance_decision),
            "reason": self.reason,
        }


@dataclass(slots=True)
class MotorResolution:
    status: ResolutionStatus
    action: str | None = None
    target_id: str | None = None
    target_display_name: str | None = None
    legacy_nombre_objeto: str | None = None
    confidence: float = 0.0
    candidates: list[CandidateMatch] = field(default_factory=list)
    reason: str = ""
    audit: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "target_id": self.target_id,
            "target_display_name": self.target_display_name,
            "legacy_nombre_objeto": self.legacy_nombre_objeto,
            "confidence": round(float(self.confidence), 4),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "reason": self.reason,
            "audit": dict(self.audit),
        }

    def to_metadata(self) -> dict[str, Any]:
        return {
            "motor_resolution": {
                key: value
                for key, value in self.to_dict().items()
                if key != "audit"
            },
            "motor_audit": dict(self.audit),
        }
