"""Replaceable, temporary ROOT-017 factual boundary for productive Persona turns.

The model selects closed acts. It never authors public factual prose. A future
CFC adapter can replace this facade without changing Orchestrator's evidence
policy, PromptPlan authority, or public DTOs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator

from ai_engine.identity_projection import IdentityContextV1
from ai_engine.persona_factual_evidence import (
    PersonaEvidenceBundleV1,
    PersonaEvidenceKindV1,
    PersonaEvidenceRegistryV1,
    PersonaEvidenceSnapshotV1,
    PersonaEvidenceSourceV1,
    PersonaMaterialCorrelationV1,
    PersonaMaterialSnapshotV1,
    PersonaTemporalScopeV1,
    compile_persona_evidence,
)
from ai_engine.persona_prompt_buider import PersonaPromptBuilder
from ai_engine.prompt_plan import PromptPlanV1
from ai_engine.schemas import OracleResult
from ai_engine.structured_parse_types import (
    StructuredParseIssueCode,
    StructuredParseResult,
    StructuredParseStatus,
    inspect_json_object,
)
from core.material_attestation import MaterialAttestationValidation, MaterialEffectStatus


class PersonaTemplateIdV1(str, Enum):
    SELF_NAME = "SELF_NAME"
    AGENT_KIND = "AGENT_KIND"
    OPERATIONAL_ENERGY = "OPERATIONAL_ENERGY"
    OPERATIONAL_STRESS = "OPERATIONAL_STRESS"
    OPERATIONAL_FOCUS = "OPERATIONAL_FOCUS"
    CURRENT_OBJECT = "CURRENT_OBJECT"
    PRIOR_OBJECT = "PRIOR_OBJECT"
    MATERIAL_APPLIED = "MATERIAL_APPLIED"
    MATERIAL_FAILED = "MATERIAL_FAILED"
    MATERIAL_NO_EFFECT = "MATERIAL_NO_EFFECT"
    MATERIAL_UNSUPPORTED = "MATERIAL_UNSUPPORTED"
    USER_SAID = "USER_SAID"
    MEMORY_RECORDS = "MEMORY_RECORDS"
    ORACLE_GENERATED = "ORACLE_GENERATED"
    ORACLE_CACHED = "ORACLE_CACHED"
    ORACLE_DEGRADED = "ORACLE_DEGRADED"
    ORACLE_LEGACY = "ORACLE_LEGACY"
    ORACLE_LOCAL = "ORACLE_LOCAL"
    SOCIAL_HELLO = "SOCIAL_HELLO"
    SOCIAL_ACKNOWLEDGE = "SOCIAL_ACKNOWLEDGE"
    SOCIAL_DISAGREE = "SOCIAL_DISAGREE"
    SOCIAL_HUMOR = "SOCIAL_HUMOR"
    CLARIFY_UNKNOWN = "CLARIFY_UNKNOWN"


class PersonaTextDisplayV1(str, Enum):
    EXACT_IF_FITS = "EXACT_IF_FITS"
    EXCERPT_240 = "EXCERPT_240"
    EXCERPT_480 = "EXCERPT_480"


class _ClosedActV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PersonaEvidenceClaimActV1(_ClosedActV1):
    kind: Literal["EVIDENCE_CLAIM"]
    evidence_id: str
    template_id: PersonaTemplateIdV1


class PersonaAttributedTextActV1(_ClosedActV1):
    kind: Literal["ATTRIBUTED_TEXT"]
    evidence_id: str
    template_id: PersonaTemplateIdV1
    display: PersonaTextDisplayV1 = PersonaTextDisplayV1.EXCERPT_240


class PersonaSocialActV1(_ClosedActV1):
    kind: Literal["SOCIAL"]
    template_id: PersonaTemplateIdV1


class PersonaClarificationActV1(_ClosedActV1):
    kind: Literal["CLARIFICATION"]
    template_id: PersonaTemplateIdV1


PersonaRenderActV1 = Annotated[
    PersonaEvidenceClaimActV1
    | PersonaAttributedTextActV1
    | PersonaSocialActV1
    | PersonaClarificationActV1,
    Field(discriminator="kind"),
]


class PersonaFactualPlanV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: StrictInt
    acts: tuple[PersonaRenderActV1, ...]

    @field_validator("version", mode="before")
    @classmethod
    def _exact_version(cls, value: object) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("Persona factual plan version must be exact integer 1")
        return value

    @field_validator("acts")
    @classmethod
    def _bounded_acts(cls, value: tuple[PersonaRenderActV1, ...]):
        if not 1 <= len(value) <= 8:
            raise ValueError("Persona factual plan requires 1..8 acts")
        return value

class PersonaFactualValidationStatusV1(str, Enum):
    ACCEPTED = "ACCEPTED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


class PersonaFactualReasonV1(str, Enum):
    MISSING_EVIDENCE = "MISSING_EVIDENCE"
    WRONG_ACT_FAMILY = "WRONG_ACT_FAMILY"
    INCOMPATIBLE_CAPABILITY = "INCOMPATIBLE_CAPABILITY"
    INCOMPATIBLE_SNAPSHOT = "INCOMPATIBLE_SNAPSHOT"
    INCOMPATIBLE_TEMPORAL_SCOPE = "INCOMPATIBLE_TEMPORAL_SCOPE"
    MATERIAL_CORRELATION = "MATERIAL_CORRELATION"
    STRUCTURAL_CONFLICT = "STRUCTURAL_CONFLICT"
    OUTPUT_BOUND = "OUTPUT_BOUND"
    PARSE_REJECTED = "PARSE_REJECTED"


@dataclass(frozen=True, slots=True)
class PersonaFactualValidationReceiptV1:
    version: int
    status: PersonaFactualValidationStatusV1
    accepted_count: int
    rejected_count: int
    reason_codes: tuple[PersonaFactualReasonV1, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != 1:
            raise ValueError("Persona validation receipt version must be 1")


@dataclass(frozen=True, slots=True)
class PersonaFactualTurnV1:
    prompt_plan: PromptPlanV1
    bundle: PersonaEvidenceBundleV1
    registry: PersonaEvidenceRegistryV1


@dataclass(frozen=True, slots=True)
class PersonaFactualRenderResultV1:
    text: str
    parse_status: StructuredParseStatus
    validation: PersonaFactualValidationReceiptV1
    retryable_syntax: bool


@runtime_checkable
class PersonaFactualBoundaryV1(Protocol):
    """Narrow adapter surface; a future implementation need not subclass Python's renderer."""

    def prepare(self, **kwargs: Any) -> PersonaFactualTurnV1: ...

    def finish(self, raw: object, turn: PersonaFactualTurnV1) -> PersonaFactualRenderResultV1: ...


