from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.identity_projection import IdentityContextV1
from ai_engine.schemas import ActionResponse
from ai_engine.subjective_effort import SubjectiveEffortCalculator
from core.orchestrator import Orchestrator
from core.identity_snapshot import (
    IdentitySnapshotV1,
    PersonalitySnapshotV1,
    SelfStateSnapshotV1,
    build_static_self_v1,
)
from memory.working_memory import WorkingMemory


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _metadata_distancia(distancia: float) -> dict:
    return {
        "motor_resolution": {
            "status": "ready",
            "target_id": "Tree_01",
            "candidates": [
                {
                    "target_id": "Tree_01",
                    "display_name": "Tree_01",
                    "legacy_nombre_objeto": "Tree_01",
                    "distance": distancia,
                }
            ],
        }
    }


def test_fatigue_raises_walking_effort_5m() -> None:
    calculator = SubjectiveEffortCalculator()
    rested = calculator.calculate(
        "navegar_a_objeto",
        {"nombre_objeto": "Tree_01"},
        metadata=_metadata_distancia(5.0),
        identity_state={"energia": 95.0, "estres": 5.0},
    )
    exhausted = calculator.calculate(
        "navegar_a_objeto",
        {"nombre_objeto": "Tree_01"},
        metadata=_metadata_distancia(5.0),
        identity_state={"energia": 10.0, "estres": 5.0},
    )

    assert_true(rested is not None and exhausted is not None, "Debe calcular esfuerzo para navegar.")
    assert_true(exhausted.score > rested.score, "Fatiga alta debe subir esfuerzo de caminar 5m.")
    assert_true(exhausted.label in {"alto", "critico"}, "Caminar exhausta debe sentirse alto o critico.")


def test_stress_and_distance_are_monotonic() -> None:
    calculator = SubjectiveEffortCalculator()
    low_stress = calculator.calculate(
        "navegar_a_objeto",
        {"nombre_objeto": "Rock_01"},
        metadata=_metadata_distancia(2.0),
        identity_state={"energia": 60.0, "estres": 0.0},
    )
    high_stress = calculator.calculate(
        "navegar_a_objeto",
        {"nombre_objeto": "Rock_01"},
        metadata=_metadata_distancia(2.0),
        identity_state={"energia": 60.0, "estres": 90.0},
    )
    far = calculator.calculate(
        "navegar_a_objeto",
        {"nombre_objeto": "Rock_01"},
        metadata=_metadata_distancia(8.0),
        identity_state={"energia": 60.0, "estres": 0.0},
    )

    assert_true(low_stress is not None and high_stress is not None and far is not None, "Debe calcular casos base.")
    assert_true(high_stress.score > low_stress.score, "Estres alto debe aumentar esfuerzo.")
    assert_true(far.score > low_stress.score, "Mas distancia debe aumentar esfuerzo.")


def test_orchestrator_attaches_effort_without_changing_action() -> None:
    orquestador = object.__new__(Orchestrator)
    orquestador.subjective_effort = SubjectiveEffortCalculator()
    identity_context = IdentityContextV1.from_snapshot(
        IdentitySnapshotV1(
            1,
            1,
            build_static_self_v1(),
            SelfStateSnapshotV1(12.0, 40.0, 70.0),
            PersonalitySnapshotV1(0, 0, 0, 0, 0),
        )
    )

    respuesta = ActionResponse(
        pensamiento="",
        accion="navegar_a_objeto",
        parametros={"nombre_objeto": "Tree_01"},
        razon="Accion validada por Capa 1.",
        metadata=_metadata_distancia(5.0),
    )

    enriquecida = orquestador._adjuntar_esfuerzo_subjetivo(
        respuesta,
        identity_context=identity_context,
    )

    assert_true(enriquecida.accion == respuesta.accion, "El esfuerzo no debe cambiar la accion.")
    assert_true(enriquecida.parametros == respuesta.parametros, "El esfuerzo no debe cambiar parametros Unity.")
    esfuerzo = enriquecida.metadata.get("subjective_effort")
    assert_true(isinstance(esfuerzo, dict), "Debe adjuntar metadata subjective_effort.")
    assert_true(esfuerzo["physical_validation_impact"] is False, "No debe afectar validacion fisica.")


def test_working_memory_receives_soft_effort_context() -> None:
    memoria = WorkingMemory()
    memoria.agregar(
        "navegar_a_objeto",
        "despachada",
        {"subjective_effort": {"label": "alto", "score": 9.25}},
    )

    resumen = memoria.resumen()
    assert_true("esfuerzo percibido: alto" in resumen, "Memoria de trabajo debe exponer contexto suave.")


def main() -> None:
    print("=== INICIANDO PRUEBA DE SUBJECTIVE EFFORT ===\n")
    test_fatigue_raises_walking_effort_5m()
    test_stress_and_distance_are_monotonic()
    test_orchestrator_attaches_effort_without_changing_action()
    test_working_memory_receives_soft_effort_context()
    print("[OK] test_subjective_effort paso: esfuerzo subjetivo estable y no invasivo.")


if __name__ == "__main__":
    main()
