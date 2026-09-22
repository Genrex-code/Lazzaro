"""Pruebas permanentes del contrato discriminado ROOT-025."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

import ai_engine.triage_router as triage_router_module
from ai_engine.intent_types import IntentType, Route
from ai_engine.triage_contracts import (
    TriageDecisionV1,
    TriageFallbackStatusV1,
    TriageModelStatusV1,
    TriageOperationalOverrideV1,
    TriageOutcomeV1,
    TriageReasonCodeV1,
    TriageSourceV1,
)
from ai_engine.triage_router import TriageRouter


class StaticModel:
    def __init__(self, label: Any, confidence: float = 0.99) -> None:
        self.label = label
        self.confidence = confidence

    def predict(self, textos):
        return [self.label]

    def predict_proba(self, textos):
        return [[self.confidence, 1.0 - self.confidence]]


class PredictRaises:
    def predict(self, textos):
        raise RuntimeError("synthetic predict failure")


class ProbaRaises:
    def predict(self, textos):
        return ["SOCIAL"]

    def predict_proba(self, textos):
        raise RuntimeError("synthetic proba failure")


def _event(text: str) -> dict[str, Any]:
    return {
        "tipo_evento": "comando_usuario",
        "detalles": {"mensaje": text},
    }


def _classification(result: dict[str, Any]) -> dict[str, Any]:
    value = result["classification"]
    assert isinstance(value, dict)
    return value


def _valid_model_decision(**updates: Any) -> TriageDecisionV1:
    data: dict[str, Any] = {
        "outcome": TriageOutcomeV1.DECIDED,
        "source": TriageSourceV1.MODEL,
        "semantic_intent": IntentType.SOCIAL,
        "route": Route.PERSONA,
        "reason_code": TriageReasonCodeV1.MODEL_ACCEPTED,
        "model_status": TriageModelStatusV1.ACCEPTED,
        "fallback_status": TriageFallbackStatusV1.NOT_ATTEMPTED,
        "raw_label": "SOCIAL",
        "human_reason": "diagnostic",
    }
    data.update(updates)
    return TriageDecisionV1(**data)


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 2])
def test_contract_version_is_exact_integer_one(version: Any) -> None:
    with pytest.raises(ValidationError):
        _valid_model_decision(contract_version=version)


def test_contract_is_frozen_forbids_extra_and_omits_confidence() -> None:
    decision = _valid_model_decision()
    with pytest.raises(ValidationError):
        decision.route = Route.MOTOR  # type: ignore[misc]
    with pytest.raises(ValidationError):
        _valid_model_decision(confidence=0.99)
    assert "confidence" not in decision.model_dump(mode="json")


@pytest.mark.parametrize("outcome", [TriageOutcomeV1.DECIDED, TriageOutcomeV1.DEGRADED])
def test_decided_and_degraded_require_a_complete_decision(outcome: TriageOutcomeV1) -> None:
    with pytest.raises(ValidationError):
        _valid_model_decision(outcome=outcome, semantic_intent=None, route=None)


@pytest.mark.parametrize("outcome", [TriageOutcomeV1.UNCERTAIN, TriageOutcomeV1.FAILED])
def test_uncertain_and_failed_cannot_assert_a_decision(outcome: TriageOutcomeV1) -> None:
    with pytest.raises(ValidationError):
        _valid_model_decision(outcome=outcome)


def test_literal_unknown_is_a_valid_model_semantic_class_only() -> None:
    decision = _valid_model_decision(
        semantic_intent=IntentType.UNKNOWN,
        raw_label="UNKNOWN",
    )
    assert decision.semantic_intent is IntentType.UNKNOWN

    with pytest.raises(ValidationError):
        _valid_model_decision(
            semantic_intent=IntentType.UNKNOWN,
            raw_label="CLASS_NOT_IN_CONTRACT",
        )


def test_degraded_requires_accepted_fallback_after_technical_failure() -> None:
    decision = TriageDecisionV1(
        outcome=TriageOutcomeV1.DEGRADED,
        source=TriageSourceV1.FALLBACK,
        semantic_intent=IntentType.SOCIAL,
        route=Route.PERSONA,
        reason_code=TriageReasonCodeV1.FALLBACK_ACCEPTED,
        model_status=TriageModelStatusV1.ERROR,
        fallback_status=TriageFallbackStatusV1.ACCEPTED,
        human_reason="diagnostic",
    )
    assert decision.outcome is TriageOutcomeV1.DEGRADED
    with pytest.raises(ValidationError):
        decision.evolve(model_status=TriageModelStatusV1.LOW_CONFIDENCE)


@pytest.mark.parametrize(
    ("source", "updates"),
    [
        (
            TriageSourceV1.MODEL,
            {"model_status": TriageModelStatusV1.ERROR},
        ),
        (
            TriageSourceV1.FALLBACK,
            {"fallback_status": TriageFallbackStatusV1.NO_MATCH},
        ),
        (
            TriageSourceV1.LEXICAL,
            {
                "model_status": TriageModelStatusV1.NOT_ATTEMPTED,
                "fallback_status": TriageFallbackStatusV1.NOT_ATTEMPTED,
                "raw_label": None,
                "reason_code": TriageReasonCodeV1.LEXICAL_RULE_ACCEPTED,
            },
        ),
        (
            TriageSourceV1.EVENT_RULE,
            {
                "model_status": TriageModelStatusV1.NOT_ATTEMPTED,
                "fallback_status": TriageFallbackStatusV1.NOT_ATTEMPTED,
                "raw_label": None,
                "reason_code": TriageReasonCodeV1.EVENT_RULE_ACCEPTED,
            },
        ),
    ],
)
def test_source_invariants_reject_missing_causal_evidence(
    source: TriageSourceV1,
    updates: dict[str, Any],
) -> None:
    updates["source"] = source
    updates["accepted_rule_id"] = None
    with pytest.raises(ValidationError):
        _valid_model_decision(**updates)


def test_route_is_canonical_without_override() -> None:
    with pytest.raises(ValidationError):
        _valid_model_decision(route=Route.MOTOR)


def test_event_route_override_is_strictly_allowlisted() -> None:
    accepted = TriageDecisionV1(
        outcome=TriageOutcomeV1.DECIDED,
        source=TriageSourceV1.EVENT_RULE,
        semantic_intent=IntentType.CORE_SYSTEM,
        route=Route.MOTOR,
        operational_override=TriageOperationalOverrideV1.EVENT_ROUTE_OVERRIDE,
        reason_code=TriageReasonCodeV1.EVENT_RULE_ACCEPTED,
        model_status=TriageModelStatusV1.NOT_ATTEMPTED,
        fallback_status=TriageFallbackStatusV1.NOT_ATTEMPTED,
        accepted_rule_id="event.alerta_dolor_fisico",
        human_reason="diagnostic",
    )
    assert accepted.route is Route.MOTOR
    with pytest.raises(ValidationError):
        accepted.evolve(semantic_intent=IntentType.SOCIAL)


def test_movement_override_preserves_motor_semantics() -> None:
    decision = _valid_model_decision(
        semantic_intent=IntentType.MOTOR_SIMPLE,
        raw_label="MOTOR_SIMPLE",
        route=Route.NOOP,
        operational_override=TriageOperationalOverrideV1.MOVEMENT_BLOCKED,
        reason_code=TriageReasonCodeV1.MOVEMENT_BLOCKED,
    )
    assert decision.semantic_intent is IntentType.MOTOR_SIMPLE
    with pytest.raises(ValidationError):
        decision.evolve(semantic_intent=IntentType.NOOP)


def test_semantic_unknown_and_incompatible_label_are_distinct() -> None:
    semantic = TriageRouter()
    semantic.model_intent = StaticModel("UNKNOWN")
    semantic_result = semantic.route_event(_event("blup azul siete"), {})
    semantic_classification = _classification(semantic_result)
    assert semantic_classification["outcome"] == "DECIDED"
    assert semantic_classification["semantic_intent"] == "UNKNOWN"
    assert semantic_classification["raw_label"] == "UNKNOWN"

    incompatible = TriageRouter()
    incompatible.model_intent = StaticModel("CLASS_NOT_IN_CONTRACT")
    incompatible_result = incompatible.route_event(_event("blup azul siete"), {})
    incompatible_classification = _classification(incompatible_result)
    assert incompatible_classification["outcome"] == "FAILED"
    assert incompatible_classification["semantic_intent"] is None
    assert incompatible_classification["model_status"] == "INCOMPATIBLE_LABEL"
    assert incompatible_classification["raw_label"] == "CLASS_NOT_IN_CONTRACT"


def test_high_confidence_known_model_class_is_decided() -> None:
    router = TriageRouter()
    router.model_intent = StaticModel("SOCIAL")
    decision = router.classify_event(_event("texto neutral"), {})
    assert decision.outcome is TriageOutcomeV1.DECIDED
    assert decision.source is TriageSourceV1.MODEL
    assert decision.semantic_intent is IntentType.SOCIAL


def test_low_confidence_uses_normal_fallback_without_degradation() -> None:
    router = TriageRouter()
    router.model_intent = StaticModel("SOCIAL", 0.59)
    accepted = router.classify_event(_event("mueve la caja"), {})
    assert accepted.outcome is TriageOutcomeV1.DECIDED
    assert accepted.source is TriageSourceV1.FALLBACK
    assert accepted.model_status is TriageModelStatusV1.LOW_CONFIDENCE
    assert accepted.semantic_intent is IntentType.MOTOR_SIMPLE

    uncertain = router.classify_event(_event("blup azul siete"), {})
    assert uncertain.outcome is TriageOutcomeV1.UNCERTAIN
    assert uncertain.semantic_intent is None
    assert uncertain.fallback_status is TriageFallbackStatusV1.NO_MATCH


def test_model_unavailable_yields_degraded_or_failed_from_same_cause() -> None:
    router = TriageRouter()
    router.model_intent = None
    accepted = router.classify_event(_event("hola"), {})
    assert accepted.outcome is TriageOutcomeV1.DEGRADED
    assert accepted.source is TriageSourceV1.FALLBACK
    assert accepted.model_status is TriageModelStatusV1.UNAVAILABLE

    failed = router.classify_event(_event("blup azul siete"), {})
    assert failed.outcome is TriageOutcomeV1.FAILED
    assert failed.reason_code is TriageReasonCodeV1.MODEL_UNAVAILABLE


@pytest.mark.parametrize("model", [PredictRaises(), ProbaRaises()])
def test_runtime_model_error_yields_degraded_or_failed(model: Any) -> None:
    router = TriageRouter()
    router.model_intent = model
    accepted = router.classify_event(_event("hola"), {})
    assert accepted.outcome is TriageOutcomeV1.DEGRADED
    assert accepted.model_status is TriageModelStatusV1.ERROR

    failed = router.classify_event(_event("blup azul siete"), {})
    assert failed.outcome is TriageOutcomeV1.FAILED
    assert failed.reason_code is TriageReasonCodeV1.MODEL_ERROR
    expected_raw_label = "SOCIAL" if isinstance(model, ProbaRaises) else None
    assert failed.raw_label == expected_raw_label


def test_incompatible_label_can_degrade_to_an_accepted_fallback() -> None:
    router = TriageRouter()
    router.model_intent = StaticModel("CLASS_NOT_IN_CONTRACT")
    decision = router.classify_event(_event("hola"), {})
    assert decision.outcome is TriageOutcomeV1.DEGRADED
    assert decision.source is TriageSourceV1.FALLBACK
    assert decision.semantic_intent is IntentType.SOCIAL
    assert decision.model_status is TriageModelStatusV1.INCOMPATIBLE_LABEL


def test_lexical_veto_is_uncertainty_and_can_allow_an_alternate() -> None:
    router = TriageRouter()
    router.model_intent = StaticModel("MOTOR_SIMPLE")
    no_candidate = router.classify_event(_event("no muevas la caja"), {})
    assert no_candidate.outcome is TriageOutcomeV1.UNCERTAIN
    assert no_candidate.model_status is TriageModelStatusV1.VETOED
    assert no_candidate.fallback_status is TriageFallbackStatusV1.VETOED

    alternate = router.classify_event(_event("no muevas la caja codigo"), {})
    assert alternate.outcome is TriageOutcomeV1.DECIDED
    assert alternate.source is TriageSourceV1.FALLBACK
    assert alternate.semantic_intent is IntentType.ORACLE_QUERY


def test_fallback_exception_has_terminal_reason_precedence(monkeypatch) -> None:
    def fail_fallback(text: str) -> IntentType:
        raise RuntimeError("synthetic fallback failure")

    monkeypatch.setattr(triage_router_module, "match_intent_by_keywords", fail_fallback)
    router = TriageRouter()
    router.model_intent = StaticModel("SOCIAL", 0.59)
    decision = router.classify_event(_event("blup azul siete"), {})
    assert decision.outcome is TriageOutcomeV1.FAILED
    assert decision.fallback_status is TriageFallbackStatusV1.ERROR
    assert decision.reason_code is TriageReasonCodeV1.FALLBACK_ERROR


def test_uncontained_internal_error_still_propagates_to_root_026(monkeypatch) -> None:
    def fail_lexical_analysis(text: str):
        raise RuntimeError("synthetic uncontained triage failure")

    monkeypatch.setattr(
        triage_router_module,
        "analyze_lexical_routing",
        fail_lexical_analysis,
    )
    router = TriageRouter()
    with pytest.raises(RuntimeError, match="synthetic uncontained triage failure"):
        router.classify_event(_event("hola"), {})


def test_hard_empty_and_unsupported_events_are_deterministic() -> None:
    router = TriageRouter()
    router.model_intent = PredictRaises()
    expected = {
        "actualizacion_estado": (IntentType.NOOP, Route.NOOP, None),
        "resultado_accion": (IntentType.INTERNAL, Route.INTERNAL, None),
        "inicio_simulacion": (
            IntentType.CORE_SYSTEM,
            Route.NOOP,
            TriageOperationalOverrideV1.EVENT_ROUTE_OVERRIDE,
        ),
        "alerta_dolor_fisico": (
            IntentType.CORE_SYSTEM,
            Route.MOTOR,
            TriageOperationalOverrideV1.EVENT_ROUTE_OVERRIDE,
        ),
    }
    for event_type, values in expected.items():
        decision = router.classify_event({"tipo_evento": event_type}, {})
        assert (decision.semantic_intent, decision.route, decision.operational_override) == values
        assert decision.model_status is TriageModelStatusV1.NOT_ATTEMPTED

    empty = router.classify_event(_event(""), {})
    assert empty.reason_code is TriageReasonCodeV1.EMPTY_COMMAND
    unsupported = router.classify_event({"tipo_evento": "future_event"}, {})
    assert unsupported.semantic_intent is IntentType.NOOP
    assert unsupported.reason_code is TriageReasonCodeV1.UNSUPPORTED_EVENT


def test_movement_gate_preserves_model_and_degraded_fallback_authority() -> None:
    model_router = TriageRouter()
    model_router.model_intent = StaticModel("MOTOR_SIMPLE")
    model_decision = model_router.classify_event(
        _event("mueve la caja"),
        {"is_moving": True},
    )
    assert model_decision.outcome is TriageOutcomeV1.DECIDED
    assert model_decision.source is TriageSourceV1.MODEL
    assert model_decision.semantic_intent is IntentType.MOTOR_SIMPLE
    assert model_decision.route is Route.NOOP
    assert model_decision.operational_override is TriageOperationalOverrideV1.MOVEMENT_BLOCKED

    degraded_router = TriageRouter()
    degraded_router.model_intent = PredictRaises()
    degraded = degraded_router.classify_event(
        _event("mueve la caja"),
        {"is_moving": True},
    )
    assert degraded.outcome is TriageOutcomeV1.DEGRADED
    assert degraded.source is TriageSourceV1.FALLBACK
    assert degraded.semantic_intent is IntentType.MOTOR_SIMPLE
    assert degraded.route is Route.NOOP
    assert degraded.operational_override is TriageOperationalOverrideV1.MOVEMENT_BLOCKED


def test_legacy_projection_is_canonical_and_classifies_once(monkeypatch) -> None:
    router = TriageRouter()
    router.model_intent = StaticModel("MOTOR_SIMPLE")
    calls = 0
    classify = router.classify_event

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return classify(*args, **kwargs)

    monkeypatch.setattr(router, "classify_event", counted)
    movement = router.route_event(_event("mueve la caja"), {"is_moving": True})
    assert calls == 1
    assert movement["intent"] == "NOOP"
    assert movement["route"] == "noop"
    assert movement["classification"]["semantic_intent"] == "MOTOR_SIMPLE"
    assert movement["classification"]["operational_override"] == "MOVEMENT_BLOCKED"


def test_uncertain_failed_and_unsupported_keep_reviewed_legacy_views() -> None:
    low = TriageRouter()
    low.model_intent = StaticModel("SOCIAL", 0.59)
    uncertain = low.route_event(_event("blup azul siete"), {})
    assert (uncertain["intent"], uncertain["route"]) == ("UNKNOWN", "persona")
    assert uncertain["classification"]["outcome"] == "UNCERTAIN"

    unavailable = TriageRouter()
    unavailable.model_intent = None
    failed = unavailable.route_event(_event("blup azul siete"), {})
    assert (failed["intent"], failed["route"]) == ("UNKNOWN", "persona")
    assert failed["classification"]["outcome"] == "FAILED"

    unsupported = unavailable.route_event({"tipo_evento": "future_event"}, {})
    assert (unsupported["intent"], unsupported["route"], unsupported["confidence"]) == (
        "UNKNOWN",
        "noop",
        0.0,
    )
    assert unsupported["classification"]["semantic_intent"] == "NOOP"
    assert unsupported["classification"]["outcome"] == "DECIDED"
