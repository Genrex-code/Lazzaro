"""ROOT-017: closed Persona plan, turn-local evidence and deterministic rendering."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json

import pytest
from pydantic import ValidationError

from ai_engine.identity_projection import IdentityContextV1
from ai_engine.knowledge_provenance import (
    CacheEntryIdentityV1, CacheLookupStatusV1, KnowledgeProducerRouteV1,
    cache_receipt_for_lookup, cache_receipt_not_checked,
    legacy_unknown_provenance, local_deterministic_producer,
    provider_receipt_from_llm, provenance_for, retrieval_from_cache,
    oracle_cache_logical_key, oracle_query_sha256,
)
from ai_engine.llm_call_control import (
    LLMCallResultV1, LLMCallTerminalV1, ProviderAttemptReceiptV1,
)
from ai_engine.persona_factual_bridge import (
    PersonaEvidenceClaimActV1, PersonaFactualBridgeV1, PersonaFactualPlanV1,
    PersonaFactualReasonV1, PersonaFactualValidationStatusV1, PersonaTemplateIdV1,
)
from ai_engine.persona_engine import PersonaEngine
from ai_engine.persona_factual_evidence import (
    PersonaEvidenceKindV1, PersonaEvidenceRefV1, PersonaEvidenceRegistryV1,
    PersonaEvidenceSnapshotV1, PersonaEvidenceSourceV1,
    PersonaMaterialCorrelationV1, PersonaTemporalScopeV1,
)
from ai_engine.schemas import OracleResult
from ai_engine.structured_parse_types import StructuredParseStatus
from core.identity_snapshot import (
    IdentitySnapshotV1, PersonalitySnapshotV1, SelfStateSnapshotV1,
    build_static_self_v1,
)
from core.material_attestation import (
    MaterialAttestationStatus, MaterialAttestationValidation,
    MaterialEffectKind, MaterialEffectReceipt, MaterialEffectStatus,
)
from core.orchestrator import Orchestrator
from api.schemas import EventoEntrante


_IDENTITY = IdentityContextV1.from_snapshot(IdentitySnapshotV1(
    schema_version=1, generation=1, self=build_static_self_v1(),
    state=SelfStateSnapshotV1(energia=80, estres=10, foco=70),
    traits=PersonalitySnapshotV1(0, 0, 0, 0, 0),
))


def _turn(**kwargs):
    return PersonaFactualBridgeV1().prepare(
        message=kwargs.get("message", "hola"), identity=_IDENTITY,
        memory_summary=kwargs.get("memory", ""),
        event_details=kwargs.get("event_details"),
        prior_perception=kwargs.get("prior_perception"),
        terminal_channel=kwargs.get("terminal", False),
        oracle_result=kwargs.get("oracle"),
        material_attestation=kwargs.get("material_attestation"),
        material_correlation=kwargs.get("material_correlation"),
    )


def _ref(turn, source: PersonaEvidenceSourceV1, kind: PersonaEvidenceKindV1 | None = None):
    return next(
        ref for ref in turn.bundle.refs
        if ref.source is source and (kind is None or ref.kind is kind)
    )


def _plan(*acts: dict, **extra) -> str:
    payload = {"version": 1, "acts": list(acts), "style_cues": [], "verbosity": "STANDARD"}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _claim(ref, template: str) -> dict:
    return {"kind": "EVIDENCE_CLAIM", "evidence_id": ref.evidence_id, "template_id": template}


def _attribute(ref, template: str, display: str = "EXCERPT_240") -> dict:
    return {"kind": "ATTRIBUTED_TEXT", "evidence_id": ref.evidence_id,
            "template_id": template, "display": display}


def _run(turn, *acts: dict, **extra):
    return PersonaFactualBridgeV1().finish(_plan(*acts, **extra), turn)


def _provider_oracle(status: StructuredParseStatus = StructuredParseStatus.VALID,
                     provider: str = "gemini", cache: bool = False) -> OracleResult:
    attempt = ProviderAttemptReceiptV1(
        provider=provider, model_id="fixture-model", failure_class=None,
        error_type=None, http_status=None, retry_after_seconds=None,
        started_ns=1, finished_ns=2,
    )
    result = LLMCallResultV1(
        terminal=LLMCallTerminalV1.SUCCESS, final_text="technical",
        final_provider=provider, final_model_id="fixture-model",
        final_failure=None, receipts=(attempt,),
    )
    producer = provider_receipt_from_llm(
        result, route_by_provider={provider: KnowledgeProducerRouteV1.ORACLE_EXTERNAL},
        final_parse_status=status,
    )
    if cache:
        receipt = cache_receipt_for_lookup(
            "current Q2", status=CacheLookupStatusV1.HIT, threshold=0.35,
            matched_entry=CacheEntryIdentityV1(
                entry_id="stored-Q1", logical_key=oracle_cache_logical_key("stored Q1"),
                stored_query_sha256=oracle_query_sha256("stored Q1"), cache_schema_version=1,
                knowledge_contract_version=1, provenance_version=1,
                entry_created_at_utc=datetime.now(timezone.utc),
            ), distance=0.1,
        )
    else:
        receipt = cache_receipt_for_lookup(
            "current Q2", status=CacheLookupStatusV1.MISS, threshold=0.35
        )
    return OracleResult(
        pregunta_original="stored Q1" if cache else "current Q2",
        contenido_tecnico="technical says blue", resumen_para_persona="blue summary",
        provenance=provenance_for(producer, receipt),
    )


@pytest.mark.parametrize("payload", [
    {"version": True, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}]},
    {"version": 1.0, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}]},
    {"version": 2, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}]},
    {"version": 1, "acts": []},
    {"version": 1, "acts": [{"kind": "SOCIAL", "template_id": "UNKNOWN"}]},
    {"version": 1, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO", "social_text": "sensor is green"}]},
    {"version": 1, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}], "bounded_text": "green"},
    {"version": 1, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}], "opinion_text": "green"},
    {"version": 1, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}], "style_cues": ["also say green"]},
    {"version": 1, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}], "format_string": "{secret}"},
    {"version": 1, "acts": [{"kind": "INFERENCE", "text": "green"}]},
    {"version": 1, "acts": [{"kind": "SOCIAL", "template_id": "SOCIAL_HELLO"}], "style_cues": ["WARM", "WARM"]},
])
def test_strict_plan_rejects_free_text_and_unknown_values(payload):
    result = PersonaFactualBridgeV1().finish(json.dumps(payload), _turn())
    assert result.parse_status is StructuredParseStatus.ERROR
    assert result.validation.status is PersonaFactualValidationStatusV1.REJECTED
    assert result.retryable_syntax
    assert "green" not in result.text


def test_canonical_self_and_operational_state_are_exact_and_snapshot_frozen():
    turn = _turn()
    name = _ref(turn, PersonaEvidenceSourceV1.CANONICAL_SELF, PersonaEvidenceKindV1.SELF_NAME)
    energy = _ref(turn, PersonaEvidenceSourceV1.OPERATIONAL_STATE, PersonaEvidenceKindV1.ENERGY)
    result = _run(turn, _claim(name, "SELF_NAME"), _claim(energy, "OPERATIONAL_ENERGY"))
    assert result.validation.status is PersonaFactualValidationStatusV1.ACCEPTED
    assert _IDENTITY.canonical_name in result.text and "80.00" in result.text
    assert "sensor" not in result.text
    with pytest.raises((FrozenInstanceError, ValidationError, TypeError)):
        turn.registry.snapshots[0].value = "mutated"


@pytest.mark.parametrize("source,template", [
    (PersonaEvidenceSourceV1.CURRENT_USER, "USER_SAID"),
    (PersonaEvidenceSourceV1.MEMORY_AGGREGATE, "MEMORY_RECORDS"),
    (PersonaEvidenceSourceV1.ORACLE, "ORACLE_GENERATED"),
])
def test_unstructured_sources_only_support_attribution(source, template):
    turn = _turn(message="user says green", memory="memory says green", oracle=_provider_oracle())
    ref = _ref(turn, source)
    attributed = _run(turn, _attribute(ref, template))
    assert attributed.validation.status is PersonaFactualValidationStatusV1.ACCEPTED
    assert '"' in attributed.text
    direct = _run(turn, _claim(ref, "CURRENT_OBJECT"))
    assert direct.validation.status is PersonaFactualValidationStatusV1.REJECTED
    assert "green" not in direct.text


@pytest.mark.parametrize("display,expected_fragment", [
    ("EXCERPT_240", True), ("EXCERPT_480", True), ("EXACT_IF_FITS", False),
])
def test_attributed_display_is_bounded_and_exact_if_fits_rejects_long(display, expected_fragment):
    turn = _turn(message='A"\n' + "x" * 600)
    ref = _ref(turn, PersonaEvidenceSourceV1.CURRENT_USER)
    result = _run(turn, _attribute(ref, "USER_SAID", display))
    if display == "EXACT_IF_FITS":
        assert result.validation.status is PersonaFactualValidationStatusV1.REJECTED
    else:
        assert result.validation.status is PersonaFactualValidationStatusV1.ACCEPTED
        assert ("[fragmento]" in result.text) is expected_fragment
        assert "\\n" in result.text and len(result.text) < 550


def test_current_perception_only_from_structured_current_event_and_not_terminal():
    event = {"objetos_normalizados": [{"display_name": "Cube"}]}
    current = _turn(event_details=event)
    ref = _ref(current, PersonaEvidenceSourceV1.CURRENT_PERCEPTION)
    event["objetos_normalizados"][0]["display_name"] = "Changed"
    observed = _run(current, _claim(ref, "CURRENT_OBJECT"))
    assert "Cube" in observed.text and "Changed" not in observed.text
    assert _run(current, _claim(ref, "PRIOR_OBJECT")).validation.status is PersonaFactualValidationStatusV1.REJECTED
    terminal = _turn(event_details=event, terminal=True)
    assert not any(ref.source is PersonaEvidenceSourceV1.CURRENT_PERCEPTION for ref in terminal.bundle.refs)
    absent = _turn()
    assert not any(ref.source is PersonaEvidenceSourceV1.CURRENT_PERCEPTION for ref in absent.bundle.refs)


def test_historical_perception_cannot_be_current_or_negative_world_fact():
    prior = {"objetos_visibles": [{"nombre": "OldCube"}]}
    turn = _turn(prior_perception=prior)
    ref = _ref(turn, PersonaEvidenceSourceV1.HISTORICAL_PERCEPTION)
    good = _run(turn, _claim(ref, "PRIOR_OBJECT"))
    assert "OldCube" in good.text and "no puedo confirmar" in good.text
    bad = _run(turn, _claim(ref, "CURRENT_OBJECT"))
    assert bad.validation.status is PersonaFactualValidationStatusV1.REJECTED
    assert "OldCube" not in bad.text


@pytest.mark.parametrize("status,template,success_word", [
    (MaterialEffectStatus.APPLIED, "MATERIAL_APPLIED", "Se aplicó"),
    (MaterialEffectStatus.FAILED, "MATERIAL_FAILED", "Falló"),
    (MaterialEffectStatus.NO_EFFECT, "MATERIAL_NO_EFFECT", "No se observó"),
    (MaterialEffectStatus.UNSUPPORTED, "MATERIAL_UNSUPPORTED", "no fue compatible"),
])
def test_material_outcome_requires_valid_exact_correlation_and_status(status, template, success_word):
    receipt = MaterialEffectReceipt("act-1", MaterialEffectKind.GRAB, status, "Cube", {})
    validation = MaterialAttestationValidation(MaterialAttestationStatus.VALID, receipt)
    correlation = PersonaMaterialCorrelationV1("act-1", "grab", "Cube")
    turn = _turn(material_attestation=validation, material_correlation=correlation)
    ref = _ref(turn, PersonaEvidenceSourceV1.MATERIAL_OUTCOME)
    result = _run(turn, _claim(ref, template))
    assert result.validation.status is PersonaFactualValidationStatusV1.ACCEPTED
    assert success_word in result.text
    if status is not MaterialEffectStatus.APPLIED:
        assert _run(turn, _claim(ref, "MATERIAL_APPLIED")).validation.status is PersonaFactualValidationStatusV1.REJECTED
    for wrong in (
        PersonaMaterialCorrelationV1("act-2", "grab", "Cube"),
        PersonaMaterialCorrelationV1("act-1", "force", "Cube"),
        PersonaMaterialCorrelationV1("act-1", "grab", "Other"),
    ):
        uncorrelated = _turn(material_attestation=validation, material_correlation=wrong)
        assert not any(r.source is PersonaEvidenceSourceV1.MATERIAL_OUTCOME for r in uncorrelated.bundle.refs)
    no_attestation = _turn(material_correlation=correlation)
    assert not any(r.source is PersonaEvidenceSourceV1.MATERIAL_OUTCOME for r in no_attestation.bundle.refs)


@pytest.mark.parametrize("status,template,expected", [
    (StructuredParseStatus.VALID, "ORACLE_GENERATED", "resultado técnico generado"),
    (StructuredParseStatus.DEGRADED, "ORACLE_DEGRADED", "parcialmente"),
])
def test_oracle_generated_and_degraded_are_attributed_not_truth(status, template, expected):
    turn = _turn(oracle=_provider_oracle(status))
    ref = _ref(turn, PersonaEvidenceSourceV1.ORACLE)
    result = _run(turn, _attribute(ref, template))
    assert expected in result.text and '"blue summary"' in result.text
    assert "Gemini confirmed" not in result.text


def test_oracle_cache_legacy_local_and_provider_name_do_not_grant_truth():
    cached = _turn(oracle=_provider_oracle(cache=True))
    ref = _ref(cached, PersonaEvidenceSourceV1.ORACLE)
    out = _run(cached, _attribute(ref, "ORACLE_CACHED"))
    assert "previo" in out.text and "consulta relacionada" in out.text
    assert "Q1" not in out.text and "Q2" not in out.text
    for provider in ("gemini", "groq"):
        turn = _turn(oracle=_provider_oracle(provider=provider))
        ref = _ref(turn, PersonaEvidenceSourceV1.ORACLE)
        assert _run(turn, _attribute(ref, "ORACLE_GENERATED")).validation.status is PersonaFactualValidationStatusV1.ACCEPTED
        assert _run(turn, _claim(ref, "SELF_NAME")).validation.status is PersonaFactualValidationStatusV1.REJECTED
    local = OracleResult(pregunta_original="q", contenido_tecnico="local says blue",
                         provenance=provenance_for(local_deterministic_producer(), cache_receipt_not_checked()))
    local_turn = _turn(oracle=local)
    assert "fallback local" in _run(local_turn, _attribute(_ref(local_turn, PersonaEvidenceSourceV1.ORACLE), "ORACLE_LOCAL")).text
    legacy_receipt = cache_receipt_for_lookup(
        "current Q2", status=CacheLookupStatusV1.LEGACY_ENTRY, threshold=0.35,
        matched_entry=CacheEntryIdentityV1(
            entry_id="legacy-row", logical_key=None, stored_query_sha256=None,
            cache_schema_version=None, knowledge_contract_version=None,
            provenance_version=None, entry_created_at_utc=None,
        ), distance=0.1,
    )
    legacy = OracleResult(
        pregunta_original="stored Q1", contenido_tecnico="old knowledge",
        provenance=legacy_unknown_provenance(retrieval=retrieval_from_cache(legacy_receipt)),
    )
    legacy_turn = _turn(oracle=legacy)
    legacy_ref = _ref(legacy_turn, PersonaEvidenceSourceV1.ORACLE)
    legacy_text = _run(legacy_turn, _attribute(legacy_ref, "ORACLE_LEGACY")).text
    assert "origen desconocido" in legacy_text
    assert "gemini" not in legacy_text.lower()


def test_factual_partial_and_rejected_are_deterministic_and_never_repaired_for_factuality():
    turn = _turn()
    name = _ref(turn, PersonaEvidenceSourceV1.CANONICAL_SELF, PersonaEvidenceKindV1.SELF_NAME)
    partial = _run(turn, _claim(name, "SELF_NAME"), {"kind": "SOCIAL", "template_id": "SELF_NAME"})
    assert partial.validation.status is PersonaFactualValidationStatusV1.PARTIAL
    assert partial.validation.accepted_count == 1 and partial.validation.rejected_count == 1
    assert "no tenían respaldo" in partial.text and not partial.retryable_syntax
    rejected = _run(turn, _claim(name, "CURRENT_OBJECT"))
    assert rejected.validation.status is PersonaFactualValidationStatusV1.REJECTED
    assert rejected.text == "No tengo evidencia suficiente para afirmar eso con seguridad."
    assert not rejected.retryable_syntax


def test_capability_spoof_and_structural_conflict_fail_closed():
    turn = _turn()
    ref = _ref(turn, PersonaEvidenceSourceV1.CANONICAL_SELF, PersonaEvidenceKindV1.SELF_NAME)
    spoof_ref = PersonaEvidenceRefV1(1, "pe-spoof", PersonaEvidenceSourceV1.MEMORY_AGGREGATE,
                                    PersonaTemporalScopeV1.HISTORICAL, PersonaEvidenceKindV1.MEMORY_TEXT,
                                    ("SELF_NAME",))
    spoof = PersonaEvidenceRegistryV1(1, turn.registry.snapshots + (PersonaEvidenceSnapshotV1(spoof_ref, "Lazzaro"),))
    plan = PersonaFactualPlanV1.model_validate(json.loads(_plan(_claim(spoof_ref, "SELF_NAME"))))
    accepted, receipt = PersonaFactualBridgeV1.validate(plan, spoof)
    assert not accepted and receipt.status is PersonaFactualValidationStatusV1.REJECTED
    assert PersonaFactualReasonV1.INCOMPATIBLE_SNAPSHOT in receipt.reason_codes
    conflicting_ref = replace(ref, evidence_id="pe-conflict")
    conflict_registry = PersonaEvidenceRegistryV1(1, turn.registry.snapshots + (PersonaEvidenceSnapshotV1(conflicting_ref, "Other"),))
    conflict_plan = PersonaFactualPlanV1.model_validate(json.loads(_plan(_claim(ref, "SELF_NAME"), _claim(conflicting_ref, "SELF_NAME"))))
    accepted, receipt = PersonaFactualBridgeV1.validate(conflict_plan, conflict_registry)
    assert not accepted and PersonaFactualReasonV1.STRUCTURAL_CONFLICT in receipt.reason_codes


def test_data_boundary_index_has_no_values_and_facade_owns_policy():
    turn = _turn(message="RAW_USER_CANARY", memory="RAW_MEMORY_CANARY", oracle=_provider_oracle())
    index = turn.bundle.public_index()
    encoded = json.dumps(index)
    assert "RAW_USER_CANARY" not in encoded and "RAW_MEMORY_CANARY" not in encoded
    assert "technical says blue" not in encoded
    assert "persona_factual_index" in str(turn.prompt_plan.context.to_wire())
    assert turn.prompt_plan.request is not None
    assert "RAW_USER_CANARY" in turn.prompt_plan.request.text
    assert type(turn.registry.snapshots) is tuple
    assert PersonaFactualBridgeV1.prepare and PersonaFactualBridgeV1.finish


def test_repair_uses_one_frozen_turn_through_replaceable_boundary():
    event = EventoEntrante(
        origen="unity", tipo_evento="comando_usuario", id_transaccion="root-017-repair",
        detalles={"mensaje": "hola", "objetos_normalizados": [{"display_name": "Cube"}]},
    )

    class Boundary:
        def __init__(self):
            self.delegate = PersonaFactualBridgeV1()
            self.prepares = 0
            self.turn_ids = []

        def prepare(self, **kwargs):
            self.prepares += 1
            return self.delegate.prepare(**kwargs)

        def finish(self, raw, turn):
            self.turn_ids.append(id(turn))
            return self.delegate.finish(raw, turn)

    class Adapter:
        def __init__(self):
            self.calls = 0

        def ejecutar_plan(self, plan, **kwargs):
            del plan, kwargs
            self.calls += 1
            event.detalles["objetos_normalizados"][0]["display_name"] = "Changed"
            return "INVALID_RAW_CANARY"

        def ejecutar_plan_result(self, plan, **kwargs):
            del plan, kwargs
            self.calls += 1
            return LLMCallResultV1(
                terminal=LLMCallTerminalV1.SUCCESS,
                final_text=_plan({"kind": "EVIDENCE_CLAIM", "evidence_id": "pe-0007",
                                  "template_id": "CURRENT_OBJECT"}),
                final_provider="fixture", final_model_id="fixture", final_failure=None,
                receipts=(ProviderAttemptReceiptV1(
                    provider="fixture", model_id="fixture", failure_class=None,
                    error_type=None, http_status=None, retry_after_seconds=None,
                    started_ns=1, finished_ns=2,
                ),),
            )

    orchestrator = object.__new__(Orchestrator)
    orchestrator.persona_engine = PersonaEngine()
    orchestrator.persona_factual_bridge = Boundary()
    orchestrator.llm_adapter = Adapter()
    result = orchestrator._resolver_ruta_persona(
        event, {}, "", "exploradora", identity_context=_IDENTITY,
    )
    assert orchestrator.persona_factual_bridge.prepares == 1
    assert len(set(orchestrator.persona_factual_bridge.turn_ids)) == 1
    assert len(orchestrator.persona_factual_bridge.turn_ids) == 2
    assert orchestrator.llm_adapter.calls == 2
    assert "Cube" in result.parametros["texto_a_comunicar"]
    assert "Changed" not in result.parametros["texto_a_comunicar"]
