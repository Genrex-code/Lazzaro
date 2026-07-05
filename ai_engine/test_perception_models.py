from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.perception_models import (
    ALLOWED_AFFORDANCES,
    PerceivedObject,
    PerceptionSnapshot,
    normalize_affordances,
    normalize_perception,
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_legacy_string_to_snapshot() -> None:
    snapshot = normalize_perception("Frente a mi veo: Cube a 2.5m al norte y Sphere a 4m al este.")

    assert_true(isinstance(snapshot, PerceptionSnapshot), "Debe devolver PerceptionSnapshot.")
    assert_true(snapshot.source == "unity", "El source legacy debe ser unity.")
    assert_true(len(snapshot.objects) == 2, "El texto legacy debe producir dos objetos.")
    assert_true(snapshot.objects[0].display_name == "Cube", "Debe conservar el nombre Cube.")
    assert_true(snapshot.objects[0].distance == 2.5, "Debe extraer distancia de Cube.")
    assert_true(snapshot.objects[1].display_name == "Sphere", "Debe conservar el nombre Sphere.")
    assert_true(snapshot.objects[1].confidence < 1.0, "La percepcion textual legacy debe ser conservadora.")


def test_unity_dict_to_snapshot_with_affordance_schema() -> None:
    snapshot = normalize_perception(
        {
            "snapshot_id": "unity-test-001",
            "objetos_normalizados": [
                {
                    "id": "Tree_01",
                    "nombre": "Tree_01",
                    "aliases": ["arbol", "pino"],
                    "distancia": "3.2",
                    "posicion": {"x": 1, "y": 0, "z": 2},
                    "affordances": ["da_sombra", "agarrar_objeto", "mirar"],
                }
            ],
            "unknown_affordances": ["se_puede_agarrar"],
        }
    )

    assert_true(snapshot.snapshot_id == "unity-test-001", "Debe respetar snapshot_id entrante.")
    assert_true(len(snapshot.objects) == 1, "Debe normalizar un objeto Unity.")
    tree = snapshot.objects[0]
    assert_true(isinstance(tree, PerceivedObject), "Debe crear PerceivedObject.")
    assert_true(tree.id == "Tree_01", "Debe conservar id.")
    assert_true("arbol" in tree.aliases, "Debe conservar aliases humanos.")
    assert_true(tree.distance == 3.2, "Debe normalizar distancia.")
    assert_true("da_sombra" in tree.affordances, "Debe conservar affordance permitida.")
    assert_true("mirar" in tree.affordances, "Debe conservar affordance permitida.")
    assert_true("agarrar_objeto" in snapshot.unknown_affordances, "Debe reportar affordance desconocida.")
    assert_true("se_puede_agarrar" in snapshot.unknown_affordances, "Debe conservar unknown_affordances de Unity.")


def test_future_desktop_format() -> None:
    snapshot = normalize_perception(
        {
            "source": "desktop",
            "objects": [
                {
                    "id": "chrome_button_refresh",
                    "display_name": "Actualizar",
                    "aliases": "refresh, recargar",
                    "screen_position": {"x": 20, "y": 40, "z": 0},
                    "affordances": ["boton", "clicable"],
                    "confidence": 0.9,
                }
            ],
        }
    )

    assert_true(snapshot.source == "desktop", "Debe conservar source desktop.")
    assert_true(snapshot.objects[0].screen_position == {"x": 20.0, "y": 40.0, "z": 0.0}, "Debe normalizar pantalla.")
    assert_true("refresh" in snapshot.objects[0].aliases, "Debe separar aliases string.")
    assert_true("clicable" in snapshot.objects[0].affordances, "Debe aceptar affordance desktop.")


def test_affordance_closed_schema() -> None:
    allowed, unknown = normalize_affordances(["clicable", "se_puede_agarrar", "da sombra"])

    assert_true("clicable" in allowed, "Debe aceptar affordances canonicas.")
    assert_true("da_sombra" in allowed, "Debe normalizar espacios a underscore.")
    assert_true("se_puede_agarrar" in unknown, "Debe rechazar sinonimos libres.")
    assert_true("agarrable" in ALLOWED_AFFORDANCES, "El schema cerrado debe incluir agarrable.")


def main() -> None:
    print("=== INICIANDO PRUEBA DE MODELOS DE PERCEPCION ===\n")
    test_legacy_string_to_snapshot()
    test_unity_dict_to_snapshot_with_affordance_schema()
    test_future_desktop_format()
    test_affordance_closed_schema()
    print("[OK] test_perception_models paso: percepcion normalizada estable.")


if __name__ == "__main__":
    main()
