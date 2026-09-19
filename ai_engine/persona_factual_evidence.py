"""Turn-local evidence snapshots for the replaceable ROOT-017 Persona bridge.

These are capabilities for rendering, not truth scores or future CFC contracts.
No live owner, Chroma handle, or mutable source dictionary survives compilation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ai_engine.identity_projection import IdentityContextV1
from ai_engine.knowledge_provenance import (
    CacheLookupStatusV1,
    KnowledgeProducerKindV1,
)
from ai_engine.schemas import OracleResult
from ai_engine.structured_parse_types import StructuredParseStatus
from core.material_attestation import (
    MaterialAttestationStatus,
    MaterialAttestationValidation,
    MaterialEffectStatus,
)


class PersonaEvidenceSourceV1(str, Enum):
    CANONICAL_SELF = "CANONICAL_SELF"
    OPERATIONAL_STATE = "OPERATIONAL_STATE"
    CURRENT_PERCEPTION = "CURRENT_PERCEPTION"
    HISTORICAL_PERCEPTION = "HISTORICAL_PERCEPTION"
    CURRENT_USER = "CURRENT_USER"
    MEMORY_AGGREGATE = "MEMORY_AGGREGATE"
    ORACLE = "ORACLE"
    MATERIAL_OUTCOME = "MATERIAL_OUTCOME"


class PersonaTemporalScopeV1(str, Enum):
    STATIC = "STATIC"
    TURN = "TURN"
    CURRENT_EVENT = "CURRENT_EVENT"
    HISTORICAL = "HISTORICAL"
    UNKNOWN = "UNKNOWN"


class PersonaEvidenceKindV1(str, Enum):
    SELF_NAME = "SELF_NAME"
    AGENT_KIND = "AGENT_KIND"
    ENERGY = "ENERGY"
    STRESS = "STRESS"
    FOCUS = "FOCUS"
    OBJECT_NAME = "OBJECT_NAME"
    USER_TEXT = "USER_TEXT"
    MEMORY_TEXT = "MEMORY_TEXT"
    ORACLE_TEXT = "ORACLE_TEXT"
    MATERIAL_EFFECT = "MATERIAL_EFFECT"


@dataclass(frozen=True, slots=True)
class PersonaMaterialSnapshotV1:
    action_id: str
    effect_kind: str
    object_id: str
    status: MaterialEffectStatus


@dataclass(frozen=True, slots=True)
class PersonaMaterialCorrelationV1:
    """Action-ledger identity expected by this turn, never model supplied."""

    action_id: str
    effect_kind: str
    object_id: str


@dataclass(frozen=True, slots=True)
class PersonaEvidenceRefV1:
    version: int
    evidence_id: str
    source: PersonaEvidenceSourceV1
    scope: PersonaTemporalScopeV1
    kind: PersonaEvidenceKindV1
    allowed_templates: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Persona evidence version must be exact integer 1")
        if not self.evidence_id.startswith("pe-") or not self.allowed_templates:
            raise ValueError("Persona evidence requires an id and capabilities")


PersonaSnapshotValueV1 = str | float | PersonaMaterialSnapshotV1


@dataclass(frozen=True, slots=True)
class PersonaEvidenceSnapshotV1:
    ref: PersonaEvidenceRefV1
    value: PersonaSnapshotValueV1
    # Provenance is descriptive. It never grants DIRECT factual authority.
    provenance_tag: str = ""

    def __post_init__(self) -> None:
        if type(self.value) not in (str, float, PersonaMaterialSnapshotV1):
            raise TypeError("Persona evidence value must be an immutable snapshot")


@dataclass(frozen=True, slots=True)
class PersonaEvidenceBundleV1:
    version: int
    refs: tuple[PersonaEvidenceRefV1, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Persona evidence bundle version must be 1")
        ids = tuple(ref.evidence_id for ref in self.refs)
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate Persona evidence id")

    def public_index(self) -> dict[str, Any]:
        """Only IDs and capabilities cross the PromptPlan DATA boundary."""
        return {
            "version": self.version,
            "refs": [
                {
                    "id": ref.evidence_id,
                    "source": ref.source.value,
                    "scope": ref.scope.value,
                    "kind": ref.kind.value,
                    "allowed_templates": list(ref.allowed_templates),
                }
                for ref in self.refs
            ],
        }


@dataclass(frozen=True, slots=True)
class PersonaEvidenceRegistryV1:
    version: int
    snapshots: tuple[PersonaEvidenceSnapshotV1, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Persona evidence registry version must be 1")
        ids = tuple(item.ref.evidence_id for item in self.snapshots)
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate Persona evidence snapshot")

    def get(self, evidence_id: str) -> PersonaEvidenceSnapshotV1 | None:
        return next(
            (item for item in self.snapshots if item.ref.evidence_id == evidence_id),
            None,
        )


def compile_persona_evidence(
    *,
    identity: IdentityContextV1,
    current_user: str | None,
    memory_summary: str,
    current_event_details: dict[str, Any] | None,
    prior_perception_context: dict[str, Any] | None,
    oracle_result: OracleResult | None,
    material_attestation: MaterialAttestationValidation | None = None,
    material_correlation: PersonaMaterialCorrelationV1 | None = None,
    terminal_channel: bool = False,
) -> tuple[PersonaEvidenceBundleV1, PersonaEvidenceRegistryV1]:
    """Copy approved fields once; never infer currentness from Behavior's cache."""
    if not isinstance(identity, IdentityContextV1):
        raise TypeError("Persona evidence requires IdentityContextV1")
    snapshots: list[PersonaEvidenceSnapshotV1] = []

    def add(
        source: PersonaEvidenceSourceV1,
        scope: PersonaTemporalScopeV1,
        kind: PersonaEvidenceKindV1,
        value: PersonaSnapshotValueV1,
        templates: tuple[str, ...],
        provenance_tag: str = "",
    ) -> None:
        ref = PersonaEvidenceRefV1(
            1, f"pe-{len(snapshots) + 1:04d}", source, scope, kind, templates
        )
        snapshots.append(PersonaEvidenceSnapshotV1(ref, value, provenance_tag))

    add(PersonaEvidenceSourceV1.CANONICAL_SELF, PersonaTemporalScopeV1.STATIC,
        PersonaEvidenceKindV1.SELF_NAME, str(identity.canonical_name), ("SELF_NAME",))
    add(PersonaEvidenceSourceV1.CANONICAL_SELF, PersonaTemporalScopeV1.STATIC,
        PersonaEvidenceKindV1.AGENT_KIND, str(identity.agent_kind), ("AGENT_KIND",))
    for kind, value, template in (
        (PersonaEvidenceKindV1.ENERGY, identity.state.energia, "OPERATIONAL_ENERGY"),
        (PersonaEvidenceKindV1.STRESS, identity.state.estres, "OPERATIONAL_STRESS"),
        (PersonaEvidenceKindV1.FOCUS, identity.state.foco, "OPERATIONAL_FOCUS"),
    ):
        add(PersonaEvidenceSourceV1.OPERATIONAL_STATE, PersonaTemporalScopeV1.TURN,
            kind, float(value), (template,))

    if isinstance(current_user, str) and current_user.strip():
        add(PersonaEvidenceSourceV1.CURRENT_USER, PersonaTemporalScopeV1.TURN,
            PersonaEvidenceKindV1.USER_TEXT, str(current_user), ("USER_SAID",))
    if isinstance(memory_summary, str) and memory_summary.strip():
        add(PersonaEvidenceSourceV1.MEMORY_AGGREGATE, PersonaTemporalScopeV1.HISTORICAL,
            PersonaEvidenceKindV1.MEMORY_TEXT, str(memory_summary), ("MEMORY_RECORDS",))

    def structured_objects(data: dict[str, Any] | None) -> tuple[str, ...]:
        if not isinstance(data, dict):
            return ()
        raw = data.get("objetos_normalizados")
        if not isinstance(raw, list):
            raw = data.get("objetos_visibles")
        if not isinstance(raw, list):
            return ()
        names: list[str] = []
        for item in raw[:12]:
            if not isinstance(item, dict):
                continue
            name = item.get("display_name") or item.get("nombre") or item.get("name")
            if type(name) is str and name.strip():
                names.append(name.strip()[:120])
        return tuple(names)

    current_names = () if terminal_channel else structured_objects(current_event_details)
    for name in current_names:
        add(PersonaEvidenceSourceV1.CURRENT_PERCEPTION,
            PersonaTemporalScopeV1.CURRENT_EVENT,
            PersonaEvidenceKindV1.OBJECT_NAME, name, ("CURRENT_OBJECT",))
    if not current_names:
        for name in structured_objects(prior_perception_context):
            add(PersonaEvidenceSourceV1.HISTORICAL_PERCEPTION,
                PersonaTemporalScopeV1.UNKNOWN,
                PersonaEvidenceKindV1.OBJECT_NAME, name, ("PRIOR_OBJECT",))

    if oracle_result is not None:
        producer = oracle_result.provenance.producer
        cache = oracle_result.provenance.retrieval.cache
        if producer.kind is KnowledgeProducerKindV1.LEGACY_UNKNOWN:
            template = "ORACLE_LEGACY"
        elif producer.kind is KnowledgeProducerKindV1.LOCAL_DETERMINISTIC:
            template = "ORACLE_LOCAL"
        elif cache.lookup_status is CacheLookupStatusV1.HIT:
            template = "ORACLE_CACHED"
        elif producer.final_parse_status is StructuredParseStatus.DEGRADED:
            template = "ORACLE_DEGRADED"
        else:
            template = "ORACLE_GENERATED"
        for field_name in ("resumen_para_persona", "contenido_tecnico"):
            content = getattr(oracle_result, field_name)
            if isinstance(content, str) and content.strip():
                add(PersonaEvidenceSourceV1.ORACLE, PersonaTemporalScopeV1.HISTORICAL,
                    PersonaEvidenceKindV1.ORACLE_TEXT, str(content), (template,),
                    provenance_tag=template)

    if (
        isinstance(material_attestation, MaterialAttestationValidation)
        and material_attestation.status is MaterialAttestationStatus.VALID
        and material_attestation.receipt is not None
        and isinstance(material_correlation, PersonaMaterialCorrelationV1)
    ):
        receipt = material_attestation.receipt
        if (
            receipt.action_id == material_correlation.action_id
            and receipt.effect_kind.value == material_correlation.effect_kind
            and receipt.object_id == material_correlation.object_id
            and all((receipt.action_id, receipt.effect_kind.value, receipt.object_id))
        ):
            snapshot = PersonaMaterialSnapshotV1(
                action_id=str(receipt.action_id),
                effect_kind=receipt.effect_kind.value,
                object_id=str(receipt.object_id),
                status=receipt.status,
            )
            template = {
                MaterialEffectStatus.APPLIED: "MATERIAL_APPLIED",
                MaterialEffectStatus.FAILED: "MATERIAL_FAILED",
                MaterialEffectStatus.NO_EFFECT: "MATERIAL_NO_EFFECT",
                MaterialEffectStatus.UNSUPPORTED: "MATERIAL_UNSUPPORTED",
            }[receipt.status]
            add(PersonaEvidenceSourceV1.MATERIAL_OUTCOME,
                PersonaTemporalScopeV1.CURRENT_EVENT,
                PersonaEvidenceKindV1.MATERIAL_EFFECT, snapshot, (template,))

    frozen = tuple(snapshots)
    return (
        PersonaEvidenceBundleV1(1, tuple(item.ref for item in frozen)),
        PersonaEvidenceRegistryV1(1, frozen),
    )
