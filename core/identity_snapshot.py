"""Contratos internos para la identidad canonica observable de Lazzaro.

ROOT-016 mantiene los facts estaticos separados de los owners mutables. Los
snapshots son valores inmutables, boot-scoped y no contienen referencias vivas.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import math
import threading
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from core.behavior_core import BehaviorCore


def _strict_string(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} debe ser str")
    if not value.strip():
        raise ValueError(f"{field_name} no puede estar vacio")
    return value


def _strict_number(value: object, field_name: str, minimum: float, maximum: float) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{field_name} debe ser int o float finito")
    canonical = float(value)
    if not math.isfinite(canonical):
        raise ValueError(f"{field_name} debe ser finito")
    if not minimum <= canonical <= maximum:
        raise ValueError(f"{field_name} fuera de rango [{minimum}, {maximum}]")
    return canonical


@dataclass(frozen=True, slots=True)
class StaticSelfV1:
    canonical_name: str
    agent_kind: Literal["digital_agent"]

    def __post_init__(self) -> None:
        name = _strict_string(self.canonical_name, "canonical_name")
        kind = _strict_string(self.agent_kind, "agent_kind")
        if kind != "digital_agent":
            raise ValueError("agent_kind debe ser exactamente digital_agent")
        object.__setattr__(self, "canonical_name", name)
        object.__setattr__(self, "agent_kind", kind)


def build_static_self_v1() -> StaticSelfV1:
    """Factory productiva unica para los facts estaticos V1."""
    return StaticSelfV1(canonical_name="Lazzaro", agent_kind="digital_agent")


@dataclass(frozen=True, slots=True)
class SelfStateSnapshotV1:
    energia: float
    estres: float
    foco: float

    def __post_init__(self) -> None:
        for field in fields(self):
            object.__setattr__(
                self,
                field.name,
                _strict_number(getattr(self, field.name), field.name, 0.0, 100.0),
            )


@dataclass(frozen=True, slots=True)
class PersonalitySnapshotV1:
    resiliencia: float
    impulsividad: float
    paciencia: float
    sociabilidad: float
    metodico: float

    def __post_init__(self) -> None:
        for field in fields(self):
            object.__setattr__(
                self,
                field.name,
                _strict_number(getattr(self, field.name), field.name, -1.0, 1.0),
            )


@dataclass(frozen=True, slots=True)
class IdentityComponentsV1:
    """State y traits ya capturados por su owner en un instante logico."""

    state: SelfStateSnapshotV1
    traits: PersonalitySnapshotV1

    def __post_init__(self) -> None:
        if not isinstance(self.state, SelfStateSnapshotV1):
            raise TypeError("state debe ser SelfStateSnapshotV1")
        if not isinstance(self.traits, PersonalitySnapshotV1):
            raise TypeError("traits debe ser PersonalitySnapshotV1")


@dataclass(frozen=True, slots=True)
class IdentitySnapshotV1:
    schema_version: int
    generation: int
    self: StaticSelfV1
    state: SelfStateSnapshotV1
    traits: PersonalitySnapshotV1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int:
            raise TypeError("schema_version debe ser int estricto")
        if self.schema_version != 1:
            raise ValueError("schema_version debe ser 1")
        if type(self.generation) is not int:
            raise TypeError("generation debe ser int estricto")
        if self.generation <= 0:
            raise ValueError("generation debe ser mayor que cero")
        if not isinstance(self.self, StaticSelfV1):
            raise TypeError("self debe ser StaticSelfV1")
        if not isinstance(self.state, SelfStateSnapshotV1):
            raise TypeError("state debe ser SelfStateSnapshotV1")
        if not isinstance(self.traits, PersonalitySnapshotV1):
            raise TypeError("traits debe ser PersonalitySnapshotV1")


class IdentitySnapshotProvider:
    """Captura serializada de los owners canonicos del runtime actual.

    El lock cubre captura, validacion y commit de generation. Si la captura o
    construccion falla, el contador no se consume.
    """

    def __init__(self, static_self: StaticSelfV1, behavior_core: "BehaviorCore") -> None:
        if not isinstance(static_self, StaticSelfV1):
            raise TypeError("static_self debe ser StaticSelfV1")
        if behavior_core is None or not callable(
            getattr(behavior_core, "capture_identity_components", None)
        ):
            raise TypeError("behavior_core debe exponer capture_identity_components")
        self._static_self = static_self
        self._behavior_core = behavior_core
        self._capture_lock = threading.Lock()
        self._generation = 0

    def capture(self) -> IdentitySnapshotV1:
        with self._capture_lock:
            state_values, trait_values = self._behavior_core.capture_identity_components()
            components = IdentityComponentsV1(
                state=SelfStateSnapshotV1(**state_values),
                traits=PersonalitySnapshotV1(**trait_values),
            )
            return self._build_snapshot_locked(components)

    def from_components(self, components: IdentityComponentsV1) -> IdentitySnapshotV1:
        """Publica componentes correlacionados sin volver a leer BehaviorCore."""
        with self._capture_lock:
            if not isinstance(components, IdentityComponentsV1):
                raise TypeError("components debe ser IdentityComponentsV1")
            return self._build_snapshot_locked(components)

    def _build_snapshot_locked(
        self,
        components: IdentityComponentsV1,
    ) -> IdentitySnapshotV1:
        next_generation = self._generation + 1
        snapshot = IdentitySnapshotV1(
            schema_version=1,
            generation=next_generation,
            self=self._static_self,
            state=components.state,
            traits=components.traits,
        )
        self._generation = next_generation
        return snapshot
