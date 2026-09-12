"""Proyecciones internas y deterministas de IdentitySnapshotV1."""

from __future__ import annotations

from dataclasses import dataclass

from core.identity_snapshot import (
    IdentitySnapshotV1,
    PersonalitySnapshotV1,
    SelfStateSnapshotV1,
    StaticSelfV1,
)


@dataclass(frozen=True, slots=True)
class IdentityContextV1:
    schema_version: int
    generation: int
    canonical_name: str
    agent_kind: str
    state: SelfStateSnapshotV1
    traits: PersonalitySnapshotV1

    def __post_init__(self) -> None:
        # Reutiliza las invariantes del contrato fuente; no crea otra semantica.
        IdentitySnapshotV1(
            schema_version=self.schema_version,
            generation=self.generation,
            self=StaticSelfV1(self.canonical_name, self.agent_kind),
            state=self.state,
            traits=self.traits,
        )

    @classmethod
    def from_snapshot(cls, snapshot: IdentitySnapshotV1) -> "IdentityContextV1":
        if not isinstance(snapshot, IdentitySnapshotV1):
            raise TypeError("snapshot debe ser IdentitySnapshotV1")
        return cls(
            schema_version=snapshot.schema_version,
            generation=snapshot.generation,
            canonical_name=snapshot.self.canonical_name,
            agent_kind=snapshot.self.agent_kind,
            state=snapshot.state,
            traits=snapshot.traits,
        )


def _state_lines(context: IdentityContextV1) -> str:
    return (
        f"- energia: {context.state.energia:.2f}\n"
        f"- estres: {context.state.estres:.2f}\n"
        f"- foco: {context.state.foco:.2f}"
    )


def _trait_lines(context: IdentityContextV1) -> str:
    traits = context.traits
    return (
        f"- resiliencia: {traits.resiliencia:.4f}\n"
        f"- impulsividad: {traits.impulsividad:.4f}\n"
        f"- paciencia: {traits.paciencia:.4f}\n"
        f"- sociabilidad: {traits.sociabilidad:.4f}\n"
        f"- metodico: {traits.metodico:.4f}"
    )


def render_persona_identity(context: IdentityContextV1) -> str:
    """Render social; los facts proceden exclusivamente del contexto tipado."""
    return (
        "=== IDENTIDAD CANONICA ===\n"
        f"- nombre: {context.canonical_name}\n"
        f"- tipo de agente: {context.agent_kind}\n"
        "=== ESTADO INTERNO APROBADO ===\n"
        f"{_state_lines(context)}\n"
        "=== RASGOS PRODUCTIVOS ===\n"
        f"{_trait_lines(context)}"
    )


def render_reasoning_identity(context: IdentityContextV1) -> str:
    """Render tecnico; conserva la misma semantica sin compartir prompt gigante."""
    return (
        "Identidad canonica interna (schema v1):\n"
        f"nombre={context.canonical_name}\n"
        f"agent_kind={context.agent_kind}\n"
        "estado:\n"
        f"{_state_lines(context)}\n"
        "rasgos:\n"
        f"{_trait_lines(context)}"
    )
