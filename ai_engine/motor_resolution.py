"""
Estructuras auditables para resolucion de objetivos de Capa 1.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class InteractionType(str, Enum):
    """Vocabulario canonico de interacciones fisicas despachables."""

    MIRAR = "mirar"
    TOCAR = "tocar"
    INSPECCIONAR = "inspeccionar"
    GOLPEAR = "golpear"
    USAR = "usar"
    ACTIVAR = "activar"


ResolutionStatus = Literal["ready", "requires_clarification", "not_found", "not_allowed", "unsafe"]
MotorDiagnosisStatus = Literal[
    "sin_intencion",
    "no_permitida",
    "falta_objetivo",
    "ambigua",
    "lista",
    "requires_clarification",
    "not_found",
    "not_allowed",
    "unsafe",
]


@dataclass(slots=True)
class CandidateMatch:
    target_id: str
    display_name: str
    confidence: float
    producer_object_id: str | None = None
    runtime_object_id: str | None = None
    score_components: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    legacy_nombre_objeto: str | None = None
    affordance_decision: dict[str, Any] = field(default_factory=dict)
    distance: float | None = None
    position: dict[str, float] | None = None
    unmatched_descriptors: list[str] = field(default_factory=list)
    known_aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "display_name": self.display_name,
            "producer_object_id": self.producer_object_id,
            "runtime_object_id": self.runtime_object_id,
            "legacy_nombre_objeto": self.legacy_nombre_objeto or self.display_name,
            "confidence": round(float(self.confidence), 4),
            "score_components": dict(self.score_components),
            "affordance_decision": dict(self.affordance_decision),
            "distance": self.distance,
            "position": dict(self.position) if self.position is not None else None,
            "unmatched_descriptors": list(self.unmatched_descriptors),
            "known_aliases": list(self.known_aliases),
            "reason": self.reason,
        }


@dataclass(slots=True)
class CandidateCorrection:
    requested: str
    suggested_id: str
    suggested_display_name: str
    score: float
    requires_confirmation: bool = True
    legacy_nombre_objeto: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "suggested_id": self.suggested_id,
            "suggested_display_name": self.suggested_display_name,
            "legacy_nombre_objeto": self.legacy_nombre_objeto or self.suggested_display_name,
            "score": round(float(self.score), 4),
            "requires_confirmation": bool(self.requires_confirmation),
            "reason": self.reason,
        }


@dataclass(slots=True)
class MotorResolution:
    status: ResolutionStatus
    action: str | None = None
    interaction_type: InteractionType | None = None
    target_id: str | None = None
    target_display_name: str | None = None
    target_producer_object_id: str | None = None
    target_runtime_object_id: str | None = None
    legacy_nombre_objeto: str | None = None
    confidence: float = 0.0
    candidates: list[CandidateMatch] = field(default_factory=list)
    candidate_correction: CandidateCorrection | None = None
    reason: str = ""
    audit: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.interaction_type is not None and not isinstance(
            self.interaction_type,
            InteractionType,
        ):
            raise TypeError("interaction_type must be InteractionType or None")
        if (
            self.interaction_type is not None
            and self.action != "interactuar_objeto_3d"
        ):
            raise ValueError(
                "interaction_type is only valid for interactuar_objeto_3d"
            )
        if (
            self.status == "ready"
            and self.action == "interactuar_objeto_3d"
            and self.interaction_type is None
        ):
            raise ValueError(
                "ready interactuar_objeto_3d resolution requires interaction_type"
            )

    @property
    def ready(self) -> bool:
        return self.status == "ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "interaction_type": (
                self.interaction_type.value
                if self.interaction_type is not None
                else None
            ),
            "target_id": self.target_id,
            "target_display_name": self.target_display_name,
            "target_producer_object_id": self.target_producer_object_id,
            "target_runtime_object_id": self.target_runtime_object_id,
            "legacy_nombre_objeto": self.legacy_nombre_objeto,
            "confidence": round(float(self.confidence), 4),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "candidate_correction": self.candidate_correction.to_dict() if self.candidate_correction else None,
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


@dataclass(frozen=True, slots=True)
class MotorDiagnosis(Mapping[str, Any]):
    """Resultado tipado que transporta una unica resolucion hasta MotorAction.

    Implementa la interfaz de mapping usada por consumidores legacy, pero la
    ruta productiva conserva ``resolucion_motor`` como autoridad tipada en vez
    de reconstruirla desde metadata o texto.
    """

    estado: MotorDiagnosisStatus
    accion: str | None = None
    parametros: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    resolucion_motor: MotorResolution | None = None

    def __post_init__(self) -> None:
        valid_states = {
            "sin_intencion",
            "no_permitida",
            "falta_objetivo",
            "ambigua",
            "lista",
            "requires_clarification",
            "not_found",
            "not_allowed",
            "unsafe",
        }
        if self.estado not in valid_states:
            raise ValueError("unknown motor diagnosis state")
        if self.parametros is not None and not isinstance(self.parametros, dict):
            raise TypeError("motor diagnosis parameters must be a dict or None")
        if not isinstance(self.metadata, dict):
            raise TypeError("motor diagnosis metadata must be a dict")

        object.__setattr__(
            self,
            "parametros",
            deepcopy(self.parametros) if self.parametros is not None else None,
        )
        object.__setattr__(self, "metadata", deepcopy(self.metadata))

        if self.estado == "lista":
            if not str(self.accion or "").strip() or self.parametros is None:
                raise ValueError("ready motor diagnosis requires action and parameters")
        elif self.parametros is not None:
            raise ValueError("non-ready motor diagnosis cannot materialize parameters")

        resolucion = self.resolucion_motor
        if resolucion is None:
            return
        expected_state = "lista" if resolucion.ready else resolucion.status
        if self.estado != expected_state:
            raise ValueError("motor resolution contradicts diagnosis state")
        if resolucion.action and resolucion.action != self.accion:
            raise ValueError("motor resolution contradicts diagnosis action")
        canonical_metadata = resolucion.to_metadata()
        if any(
            self.metadata.get(key) != value
            for key, value in canonical_metadata.items()
        ):
            raise ValueError("motor diagnosis metadata contradicts typed resolution")

    @property
    def ready(self) -> bool:
        return self.estado == "lista"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "estado": self.estado,
            "accion": self.accion,
            "parametros": deepcopy(self.parametros),
        }
        if self.metadata:
            payload["metadata"] = deepcopy(self.metadata)
        if self.resolucion_motor is not None and not self.resolucion_motor.ready:
            payload["resolucion_motor"] = self.resolucion_motor.to_dict()
        return payload

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())