_DIRECT_TEMPLATES = frozenset({
    PersonaTemplateIdV1.SELF_NAME, PersonaTemplateIdV1.AGENT_KIND,
    PersonaTemplateIdV1.OPERATIONAL_ENERGY, PersonaTemplateIdV1.OPERATIONAL_STRESS,
    PersonaTemplateIdV1.OPERATIONAL_FOCUS, PersonaTemplateIdV1.CURRENT_OBJECT,
    PersonaTemplateIdV1.PRIOR_OBJECT, PersonaTemplateIdV1.MATERIAL_APPLIED,
    PersonaTemplateIdV1.MATERIAL_FAILED, PersonaTemplateIdV1.MATERIAL_NO_EFFECT,
    PersonaTemplateIdV1.MATERIAL_UNSUPPORTED,
})
_ATTRIBUTION_TEMPLATES = frozenset({
    PersonaTemplateIdV1.USER_SAID, PersonaTemplateIdV1.MEMORY_RECORDS,
    PersonaTemplateIdV1.ORACLE_GENERATED, PersonaTemplateIdV1.ORACLE_CACHED,
    PersonaTemplateIdV1.ORACLE_DEGRADED, PersonaTemplateIdV1.ORACLE_LEGACY,
    PersonaTemplateIdV1.ORACLE_LOCAL,
})
_SOCIAL_TEMPLATES = frozenset({
    PersonaTemplateIdV1.SOCIAL_HELLO, PersonaTemplateIdV1.SOCIAL_ACKNOWLEDGE,
    PersonaTemplateIdV1.SOCIAL_DISAGREE, PersonaTemplateIdV1.SOCIAL_HUMOR,
})
_NO_FACT_TEXT = "No tengo evidencia suficiente para afirmar eso con seguridad."
_PARTIAL_NOTE = "Otras afirmaciones del plan no tenían respaldo."

