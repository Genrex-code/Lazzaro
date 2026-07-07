from __future__ import annotations

from collections import OrderedDict
import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.fuzzy_object_resolver import FuzzyObjectResolver
from ai_engine.learned_alias_candidates import LearnedAliasCandidate, build_learned_alias_candidate
from ai_engine.perception_models import PerceivedObject, PerceptionSnapshot
from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.orchestrator import Orchestrator
from memory.memory_encoder import MemoryEncoder
from memory.memory_manager import MemoryManager


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _resolution(score: float = 100.0, known_aliases: list[str] | None = None) -> dict:
    return {
        "status": "ready",
        "action": "navegar_a_objeto",
        "target_id": "Flowers_01_LOD0",
        "target_display_name": "Flowers_01_LOD0",
        "legacy_nombre_objeto": "Flowers_01_LOD0",
        "confidence": 90.0,
        "candidates": [
            {
                "target_id": "Flowers_01_LOD0",
                "display_name": "Flowers_01_LOD0",
                "legacy_nombre_objeto": "Flowers_01_LOD0",
                "confidence": 90.0,
                "score_components": {
                    "name_score": score,
                    "alias_score": score,
                    "final_score": 90.0,
                },
                "known_aliases": known_aliases or ["Flowers_01_LOD0"],
            }
        ],
    }


def test_candidate_requires_success_and_high_score() -> None:
    candidate = build_learned_alias_candidate(
        user_query="camina a la flor",
        resolution=_resolution(score=100.0),
        action_success=True,
        action="navegar_a_objeto",
    )

    assert_true(candidate is not None, "Debe generar candidato cuando hay exito y score alto.")
    assert_true(candidate.human_concept == "flor", "Debe extraer concepto humano limpio.")
    assert_true(candidate.object_id == "Flowers_01_LOD0", "Debe apuntar al objeto validado.")
    assert_true(candidate.confidence == 1.0, "Debe normalizar confianza a 0..1.")

    low_score = build_learned_alias_candidate(
        user_query="camina a la flor",
        resolution=_resolution(score=91.0),
        action_success=True,
        action="navegar_a_objeto",
    )
    failed_action = build_learned_alias_candidate(
        user_query="camina a la flor",
        resolution=_resolution(score=100.0),
        action_success=False,
        action="navegar_a_objeto",
    )
    duplicate_alias = build_learned_alias_candidate(
        user_query="camina a la flor",
        resolution=_resolution(score=100.0, known_aliases=["flor"]),
        action_success=True,
        action="navegar_a_objeto",
    )

    assert_true(low_score is None, "No debe generar candidato bajo 92.")
    assert_true(failed_action is None, "No debe generar candidato sin exito fisico.")
    assert_true(duplicate_alias is None, "No debe duplicar alias ya conocido.")


def test_fuzzy_resolver_uses_learned_alias_provider() -> None:
    resolver = FuzzyObjectResolver(
        learned_alias_provider=lambda object_ids: {"Flowers_01_LOD0": ["flor"]}
    )
    snapshot = PerceptionSnapshot(
        snapshot_id="learned-alias-snapshot",
        source="unity",
        objects=[
            PerceivedObject(
                id="Flowers_01_LOD0",
                display_name="Flowers_01_LOD0",
                aliases=["Flowers_01_LOD0"],
                affordances=["mirar"],
            )
        ],
    )

    result = resolver.resolve(
        "camina a la flor",
        snapshot,
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
    )

    assert_true(result.status == "ready", "Alias aprendido debe resolver por memoria rapida.")
    assert_true(result.target_id == "Flowers_01_LOD0", "Debe seleccionar el objeto aprendido.")
    assert_true(
        result.audit["learned_alias_hits"]["Flowers_01_LOD0"] == ["flor"],
        "Debe auditar alias aprendido usado.",
    )


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
    manager._alias_aprendidos_guardados = set()
    return manager


def test_memory_manager_persists_alias_without_duplicates() -> None:
    manager = _memory_manager_fake()
    candidate = LearnedAliasCandidate(
        object_id="Flowers_01_LOD0",
        human_concept="flor",
        confidence=0.98,
        evidence="usuario dijo flor y accion fue exitosa",
        action="navegar_a_objeto",
    )

    first_id = manager.guardar_alias_aprendido(candidate)
    second_id = manager.guardar_alias_aprendido(candidate)

    assert_true(first_id is not None, "Debe persistir alias nuevo.")
    assert_true(second_id is None, "No debe duplicar alias ya guardado.")
    assert_true(len(manager.semantic_store.stored) == 1, "Debe escribir una sola regla semantica.")
    stored = manager.semantic_store.stored[0]
    assert_true(stored.metadata.tipo_evento == "alias_aprendido", "Debe usar tipo semantico correcto.")
    assert_true(stored.metadata.extra["object_id"] == "Flowers_01_LOD0", "Debe guardar object_id en metadata.")


class FakeMemory:
    def __init__(self) -> None:
        self.saved = []

    def guardar_alias_aprendido(self, candidate):
        self.saved.append(candidate)
        return "alias-id"


def test_core_approves_pending_alias_only_after_success() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.memory = FakeMemory()
    orchestrator._alias_candidates_pendientes = OrderedDict()

    response = ActionResponse(
        pensamiento="",
        accion="navegar_a_objeto",
        parametros={"nombre_objeto": "Flowers_01_LOD0"},
        razon="validada",
        metadata={
            "alias_learning": {
                "user_query": "camina a la flor",
                "action": "navegar_a_objeto",
            },
            "motor_resolution": _resolution(score=100.0),
        },
    )
    outgoing = AccionSaliente(
        id_accion="action-1",
        nombre="navegar_a_objeto",
        parametros={"nombre_objeto": "Flowers_01_LOD0"},
    )
    orchestrator._registrar_alias_candidato_pendiente(response, outgoing)

    failed_event = EventoEntrante(
        origen="unity",
        tipo_evento="resultado_accion",
        id_transaccion="alias-failed",
        detalles={"id_accion": "action-1", "accion": "navegar_a_objeto", "exito": False},
    )
    orchestrator._registrar_alias_aprendido_si_corresponde(failed_event)
    assert_true(not orchestrator.memory.saved, "No debe aprobar alias con resultado fallido.")

    orchestrator._registrar_alias_candidato_pendiente(response, outgoing)
    success_event = EventoEntrante(
        origen="unity",
        tipo_evento="resultado_accion",
        id_transaccion="alias-success",
        detalles={"id_accion": "action-1", "accion": "navegar_a_objeto", "exito": True},
    )
    orchestrator._registrar_alias_aprendido_si_corresponde(success_event)

    assert_true(len(orchestrator.memory.saved) == 1, "Debe aprobar alias tras exito.")
    assert_true(orchestrator.memory.saved[0].human_concept == "flor", "Debe persistir concepto humano.")


def main() -> None:
    print("=== INICIANDO PRUEBA DE LEARNED ALIAS CANDIDATES ===\n")
    test_candidate_requires_success_and_high_score()
    test_fuzzy_resolver_uses_learned_alias_provider()
    test_memory_manager_persists_alias_without_duplicates()
    test_core_approves_pending_alias_only_after_success()
    print("[OK] test_learned_alias_candidates paso: diccionario evolutivo estable.")


if __name__ == "__main__":
    main()
