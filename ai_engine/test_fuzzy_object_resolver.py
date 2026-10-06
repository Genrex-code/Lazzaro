from __future__ import annotations

import sys
from pathlib import Path

import pytest


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.fuzzy_object_resolver import FuzzyObjectResolver
from ai_engine.motor_resolution import CandidateMatch
from ai_engine.perception_models import PerceivedObject, PerceptionSnapshot


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _snapshot(*objects: PerceivedObject) -> PerceptionSnapshot:
    return PerceptionSnapshot(snapshot_id="unit-test-snapshot", objects=list(objects), source="unity")


def test_exact_alias_ready_with_audit() -> None:
    resolver = FuzzyObjectResolver()
    snapshot = _snapshot(
        PerceivedObject(id="Rock_01", display_name="Rock_01", aliases=["roca", "piedra"], affordances=["mirar"]),
        PerceivedObject(id="Tree_01", display_name="Tree_01", aliases=["arbol"], affordances=["da_sombra"]),
    )

    result = resolver.resolve(
        "camina a la piedra",
        snapshot,
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
    )

    assert_true(result.status == "ready", "Alias exacto debe resolver ready.")
    assert_true(result.target_id == "Rock_01", "Debe elegir Rock_01.")
    assert_true(result.legacy_nombre_objeto == "Rock_01", "Debe conservar nombre legacy para Unity.")
    metadata = result.to_metadata()
    assert_true("motor_resolution" in metadata, "Debe serializar motor_resolution.")
    assert_true("motor_audit" in metadata, "Debe serializar motor_audit.")
    assert_true("score_components" in result.candidates[0].to_dict(), "Debe exponer componentes de score.")


def test_typo_enters_clarification_zone() -> None:
    resolver = FuzzyObjectResolver()
    snapshot = _snapshot(
        PerceivedObject(id="Sphere", display_name="Sphere", aliases=["sphere", "esfera"], affordances=["mirar"]),
    )

    result = resolver.resolve(
        "camina a la spehre",
        snapshot,
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
    )

    assert_true(result.status == "requires_clarification", "Typo fuerte debe pedir aclaracion, no ejecutar.")
    assert_true(result.target_id == "Sphere", "Debe conservar el candidato plausible.")
    assert_true(result.audit["status_reason"] == "between_clarify_and_execute", "Debe auditar zona de aclaracion.")


def test_close_candidates_require_clarification() -> None:
    resolver = FuzzyObjectResolver()
    snapshot = _snapshot(
        PerceivedObject(id="Rock_01", display_name="Rock_01", aliases=["roca"], distance=2.0, affordances=["mirar"]),
        PerceivedObject(id="Rock_02", display_name="Rock_02", aliases=["roca"], distance=5.0, affordances=["mirar"]),
    )

    result = resolver.resolve(
        "ve a la roca",
        snapshot,
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
    )

    assert_true(result.status == "requires_clarification", "Dos rocas cercanas no deben ejecutarse.")
    assert_true(len(result.candidates) == 2, "Debe devolver candidatos para preguntar.")
    assert_true(result.candidates[0].distance == 2.0, "Debe conservar distancia en candidatos.")
    assert_true(result.audit["status_reason"] == "close_candidates", "Debe auditar ambiguedad.")


def test_clarification_candidates_are_limited_to_three() -> None:
    resolver = FuzzyObjectResolver()
    snapshot = _snapshot(
        PerceivedObject(id="Rock_01", display_name="Rock_01", aliases=["roca"], distance=1.0, affordances=["mirar"]),
        PerceivedObject(id="Rock_02", display_name="Rock_02", aliases=["roca"], distance=2.0, affordances=["mirar"]),
        PerceivedObject(id="Rock_03", display_name="Rock_03", aliases=["roca"], distance=3.0, affordances=["mirar"]),
        PerceivedObject(id="Rock_04", display_name="Rock_04", aliases=["roca"], distance=4.0, affordances=["mirar"]),
    )

    result = resolver.resolve(
        "ve a la roca",
        snapshot,
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
    )

    assert_true(result.status == "requires_clarification", "Muchas rocas deben pedir aclaracion.")
    assert_true(len(result.candidates) == 3, "La aclaracion debe incluir maximo 3 candidatos.")


def test_affordance_query_can_select_utility_object() -> None:
    resolver = FuzzyObjectResolver()
    snapshot = _snapshot(
        PerceivedObject(id="Tree_01", display_name="Tree_01", aliases=["arbol"], affordances=["da_sombra", "cubre"]),
        PerceivedObject(id="Rock_01", display_name="Rock_01", aliases=["roca"], affordances=["obstaculo"]),
    )

    result = resolver.resolve(
        "busca algo para cubrirte",
        snapshot,
        intent="MOTOR_REFERENCE",
        action="navegar_a_objeto",
        context={"fatiga": 0.8},
    )

    assert_true(result.status == "ready", "Affordance clara debe resolver objeto util.")
    assert_true(result.target_id == "Tree_01", "Debe elegir objeto con cubre/da_sombra.")
    assert_true(result.candidates[0].score_components["affordance_score"] == 100.0, "Debe auditar affordance.")
    assert_true(result.candidates[0].score_components["context_bonus"] > 0.0, "Fatiga debe aportar bonus acotado.")


def test_action_not_allowed_when_affordance_missing() -> None:
    resolver = FuzzyObjectResolver()
    snapshot = _snapshot(
        PerceivedObject(id="Tree_01", display_name="Tree_01", aliases=["arbol"], affordances=["mirar", "da_sombra"]),
    )

    result = resolver.resolve(
        "agarra el arbol",
        snapshot,
        intent="INTERACTION",
        action="agarrar_objeto",
    )

    assert_true(result.status == "not_allowed", "Objeto claro sin agarrable debe ser not_allowed.")
    assert_true(result.target_id == "Tree_01", "Debe identificar el objeto rechazado.")
    assert_true(result.audit["status_reason"] == "affordance_block", "Debe auditar affordance faltante.")


