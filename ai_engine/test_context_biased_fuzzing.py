from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.fuzzy_object_resolver import CONTEXT_BONUS_LIMIT, FuzzyObjectResolver
from core.identity_snapshot import SelfStateSnapshotV1
from ai_engine.perception_models import PerceivedObject, PerceptionSnapshot, normalize_perception
from core.orchestrator import Orchestrator


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _snapshot(*objects: PerceivedObject) -> PerceptionSnapshot:
    return PerceptionSnapshot(snapshot_id="context-biased-test", objects=list(objects), source="unity")


def _candidate_by_id(result, target_id: str):
    for candidate in result.candidates:
        if candidate.target_id == target_id:
            return candidate
    return None


def test_context_bonus_is_capped() -> None:
    resolver = FuzzyObjectResolver()
    obj = PerceivedObject(
        id="Shelter_01",
        display_name="Shelter_01",
        aliases=["refugio"],
        affordances=["da_sombra", "cubre", "descanso", "nuevo", "peligro", "obstaculo"],
    )

    bonus = resolver._context_bonus(obj, {"fatiga": 0.95, "curiosidad": 0.95, "estres": 0.95})

    assert_true(bonus == CONTEXT_BONUS_LIMIT, "El bonus contextual debe recortarse al limite definido.")


def test_fatigue_curiosity_and_stress_add_small_biases() -> None:
    resolver = FuzzyObjectResolver()

    sombra = PerceivedObject(id="Tree_01", display_name="Tree_01", aliases=["arbol"], affordances=["da_sombra"])
    nuevo = PerceivedObject(id="Artifact_01", display_name="Artifact_01", aliases=["artefacto"], affordances=["nuevo"])
    peligro = PerceivedObject(id="Fire_01", display_name="Fire_01", aliases=["fuego"], affordances=["peligro"])

    assert_true(resolver._context_bonus(sombra, {"fatiga": 0.8}) == 6.0, "Fatiga debe favorecer sombra poco.")
    assert_true(resolver._context_bonus(nuevo, {"curiosidad": 80}) == 4.0, "Curiosidad debe aceptar 0..100 y favorecer nuevo.")
    assert_true(resolver._context_bonus(peligro, {"estres": 0.8}) == 4.0, "Estres debe resaltar peligro poco.")


def test_fatigue_does_not_override_explicit_target() -> None:
    resolver = FuzzyObjectResolver()
    snapshot = _snapshot(
        PerceivedObject(
            id="Tree_01",
            display_name="Tree_01",
            aliases=["arbol"],
            affordances=["da_sombra", "cubre", "descanso"],
        ),
        PerceivedObject(
            id="Rock_01",
            display_name="Rock_01",
            aliases=["roca"],
            affordances=["mirar"],
        ),
    )

    result = resolver.resolve(
        "camina a la roca",
        snapshot,
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
        context={"fatiga": 0.95},
    )

    tree_candidate = _candidate_by_id(result, "Tree_01")
    assert_true(result.status == "ready", "Objetivo explicito debe seguir resolviendo ready.")
    assert_true(result.target_id == "Rock_01", "Fatiga no debe cambiar roca explicita por sombra.")
    assert_true(tree_candidate is not None, "Debe conservar candidato de sombra en auditoria.")
    assert_true(
        tree_candidate.score_components["context_bonus"] <= CONTEXT_BONUS_LIMIT,
        "El candidato sesgado debe auditar bonus acotado.",
    )


def test_orchestrator_builds_state_context_for_motor() -> None:
    orchestrator = object.__new__(Orchestrator)
    identity_state = SelfStateSnapshotV1(energia=20.0, estres=80.0, foco=70.0)

    context = orchestrator._contexto_motor_estado(
        "camina al arbol",
        {"curiosidad": 90.0},
        identity_state,
    )

    assert_true(context["fatiga"] == 0.8, "Energia baja debe derivar fatiga normalizada.")
    assert_true(context["estres"] == 0.8, "Estres debe viajar normalizado.")
    assert_true(context["curiosidad"] == 0.9, "Curiosidad de BehaviorCore debe viajar normalizada.")
    assert_true(context["estado"]["fatiga"] == 0.8, "Debe incluir snapshot anidado para auditoria futura.")


def test_nuevo_is_closed_affordance() -> None:
    snapshot = normalize_perception(
        {
            "objetos_normalizados": [
                {"id": "Artifact_01", "display_name": "Artifact_01", "affordances": ["nuevo", "tag_libre"]}
            ]
        }
    )

    assert_true("nuevo" in snapshot.objects[0].affordances, "nuevo debe estar en la ontologia cerrada.")
    assert_true("tag_libre" in snapshot.unknown_affordances, "Tags libres deben seguir marcados como unknown.")


def main() -> None:
    print("=== INICIANDO PRUEBA DE CONTEXT BIASED FUZZING ===\n")
    test_context_bonus_is_capped()
    test_fatigue_curiosity_and_stress_add_small_biases()
    test_fatigue_does_not_override_explicit_target()
    test_orchestrator_builds_state_context_for_motor()
    test_nuevo_is_closed_affordance()
    print("[OK] test_context_biased_fuzzing paso: sesgo contextual acotado y no autonomo.")


if __name__ == "__main__":
    main()
