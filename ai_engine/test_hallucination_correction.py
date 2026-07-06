from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.fuzzy_object_resolver import FuzzyObjectResolver
from ai_engine.motor_engine import MotorEngine
from ai_engine.perception_models import PerceivedObject, PerceptionSnapshot
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class ReasoningProhibido:
    def process(self, **kwargs):
        raise AssertionError("ReasoningEngine no debe recibir correcciones resueltas por Capa 1.")


def _snapshot(*objects: PerceivedObject) -> PerceptionSnapshot:
    return PerceptionSnapshot(snapshot_id="hallucination-test", objects=list(objects), source="unity")


def _orquestador_minimo() -> Orchestrator:
    orquestador = object.__new__(Orchestrator)
    orquestador.motor_engine = MotorEngine()
    orquestador.reasoning = ReasoningProhibido()
    return orquestador


def _resolver_motor(mensaje: str, percepcion: dict, intent: str = "MOTOR_SIMPLE"):
    evento = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=f"hallucination-{abs(hash(mensaje))}",
        detalles={"mensaje": mensaje, **percepcion},
    )
    return _orquestador_minimo()._resolver_ruta_motor(
        evento=evento,
        contexto_llm={},
        memoria_texto="",
        herramientas=[
            "navegar_a_objeto",
            "interactuar_objeto_3d",
            "agarrar_objeto",
            "alejarse_de_objeto",
            "girar_avatar",
            "hablar",
        ],
        herramientas_detalle={},
        politica="exploradora",
        tarea=None,
        triage_decision={"intent": intent, "route": "motor"},
    )


def test_arbol_grande_suggests_tree_without_executing() -> None:
    resolver = FuzzyObjectResolver()
    result = resolver.resolve(
        "camina al arbol grande",
        _snapshot(
            PerceivedObject(
                id="Tree_01",
                display_name="Tree_01",
                aliases=["arbol"],
                distance=3.0,
                affordances=["mirar", "da_sombra"],
            )
        ),
        intent="MOTOR_SIMPLE",
        action="navegar_a_objeto",
    )

    assert_true(result.status == "requires_clarification", "Una propiedad no percibida debe pedir confirmacion.")
    assert_true(result.target_id == "Tree_01", "Debe conservar el candidato plausible.")
    assert_true(result.candidate_correction is not None, "Debe exponer CandidateCorrection.")
    assert_true(result.candidate_correction.suggested_id == "Tree_01", "Debe sugerir Tree_01.")
    assert_true(result.candidate_correction.requires_confirmation, "La correccion siempre requiere confirmacion.")
    assert_true("grande" in result.candidates[0].unmatched_descriptors, "Debe auditar descriptor inventado.")
    assert_true(result.audit["status_reason"] == "candidate_correction", "Debe auditar regla de correccion.")


def test_piedra_chica_core_asks_confirmation_for_rock_02() -> None:
    percepcion = {
        "snapshot_id": "snap-correction-rock",
        "source": "unity",
        "objetos_normalizados": [
            {
                "id": "Rock_02",
                "display_name": "Rock_02",
                "nombre": "Rock_02",
                "aliases": ["piedra"],
                "distance": 4.2,
                "affordances": ["mirar"],
            }
        ],
    }

    respuesta = _resolver_motor("camina a la piedra chica", percepcion)

    texto = respuesta.parametros["texto_a_comunicar"]
    assert_true(respuesta.accion == "hablar", "La correccion debe hablar, no ejecutar navegacion.")
    assert_true("Quisiste decir Rock_02 a 4.2m?" in texto, "Debe pedir confirmacion con candidato y distancia.")
    assert_true("nombre_objeto" not in respuesta.parametros, "No debe mandar parametros fisicos a Unity.")
    assert_true(
        respuesta.metadata["motor_resolution"]["candidate_correction"]["suggested_id"] == "Rock_02",
        "Debe preservar candidate_correction en metadata.",
    )


def test_correction_does_not_override_missing_affordance() -> None:
    percepcion = {
        "snapshot_id": "snap-correction-affordance",
        "source": "unity",
        "objetos_normalizados": [
            {
                "id": "Tree_01",
                "display_name": "Tree_01",
                "nombre": "Tree_01",
                "aliases": ["arbol"],
                "distance": 3.0,
                "affordances": ["mirar", "da_sombra"],
            }
        ],
    }

    respuesta = _resolver_motor("agarra el arbol grande", percepcion, intent="INTERACTION")

    assert_true(respuesta.accion == "hablar", "La affordance faltante debe bloquear con respuesta tecnica.")
    assert_true(
        respuesta.metadata["motor_resolution"]["status"] == "not_allowed",
        "Debe priorizar not_allowed sobre correccion candidata.",
    )
    assert_true(
        respuesta.metadata["motor_resolution"]["candidate_correction"] is None,
        "No debe sugerir correccion cuando la accion ya esta bloqueada por affordance.",
    )


def main() -> None:
    print("=== INICIANDO PRUEBA DE AUTO-CORRECCION DE ALUCINACIONES ===\n")
    test_arbol_grande_suggests_tree_without_executing()
    test_piedra_chica_core_asks_confirmation_for_rock_02()
    test_correction_does_not_override_missing_affordance()
    print("[OK] test_hallucination_correction paso: correcciones seguras sin ejecucion fisica.")


if __name__ == "__main__":
    main()