# A capability printed in the DATA index is not, by itself, authority. The
# validator also checks this code-owned source/scope/kind contract.
_TEMPLATE_CONTRACT: dict[
    PersonaTemplateIdV1,
    tuple[PersonaEvidenceSourceV1, PersonaTemporalScopeV1, PersonaEvidenceKindV1],
] = {
    PersonaTemplateIdV1.SELF_NAME: (PersonaEvidenceSourceV1.CANONICAL_SELF, PersonaTemporalScopeV1.STATIC, PersonaEvidenceKindV1.SELF_NAME),
    PersonaTemplateIdV1.AGENT_KIND: (PersonaEvidenceSourceV1.CANONICAL_SELF, PersonaTemporalScopeV1.STATIC, PersonaEvidenceKindV1.AGENT_KIND),
    PersonaTemplateIdV1.OPERATIONAL_ENERGY: (PersonaEvidenceSourceV1.OPERATIONAL_STATE, PersonaTemporalScopeV1.TURN, PersonaEvidenceKindV1.ENERGY),
    PersonaTemplateIdV1.OPERATIONAL_STRESS: (PersonaEvidenceSourceV1.OPERATIONAL_STATE, PersonaTemporalScopeV1.TURN, PersonaEvidenceKindV1.STRESS),
    PersonaTemplateIdV1.OPERATIONAL_FOCUS: (PersonaEvidenceSourceV1.OPERATIONAL_STATE, PersonaTemporalScopeV1.TURN, PersonaEvidenceKindV1.FOCUS),
    PersonaTemplateIdV1.CURRENT_OBJECT: (PersonaEvidenceSourceV1.CURRENT_PERCEPTION, PersonaTemporalScopeV1.CURRENT_EVENT, PersonaEvidenceKindV1.OBJECT_NAME),
    PersonaTemplateIdV1.PRIOR_OBJECT: (PersonaEvidenceSourceV1.HISTORICAL_PERCEPTION, PersonaTemporalScopeV1.UNKNOWN, PersonaEvidenceKindV1.OBJECT_NAME),
    PersonaTemplateIdV1.MATERIAL_APPLIED: (PersonaEvidenceSourceV1.MATERIAL_OUTCOME, PersonaTemporalScopeV1.CURRENT_EVENT, PersonaEvidenceKindV1.MATERIAL_EFFECT),
    PersonaTemplateIdV1.MATERIAL_FAILED: (PersonaEvidenceSourceV1.MATERIAL_OUTCOME, PersonaTemporalScopeV1.CURRENT_EVENT, PersonaEvidenceKindV1.MATERIAL_EFFECT),
    PersonaTemplateIdV1.MATERIAL_NO_EFFECT: (PersonaEvidenceSourceV1.MATERIAL_OUTCOME, PersonaTemporalScopeV1.CURRENT_EVENT, PersonaEvidenceKindV1.MATERIAL_EFFECT),
    PersonaTemplateIdV1.MATERIAL_UNSUPPORTED: (PersonaEvidenceSourceV1.MATERIAL_OUTCOME, PersonaTemporalScopeV1.CURRENT_EVENT, PersonaEvidenceKindV1.MATERIAL_EFFECT),
    PersonaTemplateIdV1.USER_SAID: (PersonaEvidenceSourceV1.CURRENT_USER, PersonaTemporalScopeV1.TURN, PersonaEvidenceKindV1.USER_TEXT),
    PersonaTemplateIdV1.MEMORY_RECORDS: (PersonaEvidenceSourceV1.MEMORY_AGGREGATE, PersonaTemporalScopeV1.HISTORICAL, PersonaEvidenceKindV1.MEMORY_TEXT),
    **{
        template: (PersonaEvidenceSourceV1.ORACLE, PersonaTemporalScopeV1.HISTORICAL, PersonaEvidenceKindV1.ORACLE_TEXT)
        for template in _ATTRIBUTION_TEMPLATES
        if template not in {PersonaTemplateIdV1.USER_SAID, PersonaTemplateIdV1.MEMORY_RECORDS}
    },
}


