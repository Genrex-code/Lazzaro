"""
Estructuras auditables para resolucion de objetivos de Capa 1.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import Any, Literal


class InteractionType(str, Enum):
    """Vocabulario canonico de interacciones fisicas despachables."""

    MIRAR = "mirar"
    TOCAR = "tocar"
    INSPECCIONAR = "inspeccionar"
    GOLPEAR = "golpear"
    USAR = "usar"
    ACTIVAR = "activar"


class MotorActionKindV1(StrEnum):
    """Vocabulario cerrado de acciones pertenecientes a Capa 1."""

    NONE = "none"
    NAVIGATE_TO_OBJECT = "navegar_a_objeto"
    NAVIGATE_TO_POSITION = "navegar_a_posicion"
    INTERACT_OBJECT_3D = "interactuar_objeto_3d"
    GRAB_OBJECT = "agarrar_objeto"
    RELEASE_OBJECT = "soltar_objeto"
    MOVE_AWAY_FROM_OBJECT = "alejarse_de_objeto"
    ROTATE_AVATAR = "girar_avatar"
    APPLY_FORCE = "aplicar_fuerza"
    CANCEL_TASK = "cancelar_tarea"


class ResolutionStatusV1(StrEnum):
    READY = "ready"
    REQUIRES_CLARIFICATION = "requires_clarification"
    NOT_FOUND = "not_found"
    NOT_ALLOWED = "not_allowed"
    UNSAFE = "unsafe"


class MotorDiagnosisStatusV1(StrEnum):
    NO_INTENT = "sin_intencion"
    NOT_ALLOWED = "no_permitida"
    MISSING_TARGET = "falta_objetivo"
    AMBIGUOUS = "ambigua"
    READY = "lista"
    REQUIRES_CLARIFICATION = "requires_clarification"
    NOT_FOUND = "not_found"
    TARGET_NOT_ALLOWED = "not_allowed"
    UNSAFE = "unsafe"


class _ImmutableSnapshot(Mapping[str, Any]):
    """Mapping recursivo inmutable; ``to_dict`` sigue proyectando dict/list."""

    __slots__ = ("__data",)

    def __init__(self, value: Mapping[str, Any]) -> None:
        object.__setattr__(
            self,
            "_ImmutableSnapshot__data",
            MappingProxyType(
                {key: _freeze_snapshot(item) for key, item in value.items()}
            ),
        )

    def __getitem__(self, key: str) -> Any:
        return self.__data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.__data)

    def __len__(self) -> int:
        return len(self.__data)

    def __repr__(self) -> str:
        return f"_ImmutableSnapshot({dict(self.__data)!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Mapping):
            return False
        return _thaw_snapshot(self) == _thaw_snapshot(other)


class _ImmutableListSnapshot(tuple):
    """Distingue listas congeladas para restaurarlas como listas legacy."""


def _freeze_snapshot(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _ImmutableSnapshot(value)
    if isinstance(value, list):
        return _ImmutableListSnapshot(_freeze_snapshot(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_snapshot(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_snapshot(item) for item in value)
    return deepcopy(value)


def _thaw_snapshot(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_snapshot(item) for key, item in value.items()}
    if isinstance(value, _ImmutableListSnapshot):
        return [_thaw_snapshot(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_thaw_snapshot(item) for item in value)
    if isinstance(value, frozenset):
        return {_thaw_snapshot(item) for item in value}
    return deepcopy(value)


# Aliases de typing conservados para imports existentes. La autoridad runtime
# son los enums anteriores y su validacion explicita en __post_init__.
ResolutionStatus = Literal["ready", "requires_clarification", "not_found", "not_allowed", "unsafe"]
MotorDiagnosisStatus = Literal[
    "sin_intencion", "no_permitida", "falta_objetivo", "ambigua", "lista",
    "requires_clarification", "not_found", "not_allowed", "unsafe",
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


@dataclass(frozen=True, slots=True)
class MotorResolution:
    status: ResolutionStatus
    action: MotorActionKindV1 | str | None = None
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
        try:
            normalized_status = ResolutionStatusV1(self.status)
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown motor resolution status") from exc
        object.__setattr__(self, "status", normalized_status.value)

        if self.action is not None:
            try:
                normalized_action = MotorActionKindV1(self.action)
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown motor resolution action") from exc
            object.__setattr__(self, "action", normalized_action.value)

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
    accion: MotorActionKindV1 | str | None = None
    parametros: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    resolucion_motor: MotorResolution | None = None

    def __post_init__(self) -> None:
        try:
            normalized_state = MotorDiagnosisStatusV1(self.estado)
        except (TypeError, ValueError) as exc:
            raise ValueError("unknown motor diagnosis state") from exc
        object.__setattr__(self, "estado", normalized_state.value)

        if self.accion is not None:
            try:
                normalized_action = MotorActionKindV1(self.accion)
            except (TypeError, ValueError) as exc:
                raise ValueError("unknown motor diagnosis action") from exc
            object.__setattr__(self, "accion", normalized_action.value)
        if self.parametros is not None and not isinstance(self.parametros, dict):
            raise TypeError("motor diagnosis parameters must be a dict or None")
        if not isinstance(self.metadata, dict):
            raise TypeError("motor diagnosis metadata must be a dict")

        parameter_snapshot = (
            deepcopy(self.parametros) if self.parametros is not None else None
        )
        metadata_snapshot = deepcopy(self.metadata)

        if self.estado == "lista":
            if not str(self.accion or "").strip() or parameter_snapshot is None:
                raise ValueError("ready motor diagnosis requires action and parameters")
        elif parameter_snapshot is not None:
            raise ValueError("non-ready motor diagnosis cannot materialize parameters")

        resolucion = self.resolucion_motor
        if resolucion is not None:
            expected_state = "lista" if resolucion.ready else resolucion.status
            if self.estado != expected_state:
                raise ValueError("motor resolution contradicts diagnosis state")
            if resolucion.action and resolucion.action != self.accion:
                raise ValueError("motor resolution contradicts diagnosis action")
            canonical_metadata = resolucion.to_metadata()
            if any(
                metadata_snapshot.get(key) != value
                for key, value in canonical_metadata.items()
            ):
                raise ValueError("motor diagnosis metadata contradicts typed resolution")

        object.__setattr__(
            self,
            "parametros",
            _freeze_snapshot(parameter_snapshot)
            if parameter_snapshot is not None
            else None,
        )
        object.__setattr__(self, "metadata", _freeze_snapshot(metadata_snapshot))

    @property
    def ready(self) -> bool:
        return self.estado == "lista"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "estado": self.estado,
            "accion": self.accion,
            "parametros": _thaw_snapshot(self.parametros),
        }
        if self.metadata:
            payload["metadata"] = _thaw_snapshot(self.metadata)
        if self.resolucion_motor is not None and not self.resolucion_motor.ready:
            payload["resolucion_motor"] = self.resolucion_motor.to_dict()
        return payload

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())