def test_hazard_approach_is_unsafe_regardless_of_ranking() -> None:
    resolver = FuzzyObjectResolver()
    fire = PerceivedObject(
        id="Fire_01",
        display_name="Fire_01",
        aliases=["fuego"],
        affordances=["mirar", "peligro"],
    )

    result = resolver.resolve("fuego", _snapshot(fire), action="navegar_a_objeto")

    assert result.status == "unsafe"
    decision = result.candidates[0].affordance_decision
    assert decision["safety_assessment"] == {
        "contract_version": 1,
        "disposition": "UNSAFE",
        "reason_code": "HAZARD_APPROACH",
        "ranking_penalty": 20.0,
    }

    score_100_candidate = CandidateMatch(
        target_id=fire.id,
        display_name=fire.display_name,
        confidence=100.0,
        score_components={"name_score": 100.0, "alias_score": 100.0},
        affordance_decision=decision,
    )
    assert resolver._blocking_affordance_status(score_100_candidate) == "unsafe"


def test_hazard_retreat_remains_ready_without_historical_penalty() -> None:
    resolver = FuzzyObjectResolver()
    fire = PerceivedObject(
        id="Fire_01",
        display_name="Fire_01",
        aliases=["fuego"],
        affordances=["peligro", "obstaculo"],
    )

    result = resolver.resolve("fuego", _snapshot(fire), action="alejarse_de_objeto")

    assert result.status == "ready"
    candidate = result.candidates[0]
    assert candidate.score_components["risk_penalty"] == 0.0
    assert candidate.affordance_decision["safety_assessment"]["disposition"] == "SAFE"
    assert candidate.affordance_decision["safety_assessment"]["reason_code"] == "HAZARD_AVOIDANCE"


def test_generic_hazard_interaction_without_explicit_type_is_indeterminate() -> None:
    resolver = FuzzyObjectResolver()
    panel = PerceivedObject(
        id="Panel_01",
        display_name="Panel_01",
        aliases=["panel"],
        affordances=["mirar", "peligro"],
    )

    result = resolver.resolve("panel", _snapshot(panel), action="interactuar_objeto_3d")

    assert result.status == "unsafe"
    assessment = result.candidates[0].affordance_decision["safety_assessment"]
    assert assessment["disposition"] == "INDETERMINATE"
    assert assessment["reason_code"] == "HAZARD_INTERACTION_UNSPECIFIED"
    assert assessment["ranking_penalty"] == 20.0


def test_safety_assessment_is_stable_across_order_source_context_and_display_name() -> None:
    resolver = FuzzyObjectResolver()
    hazard_a = PerceivedObject(
        id="Hazard",
        display_name="Red Hazard",
        aliases=["hazard"],
        affordances=["mirar", "peligro"],
        source="unity",
    )
    hazard_b = PerceivedObject(
        id="Hazard",
        display_name="Renamed Hazard",
        aliases=["hazard"],
        affordances=["mirar", "peligro"],
        source="desktop",
    )
    neutral = PerceivedObject(id="Neutral", display_name="Neutral", aliases=["neutral"], affordances=["mirar"])

    first = resolver._score_objects("hazard", [hazard_a, neutral], action="navegar_a_objeto", context={"estres": 0.0})
    second = resolver._score_objects("hazard", [neutral, hazard_b], action="navegar_a_objeto", context={"estres": 1.0})
    first_assessment = next(candidate for candidate in first if candidate.target_id == "Hazard").affordance_decision["safety_assessment"]
    second_assessment = next(candidate for candidate in second if candidate.target_id == "Hazard").affordance_decision["safety_assessment"]
    assert first_assessment == second_assessment
    assert first_assessment["disposition"] == "UNSAFE"

    rewritten_reason = resolver.affordances.validate(hazard_a, "navegar_a_objeto").to_dict()
    rewritten_reason["reason"] = "diagnostico humano deliberadamente distinto"
    candidate = CandidateMatch(
        target_id=hazard_a.id,
        display_name=hazard_a.display_name,
        confidence=100.0,
        score_components={"name_score": 100.0, "alias_score": 100.0},
        affordance_decision=rewritten_reason,
    )
    assert resolver._blocking_affordance_status(candidate) == "unsafe"


def test_unexpected_safety_exception_propagates_to_existing_motor_boundary() -> None:
    class ExplodingAffordances:
        def requested_from_query(self, query):
            return set()

        def validate(self, obj, action, *, interaction_type=None):
            raise RuntimeError("synthetic safety defect")

        def score(self, obj, **kwargs):
            return 100.0

    resolver = FuzzyObjectResolver(affordance_resolver=ExplodingAffordances())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="synthetic safety defect"):
        resolver.resolve(
            "fuego",
            _snapshot(PerceivedObject(id="Fire", display_name="Fire", aliases=["fuego"], affordances=["peligro"])),
            action="navegar_a_objeto",
        )


def main() -> None:
    print("=== INICIANDO PRUEBA DE FUZZY OBJECT RESOLVER ===\n")
    test_exact_alias_ready_with_audit()
    test_typo_enters_clarification_zone()
    test_close_candidates_require_clarification()
    test_clarification_candidates_are_limited_to_three()
    test_affordance_query_can_select_utility_object()
    test_action_not_allowed_when_affordance_missing()
    print("[OK] test_fuzzy_object_resolver paso: resolver aislado estable.")


if __name__ == "__main__":
    main()