class PersonaFactualBridgeV1:
    """Single replaceable seam: prepare one turn, then validate/render its plan."""

    def prepare(
        self,
        *,
        message: str | None,
        identity: IdentityContextV1,
        memory_summary: str,
        event_details: dict[str, Any] | None,
        prior_perception: dict[str, Any] | None,
        terminal_channel: bool,
        oracle_result: OracleResult | None = None,
        material_attestation: MaterialAttestationValidation | None = None,
        material_correlation: PersonaMaterialCorrelationV1 | None = None,
    ) -> PersonaFactualTurnV1:
        bundle, registry = compile_persona_evidence(
            identity=identity,
            current_user=message,
            memory_summary=memory_summary,
            current_event_details=event_details,
            prior_perception_context=prior_perception,
            oracle_result=oracle_result,
            material_attestation=material_attestation,
            material_correlation=material_correlation,
            terminal_channel=terminal_channel,
        )
        prompt = PersonaPromptBuilder.build_factual_plan(
            mensaje_usuario=message,
            identity_context=identity,
            percepcion=prior_perception,
            recuerdos=memory_summary,
            terminal_channel=terminal_channel,
            oracle_result=oracle_result,
            factual_index=bundle.public_index(),
        )
        return PersonaFactualTurnV1(prompt, bundle, registry)

    @staticmethod
    def parse(raw: object) -> StructuredParseResult[PersonaFactualPlanV1]:
        inspection = inspect_json_object(raw)
        if inspection.data is not None:
            try:
                plan = PersonaFactualPlanV1.model_validate(inspection.data)
            except ValidationError:
                issue = StructuredParseIssueCode.SCHEMA_VALIDATION_FAILED
            else:
                if inspection.issue_code is None:
                    return StructuredParseResult(
                        1, StructuredParseStatus.VALID, plan, None, (),
                        inspection.normalizations, inspection.raw_shape,
                        inspection.raw_length,
                    )
                issue = inspection.issue_code
        else:
            issue = inspection.issue_code or StructuredParseIssueCode.MALFORMED_JSON
        return StructuredParseResult(
            1, StructuredParseStatus.ERROR, None, None, (issue,),
            inspection.normalizations, inspection.raw_shape, inspection.raw_length,
        )

    def finish(self, raw: object, turn: PersonaFactualTurnV1) -> PersonaFactualRenderResultV1:
        if not isinstance(turn, PersonaFactualTurnV1):
            raise TypeError("Persona factual turn must be typed")
        parsed = self.parse(raw)
        if parsed.value is None:
            receipt = PersonaFactualValidationReceiptV1(
                1, PersonaFactualValidationStatusV1.REJECTED, 0, 0,
                (PersonaFactualReasonV1.PARSE_REJECTED,),
            )
            return PersonaFactualRenderResultV1(
                _NO_FACT_TEXT, parsed.status, receipt, True
            )
        valid, receipt = self.validate(parsed.value, turn.registry)
        rendered = self.render(valid, turn.registry)
        exceeded_bound = (
            rendered == _NO_FACT_TEXT
            and valid
            and not all(isinstance(act, PersonaClarificationActV1) for act in valid)
        )
        if receipt.status is PersonaFactualValidationStatusV1.PARTIAL:
            rendered += " " + _PARTIAL_NOTE
        if len(rendered) > 700 or exceeded_bound:
            receipt = PersonaFactualValidationReceiptV1(
                1, PersonaFactualValidationStatusV1.REJECTED, 0, len(parsed.value.acts),
                (PersonaFactualReasonV1.OUTPUT_BOUND,),
            )
            rendered = _NO_FACT_TEXT
        return PersonaFactualRenderResultV1(
            rendered,
            parsed.status, receipt, False,
        )

    @staticmethod
    def validate(
        plan: PersonaFactualPlanV1,
        registry: PersonaEvidenceRegistryV1,
    ) -> tuple[tuple[PersonaRenderActV1, ...], PersonaFactualValidationReceiptV1]:
        if not isinstance(plan, PersonaFactualPlanV1) or not isinstance(registry, PersonaEvidenceRegistryV1):
            raise TypeError("Factual validation requires typed plan and registry")
        accepted: list[PersonaRenderActV1] = []
        reasons: list[PersonaFactualReasonV1] = []
        for act in plan.acts:
            reason = PersonaFactualBridgeV1._validate_act(act, registry)
            if reason is None:
                accepted.append(act)
            else:
                reasons.append(reason)
        # Only structurally comparable typed atoms are checked. Distinct
        # perceived objects and unstructured prose are intentionally excluded.
        keys: dict[tuple[object, ...], object] = {}
        conflicted: set[tuple[object, ...]] = set()
        for act in accepted:
            if not isinstance(act, PersonaEvidenceClaimActV1):
                continue
            snapshot = registry.get(act.evidence_id)
            assert snapshot is not None
            ref, value = snapshot.ref, snapshot.value
            if ref.source in {PersonaEvidenceSourceV1.CANONICAL_SELF, PersonaEvidenceSourceV1.OPERATIONAL_STATE}:
                key = (ref.source, ref.scope, ref.kind)
            elif isinstance(value, PersonaMaterialSnapshotV1):
                key = (ref.source, value.action_id, value.effect_kind, value.object_id)
            else:
                continue
            if key in keys and keys[key] != value:
                conflicted.add(key)
            keys[key] = value
        if conflicted:
            kept: list[PersonaRenderActV1] = []
            for act in accepted:
                if isinstance(act, PersonaEvidenceClaimActV1):
                    snapshot = registry.get(act.evidence_id)
                    assert snapshot is not None
                    ref, value = snapshot.ref, snapshot.value
                    key = (
                        (ref.source, value.action_id, value.effect_kind, value.object_id)
                        if isinstance(value, PersonaMaterialSnapshotV1)
                        else (ref.source, ref.scope, ref.kind)
                    )
                    if key in conflicted:
                        reasons.append(PersonaFactualReasonV1.STRUCTURAL_CONFLICT)
                        continue
                kept.append(act)
            accepted = kept
        status = (
            PersonaFactualValidationStatusV1.ACCEPTED if not reasons else
            PersonaFactualValidationStatusV1.PARTIAL if accepted else
            PersonaFactualValidationStatusV1.REJECTED
        )
        receipt = PersonaFactualValidationReceiptV1(
            1, status, len(accepted), len(reasons), tuple(dict.fromkeys(reasons))
        )
        return tuple(accepted), receipt

    @staticmethod
    def _validate_act(
        act: PersonaRenderActV1,
        registry: PersonaEvidenceRegistryV1,
    ) -> PersonaFactualReasonV1 | None:
        if isinstance(act, PersonaSocialActV1):
            return None if act.template_id in _SOCIAL_TEMPLATES else PersonaFactualReasonV1.WRONG_ACT_FAMILY
        if isinstance(act, PersonaClarificationActV1):
            return None if act.template_id is PersonaTemplateIdV1.CLARIFY_UNKNOWN else PersonaFactualReasonV1.WRONG_ACT_FAMILY
        snapshot = registry.get(act.evidence_id)
        if snapshot is None:
            return PersonaFactualReasonV1.MISSING_EVIDENCE
        ref = snapshot.ref
        if act.template_id.value not in ref.allowed_templates:
            return PersonaFactualReasonV1.INCOMPATIBLE_CAPABILITY
        expected_contract = _TEMPLATE_CONTRACT.get(act.template_id)
        if expected_contract is None:
            return PersonaFactualReasonV1.WRONG_ACT_FAMILY
        if ref.source is not expected_contract[0] or ref.kind is not expected_contract[2]:
            return PersonaFactualReasonV1.INCOMPATIBLE_SNAPSHOT
        if ref.scope is not expected_contract[1]:
            return PersonaFactualReasonV1.INCOMPATIBLE_TEMPORAL_SCOPE
        if isinstance(act, PersonaAttributedTextActV1):
            if act.template_id not in _ATTRIBUTION_TEMPLATES:
                return PersonaFactualReasonV1.WRONG_ACT_FAMILY
            if ref.source not in {
                PersonaEvidenceSourceV1.CURRENT_USER,
                PersonaEvidenceSourceV1.MEMORY_AGGREGATE,
                PersonaEvidenceSourceV1.ORACLE,
            } or type(snapshot.value) is not str:
                return PersonaFactualReasonV1.INCOMPATIBLE_SNAPSHOT
            if act.display is PersonaTextDisplayV1.EXACT_IF_FITS and len(snapshot.value) > 480:
                return PersonaFactualReasonV1.OUTPUT_BOUND
            return None
        if not isinstance(act, PersonaEvidenceClaimActV1) or act.template_id not in _DIRECT_TEMPLATES:
            return PersonaFactualReasonV1.WRONG_ACT_FAMILY
        if ref.source in {
            PersonaEvidenceSourceV1.CURRENT_USER,
            PersonaEvidenceSourceV1.MEMORY_AGGREGATE,
            PersonaEvidenceSourceV1.ORACLE,
        }:
            return PersonaFactualReasonV1.INCOMPATIBLE_CAPABILITY
        if act.template_id is PersonaTemplateIdV1.CURRENT_OBJECT and ref.scope is not PersonaTemporalScopeV1.CURRENT_EVENT:
            return PersonaFactualReasonV1.INCOMPATIBLE_TEMPORAL_SCOPE
        if act.template_id is PersonaTemplateIdV1.PRIOR_OBJECT and ref.scope is PersonaTemporalScopeV1.CURRENT_EVENT:
            return PersonaFactualReasonV1.INCOMPATIBLE_TEMPORAL_SCOPE
        if ref.kind is PersonaEvidenceKindV1.MATERIAL_EFFECT:
            value = snapshot.value
            if not isinstance(value, PersonaMaterialSnapshotV1):
                return PersonaFactualReasonV1.INCOMPATIBLE_SNAPSHOT
            expected = {
                MaterialEffectStatus.APPLIED: PersonaTemplateIdV1.MATERIAL_APPLIED,
                MaterialEffectStatus.FAILED: PersonaTemplateIdV1.MATERIAL_FAILED,
                MaterialEffectStatus.NO_EFFECT: PersonaTemplateIdV1.MATERIAL_NO_EFFECT,
                MaterialEffectStatus.UNSUPPORTED: PersonaTemplateIdV1.MATERIAL_UNSUPPORTED,
            }[value.status]
            if act.template_id is not expected or not all((value.action_id, value.effect_kind, value.object_id)):
                return PersonaFactualReasonV1.MATERIAL_CORRELATION
        elif ref.kind in {
            PersonaEvidenceKindV1.ENERGY,
            PersonaEvidenceKindV1.STRESS,
            PersonaEvidenceKindV1.FOCUS,
        }:
            if type(snapshot.value) is not float:
                return PersonaFactualReasonV1.INCOMPATIBLE_SNAPSHOT
        elif type(snapshot.value) is not str:
            return PersonaFactualReasonV1.INCOMPATIBLE_SNAPSHOT
        return None

    @staticmethod
    def render(
        acts: tuple[PersonaRenderActV1, ...],
        registry: PersonaEvidenceRegistryV1,
    ) -> str:
        """Only validated acts and frozen snapshots reach this deterministic renderer."""
        if not isinstance(registry, PersonaEvidenceRegistryV1):
            raise TypeError("Renderer requires immutable Persona registry")
        parts: list[str] = []
        for act in acts:
            if PersonaFactualBridgeV1._validate_act(act, registry) is not None:
                raise ValueError("Renderer received an unvalidated act")
            snapshot = registry.get(act.evidence_id) if hasattr(act, "evidence_id") else None
            if isinstance(act, PersonaAttributedTextActV1):
                assert snapshot is not None and isinstance(snapshot.value, str)
                quoted = PersonaFactualBridgeV1._display_text(snapshot.value, act.display)
                prefix = {
                    PersonaTemplateIdV1.USER_SAID: "Me dijiste: ",
                    PersonaTemplateIdV1.MEMORY_RECORDS: "Mi memoria agregada registra: ",
                    PersonaTemplateIdV1.ORACLE_GENERATED: "El resultado técnico generado indica: ",
                    PersonaTemplateIdV1.ORACLE_CACHED: "Un resultado técnico previo, recuperado para una consulta relacionada, indica: ",
                    PersonaTemplateIdV1.ORACLE_DEGRADED: "El resultado técnico recuperado parcialmente indica: ",
                    PersonaTemplateIdV1.ORACLE_LEGACY: "Un registro técnico previo de origen desconocido indica: ",
                    PersonaTemplateIdV1.ORACLE_LOCAL: "El fallback local informa: ",
                }[act.template_id]
                parts.append(prefix + quoted)
            elif isinstance(act, PersonaEvidenceClaimActV1):
                assert snapshot is not None
                value = snapshot.value
                template = act.template_id
                if isinstance(value, PersonaMaterialSnapshotV1):
                    target = json.dumps(value.object_id, ensure_ascii=False)
                    effect = json.dumps(value.effect_kind, ensure_ascii=False)
                    action = json.dumps(value.action_id, ensure_ascii=False)
                    wording = {
                        PersonaTemplateIdV1.MATERIAL_APPLIED: "Se aplicó el efecto {e} sobre {t} en la acción {a}.",
                        PersonaTemplateIdV1.MATERIAL_FAILED: "Falló el efecto {e} sobre {t} en la acción {a}.",
                        PersonaTemplateIdV1.MATERIAL_NO_EFFECT: "No se observó efecto {e} sobre {t} en la acción {a}.",
                        PersonaTemplateIdV1.MATERIAL_UNSUPPORTED: "El efecto {e} sobre {t} no fue compatible en la acción {a}.",
                    }[template]
                    parts.append(wording.format(e=effect, t=target, a=action))
                else:
                    safe = json.dumps(value, ensure_ascii=False) if isinstance(value, str) else f"{value:.2f}"
                    wording = {
                        PersonaTemplateIdV1.SELF_NAME: "Mi nombre configurado es {v}.",
                        PersonaTemplateIdV1.AGENT_KIND: "Mi tipo de agente configurado es {v}.",
                        PersonaTemplateIdV1.OPERATIONAL_ENERGY: "Mi energía operacional es {v}.",
                        PersonaTemplateIdV1.OPERATIONAL_STRESS: "Mi estrés operacional es {v}.",
                        PersonaTemplateIdV1.OPERATIONAL_FOCUS: "Mi foco operacional es {v}.",
                        PersonaTemplateIdV1.CURRENT_OBJECT: "En la percepción de este turno aparece {v}.",
                        PersonaTemplateIdV1.PRIOR_OBJECT: "Un contexto perceptivo previo registró {v}; no puedo confirmar que siga ahí.",
                    }[template]
                    parts.append(wording.format(v=safe))
            elif isinstance(act, PersonaSocialActV1):
                parts.append({
                    PersonaTemplateIdV1.SOCIAL_HELLO: "Hola.",
                    PersonaTemplateIdV1.SOCIAL_ACKNOWLEDGE: "Te sigo.",
                    PersonaTemplateIdV1.SOCIAL_DISAGREE: "No estoy de acuerdo sin más evidencia.",
                    PersonaTemplateIdV1.SOCIAL_HUMOR: "Hasta mis dudas piden recibo.",
                }[act.template_id])
            else:
                parts.append(_NO_FACT_TEXT)
        if not parts:
            return _NO_FACT_TEXT
        output = " ".join(parts)
        return output if len(output) <= 700 else _NO_FACT_TEXT

    @staticmethod
    def _display_text(value: str, operation: PersonaTextDisplayV1) -> str:
        if operation is PersonaTextDisplayV1.EXACT_IF_FITS:
            if len(value) > 480:
                raise ValueError("Exact attributed text exceeds display bound")
            shown, truncated = value, False
        else:
            limit = 240 if operation is PersonaTextDisplayV1.EXCERPT_240 else 480
            shown, truncated = value[:limit], len(value) > limit
        escaped = json.dumps(shown, ensure_ascii=False)
        return escaped + (" [fragmento]" if truncated else "")
