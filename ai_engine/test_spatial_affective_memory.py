from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.fuzzy_object_resolver import CONTEXT_BONUS_LIMIT, FuzzyObjectResolver
from ai_engine.motor_engine import MotorEngine
from ai_engine.perception_models import PerceivedObject, PerceptionSnapshot
from core.orchestrator import Orchestrator
from memory.memory_encoder import MemoryEncoder
from memory.memory_manager import MemoryManager


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeSemanticStore:
    def __init__(self) -> None:
        self.stored = []

    def almacenar(self, recuerdo) -> bool:
        self.stored.append(recuerdo)
        return True


class FakeClient:
    def __init__(self, metadatas: list[dict] | None = None) -> None:
        self.metadatas = metadatas or []

    @staticmethod
    def combinar_where(base, extra):
        return {"$and": [base, extra]}

    def listar(self, where=None, limite=None):
        return {"metadatas": list(self.metadatas)}


def _memory_manager_fake(metadatas: list[dict] | None = None) -> MemoryManager:
    manager = object.__new__(MemoryManager)
    manager.client = FakeClient(metadatas)
    manager.encoder = MemoryEncoder()
    manager.semantic_store = FakeSemanticStore()
    manager._memorias_afectivas_guardadas = set()
    return manager


def _snapshot(*objects: PerceivedObject) -> PerceptionSnapshot:
    return PerceptionSnapshot(snapshot_id="spatial-affect-test", objects=list(objects), source="unity")


def _candidate_by_id(result, target_id: str):
    for candidate in result.candidates:
        if candidate.target_id == target_id:
            return candidate
    return None


def test_memory_manager_stores_affective_memory_as_metadata() -> None:
    manager = _memory_manager_fake()

    memory_id = manager.guardar_memoria_afectiva_espacial(
        object_id="Tree_01",
        object_class="tree",
        affect="alivio",
        weight=0.8,
        source="rest_event",
        confirmed=True,
    )

    assert_true(memory_id is not None, "Debe guardar memoria afectiva nueva.")
    assert_true(len(manager.semantic_store.stored) == 1, "Debe persistir una regla semantica.")
    stored = manager.semantic_store.stored[0]
    assert_true(stored.metadata.tipo_evento == "memoria_afectiva_espacial", "Debe usar tipo semantico especifico.")
    assert_true(stored.metadata.extra["object_id"] == "Tree_01", "Debe guardar object_id como metadata.")
    assert_true(stored.metadata.extra["affect"] == "alivio", "Debe guardar affect normalizado.")
    assert_true("accion" not in stored.metadata.extra, "No debe guardar comandos fisicos en metadata.")


def test_memory_manager_reads_affective_memory_by_object_and_class() -> None:
    manager = _memory_manager_fake(
        [
            {
                "object_id": "Tree_01",
                "object_class": "tree",
                "affect": "alivio",
                "weight": 0.8,
                "source": "rest_event",
                "confirmed": True,
            },
            {
                "object_id": "",
                "object_class": "rock",
                "affect": "alerta",
                "weight": 0.6,
                "source": "pain_event",
                "confirmed": True,
            },
        ]
    )
    refs = [
        PerceivedObject(id="Tree_01", display_name="Tree_01", aliases=["arbol"], affordances=["da_sombra"]),
        PerceivedObject(id="Rock_01", display_name="Rock_01", aliases=["roca"], affordances=["obstaculo"]),
    ]

    hints = manager.obtener_memorias_afectivas_espaciales(refs)

    assert_true("Tree_01" in hints, "Debe leer memoria afectiva por object_id.")
    assert_true("class:rock" in hints, "Debe leer memoria afectiva por clase.")
    assert_true(hints["Tree_01"][0]["weight"] == 0.8, "Debe conservar peso afectivo.")


