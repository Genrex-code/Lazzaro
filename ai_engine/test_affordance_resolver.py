from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.affordance_resolver import AffordanceResolver
from ai_engine.perception_models import PerceivedObject


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_grab_requires_grabbable() -> None:
    resolver = AffordanceResolver()
    tree = PerceivedObject(
        id="Tree_01",
        display_name="Tree_01",
        aliases=["arbol"],
        affordances=["mirar", "da_sombra"],
    )

    decision = resolver.validate(tree, "agarrar_objeto")

    assert_true(not decision.allowed, "Agarrar debe fallar sin affordance agarrable.")
    assert_true(decision.status == "not_allowed", "Debe devolver not_allowed.")
    assert_true("agarrable" in decision.missing, "Debe auditar affordance faltante.")


def test_grab_allowed_when_grabbable() -> None:
    resolver = AffordanceResolver()
    flower = PerceivedObject(
        id="Flower_01",
        display_name="Flower_01",
        aliases=["flor"],
        affordances=["agarrable", "mirar"],
    )

    decision = resolver.validate(flower, "agarrar_objeto")

    assert_true(decision.allowed, "Objeto agarrable debe permitir agarrar.")
    assert_true(decision.status == "allowed", "Debe devolver allowed.")
    assert_true("agarrable" in decision.matched, "Debe auditar affordance usada.")


def test_interaction_type_uses_specific_rules() -> None:
    resolver = AffordanceResolver()
    tablet = PerceivedObject(
        id="Tablet_01",
        display_name="Tablet_01",
        aliases=["tablet"],
        affordances=["inspeccionable"],
    )

    mirar = resolver.validate(tablet, "interactuar_objeto_3d", interaction_type="mirar")
    activar = resolver.validate(tablet, "interactuar_objeto_3d", interaction_type="activar")

    assert_true(mirar.allowed, "Mirar debe permitir inspeccionable.")
    assert_true(not activar.allowed, "Activar debe requerir activable.")
    assert_true("activable" in activar.missing, "Debe auditar activable faltante.")


def test_query_affordances_and_score() -> None:
    resolver = AffordanceResolver()
    tree = PerceivedObject(
        id="Tree_01",
        display_name="Tree_01",
        aliases=["arbol"],
        affordances=["da_sombra", "cubre"],
    )

    requested = resolver.requested_from_query("busca algo para cubrirte")
    score = resolver.score(tree, action="navegar_a_objeto", requested_affordances=requested)

    assert_true({"cubre", "da_sombra"} & requested, "Debe detectar affordances de cobertura.")
    assert_true(score == 100.0, "Objeto con affordance solicitada debe puntuar 100.")


def test_risk_penalty_for_danger() -> None:
    resolver = AffordanceResolver()
    fire = PerceivedObject(
        id="Fire_01",
        display_name="Fire_01",
        aliases=["fuego"],
        affordances=["peligro", "mirar"],
    )

    decision = resolver.validate(fire, "interactuar_objeto_3d", interaction_type="mirar")

    assert_true(decision.allowed, "Mirar un peligro puede seguir permitido.")
    assert_true(decision.risk_penalty > 0.0, "Debe producir penalizacion de riesgo.")


def main() -> None:
    print("=== INICIANDO PRUEBA DE AFFORDANCE RESOLVER ===\n")
    test_grab_requires_grabbable()
    test_grab_allowed_when_grabbable()
    test_interaction_type_uses_specific_rules()
    test_query_affordances_and_score()
    test_risk_penalty_for_danger()
    print("[OK] test_affordance_resolver paso: affordances validadas.")


if __name__ == "__main__":
    main()
