from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.fuzzy_object_resolver import FuzzyObjectResolver
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