def test_affective_memory_bonus_is_capped_and_audited() -> None:
    resolver = FuzzyObjectResolver(
        affective_memory_provider=lambda objects: {
            "Tree_01": [
                {
                    "object_id": "Tree_01",
                    "object_class": "tree",
                    "affect": "alivio",
                    "weight": 0.8,
                    "source": "rest_event",
                    "confirmed": True,
                }
            ]
        }
    )
    snapshot = _snapshot(
        PerceivedObject(id="Tree_01", display_name="Tree_01", aliases=["arbol"], affordances=["da_sombra"]),
    )

    result = resolver.resolve(
        "camina al arbol",
        snapshot,
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
        context={"fatiga": 0.95},
    )

    candidate = result.candidates[0]
    assert_true(candidate.score_components["affective_bonus"] == 3.2, "Peso 0.8 debe aportar bonus afectivo acotado.")
    assert_true(candidate.score_components["context_bonus"] <= CONTEXT_BONUS_LIMIT, "No debe superar limite total.")
    assert_true("Tree_01" in result.audit["affective_memory_hits"], "Debe auditar memoria afectiva usada.")


def test_affective_memory_does_not_override_explicit_target() -> None:
    resolver = FuzzyObjectResolver(
        affective_memory_provider=lambda objects: {
            "Tree_01": [
                {
                    "object_id": "Tree_01",
                    "object_class": "tree",
                    "affect": "alivio",
                    "weight": 1.0,
                    "source": "rest_event",
                    "confirmed": True,
                }
            ]
        }
    )
    snapshot = _snapshot(
        PerceivedObject(id="Tree_01", display_name="Tree_01", aliases=["arbol"], affordances=["da_sombra"]),
        PerceivedObject(id="Rock_01", display_name="Rock_01", aliases=["roca"], affordances=["mirar"]),
    )

    result = resolver.resolve(
        "camina a la roca",
        snapshot,
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
        context={},
    )

    tree_candidate = _candidate_by_id(result, "Tree_01")
    assert_true(result.target_id == "Rock_01", "Memoria afectiva no debe desplazar objetivo explicito.")
    assert_true(tree_candidate is not None, "Debe conservar candidato afectivo en auditoria.")
    assert_true(tree_candidate.score_components["affective_bonus"] <= 4.0, "Bonus afectivo debe ser pequeno.")


def test_alert_affect_only_biases_avoidance() -> None:
    hint = [{"affect": "alerta", "weight": 1.0, "confirmed": True}]

    navigate_bonus = FuzzyObjectResolver._affective_context_bonus(hint, action="navegar_a_objeto")
    avoid_bonus = FuzzyObjectResolver._affective_context_bonus(hint, action="alejarse_de_objeto")

    assert_true(navigate_bonus == 0.0, "Alerta no debe atraer navegacion hacia peligro.")
    assert_true(avoid_bonus == 4.0, "Alerta puede sesgar alejamiento de forma acotada.")


class FakeMemory:
    def obtener_aliases_aprendidos(self, object_ids=None):
        return {}

    def obtener_memorias_afectivas_espaciales(self, object_refs=None):
        return {"Tree_01": [{"affect": "alivio", "weight": 0.5, "confirmed": True}]}


def test_orchestrator_connects_affective_provider_without_autonomy() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.memory = FakeMemory()
    orchestrator.motor_engine = MotorEngine()

    orchestrator._conectar_aliases_aprendidos()

    provider = orchestrator.motor_engine.fuzzy_resolver.affective_memory_provider
    assert_true(
        callable(provider) and provider([]) == {"Tree_01": [{"affect": "alivio", "weight": 0.5, "confirmed": True}]},
        "Core debe conectar provider afectivo como lectura.",
    )
    assert_true(
        not hasattr(orchestrator.motor_engine.fuzzy_resolver, "autonomous_action_provider"),
        "Fase 10 no debe crear proveedor de acciones autonomas.",
    )


def main() -> None:
    print("=== INICIANDO PRUEBA DE MEMORIA AFECTIVA ESPACIAL ===\n")
    test_memory_manager_stores_affective_memory_as_metadata()
    test_memory_manager_reads_affective_memory_by_object_and_class()
    test_affective_memory_bonus_is_capped_and_audited()
    test_affective_memory_does_not_override_explicit_target()
    test_alert_affect_only_biases_avoidance()
    test_orchestrator_connects_affective_provider_without_autonomy()
    print("[OK] test_spatial_affective_memory paso: memoria afectiva sin autonomia fisica.")


if __name__ == "__main__":
    main()
