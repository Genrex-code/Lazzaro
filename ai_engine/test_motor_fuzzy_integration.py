from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.motor_engine import MotorEngine
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class ReasoningProhibido:
    def process(self, **kwargs):
        raise AssertionError("ReasoningEngine no debe recibir comandos motores resueltos por Capa 1.")


def _orquestador_minimo() -> Orchestrator:
    orquestador = object.__new__(Orchestrator)
    orquestador.motor_engine = MotorEngine()
    orquestador.reasoning = ReasoningProhibido()
    return orquestador


def _resolver_motor(mensaje: str, percepcion: dict, intent: str = "MOTOR_SIMPLE"):
    evento = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion=f"test-{abs(hash(mensaje))}",
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


def test_ready_uses_fuzzy_target_and_legacy_name() -> None:
    percepcion = {
        "snapshot_id": "snap-ready",
        "source": "unity",
        "objetos_normalizados": [
            {
                "id": "Rock_01",
                "display_name": "Rock_01",
                "nombre": "Rock_01",
                "aliases": ["piedra", "roca"],
                "affordances": ["mirar"],
            }
        ],
    }

    respuesta = _resolver_motor("camina a la piedra", percepcion)

    assert_true(respuesta.accion == "navegar_a_objeto", "Debe navegar al objeto resuelto por fuzzy.")
    assert_true(respuesta.parametros["nombre_objeto"] == "Rock_01", "Debe conservar nombre legacy para Unity.")
    assert_true(
        respuesta.metadata["motor_resolution"]["status"] == "ready",
        "Debe preservar metadata de resolucion ready.",
    )


def test_ambiguous_fuzzy_target_asks_clarification() -> None:
    percepcion = {
        "snapshot_id": "snap-ambiguous",
        "source": "unity",
        "objetos_normalizados": [
            {"id": "Rock_01", "display_name": "Rock_01", "nombre": "Rock_01", "aliases": ["roca"], "affordances": ["mirar"]},
            {"id": "Rock_02", "display_name": "Rock_02", "nombre": "Rock_02", "aliases": ["roca"], "affordances": ["mirar"]},
        ],
    }

    respuesta = _resolver_motor("ve a la roca", percepcion)

    assert_true(respuesta.accion == "hablar", "La ambiguedad debe producir aclaracion, no accion fisica.")
    assert_true("Rock_01" in respuesta.parametros["texto_a_comunicar"], "La aclaracion debe listar candidatos.")
    assert_true(
        respuesta.metadata["motor_resolution"]["status"] == "requires_clarification",
        "Debe preservar estado requires_clarification.",
    )


def test_not_allowed_affordance_blocks_action() -> None:
    percepcion = {
        "snapshot_id": "snap-not-allowed",
        "source": "unity",
        "objetos_normalizados": [
            {"id": "Tree_01", "display_name": "Tree_01", "nombre": "Tree_01", "aliases": ["arbol"], "affordances": ["mirar", "da_sombra"]},
        ],
    }

    respuesta = _resolver_motor("agarra el arbol", percepcion, intent="INTERACTION")

    assert_true(respuesta.accion == "hablar", "Accion no permitida debe responder con negativa tecnica.")
    assert_true("no permite" in respuesta.parametros["texto_a_comunicar"], "Debe explicar que la accion no aplica.")
    assert_true(
        respuesta.metadata["motor_resolution"]["status"] == "not_allowed",
        "Debe preservar estado not_allowed.",
    )


def test_legacy_alias_still_resolves_when_metadata_has_no_human_alias() -> None:
    percepcion = {
        "snapshot_id": "snap-legacy",
        "source": "unity",
        "objetos_normalizados": [
            {"id": "Cube", "display_name": "Cube", "nombre": "Cube", "aliases": ["Cube"], "affordances": ["mirar"]},
        ],
        "resumen_percepcion": "Frente a mi veo: Cube a 2.5m al norte.",
    }

    respuesta = _resolver_motor("camina al cubo", percepcion)

    assert_true(respuesta.accion == "navegar_a_objeto", "Alias legacy cubo debe seguir funcionando.")
    assert_true(respuesta.parametros["nombre_objeto"] == "Cube", "Debe resolver cubo a Cube.")


def main() -> None:
    print("=== INICIANDO PRUEBA DE INTEGRACION MOTOR + FUZZY ===\n")
    test_ready_uses_fuzzy_target_and_legacy_name()
    test_ambiguous_fuzzy_target_asks_clarification()
    test_not_allowed_affordance_blocks_action()
    test_legacy_alias_still_resolves_when_metadata_has_no_human_alias()
    print("[OK] test_motor_fuzzy_integration paso: MotorEngine integra fuzzy sin romper legacy.")


if __name__ == "__main__":
    main()
