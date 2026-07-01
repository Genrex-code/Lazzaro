from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.motor_engine import MotorEngine
from api.schemas import EventoEntrante
from core.orchestrator import Orchestrator


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class ReasoningProhibido:
    def process(self, **kwargs):
        raise AssertionError("ReasoningEngine no debe recibir comandos motores incompletos.")


def _orquestador_minimo() -> Orchestrator:
    orquestador = object.__new__(Orchestrator)
    orquestador.motor_engine = MotorEngine()
    orquestador.reasoning = ReasoningProhibido()
    return orquestador


def main() -> None:
    print("=== INICIANDO PRUEBA DE GUARDARRAILES MOTOR ===\n")

    percepcion = {
        "resumen_percepcion": "Frente a mi veo: Cube a 2.5m al norte y Sphere a 4m al este."
    }
    herramientas = [
        "navegar_a_objeto",
        "interactuar_objeto_3d",
        "alejarse_de_objeto",
        "girar_avatar",
        "hablar",
    ]
    motor = MotorEngine()

    diagnostico_claro = motor.diagnosticar_comando_usuario(
        "camina al cubo",
        percepcion=percepcion,
        herramientas_disponibles=herramientas,
    )
    assert_true(diagnostico_claro["estado"] == "lista", "Un comando claro debe ser ejecutable.")
    assert_true(
        diagnostico_claro["parametros"]["nombre_objeto"] == "Cube",
        "El motor debe resolver cubo al objeto visible Cube.",
    )

    diagnostico_incompleto = motor.diagnosticar_comando_usuario(
        "camina hacia el",
        percepcion=percepcion,
        herramientas_disponibles=herramientas,
    )
    assert_true(
        diagnostico_incompleto["estado"] == "falta_objetivo",
        "Un comando motor sin objeto debe pedir aclaracion.",
    )

    diagnostico_giro = motor.diagnosticar_comando_usuario(
        "gira 180 grados",
        percepcion=percepcion,
        herramientas_disponibles=herramientas,
    )
    assert_true(diagnostico_giro["estado"] == "lista", "Un giro con grados debe ser ejecutable.")
    assert_true(diagnostico_giro["accion"] == "girar_avatar", "El giro debe usar girar_avatar.")
    assert_true(
        diagnostico_giro["parametros"]["grados"] == 180.0,
        "El motor debe conservar los grados solicitados.",
    )

    diagnostico_derecha = motor.diagnosticar_comando_usuario(
        "gira hacia la derecha",
        percepcion=percepcion,
        herramientas_disponibles=herramientas,
    )
    assert_true(
        diagnostico_derecha["parametros"]["grados"] == 90.0,
        "Girar a la derecha debe traducirse a 90 grados.",
    )

    percepcion_bosque = {"objetos_visibles": [{"nombre": "Flores"}]}
    diagnostico_bosque = motor.diagnosticar_comando_usuario(
        "camina a las flores",
        percepcion=percepcion_bosque,
        herramientas_disponibles=herramientas,
    )
    assert_true(diagnostico_bosque["estado"] == "lista", "El diccionario del bosque debe resolver flores.")
    assert_true(
        diagnostico_bosque["parametros"]["nombre_objeto"] == "Flores",
        "El motor debe resolver flores al objeto visible Flores.",
    )

    orquestador = _orquestador_minimo()
    evento_incompleto = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="test-motor-incompleto",
        detalles={"mensaje": "camina", **percepcion},
    )
    respuesta_incompleta = orquestador._resolver_ruta_motor(
        evento=evento_incompleto,
        contexto_llm={},
        memoria_texto="",
        herramientas=herramientas,
        herramientas_detalle={},
        politica="exploradora",
        tarea=None,
    )
    assert_true(respuesta_incompleta.accion == "hablar", "Motor incompleto debe responder hablando.")
    assert_true(
        "objeto" in respuesta_incompleta.parametros["texto_a_comunicar"].lower(),
        "La aclaracion debe pedir el objeto faltante.",
    )

    evento_claro = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="test-motor-claro",
        detalles={"mensaje": "camina al cubo", **percepcion},
    )
    respuesta_clara = orquestador._resolver_ruta_motor(
        evento=evento_claro,
        contexto_llm={},
        memoria_texto="",
        herramientas=herramientas,
        herramientas_detalle={},
        politica="exploradora",
        tarea=None,
    )
    assert_true(respuesta_clara.accion == "navegar_a_objeto", "Motor claro debe navegar.")
    assert_true(
        respuesta_clara.parametros["nombre_objeto"] == "Cube",
        "La accion clara debe conservar el objeto resuelto.",
    )

    evento_giro = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="test-motor-giro",
        detalles={"mensaje": "gira 180 grados", **percepcion},
    )
    respuesta_giro = orquestador._resolver_ruta_motor(
        evento=evento_giro,
        contexto_llm={},
        memoria_texto="",
        herramientas=herramientas,
        herramientas_detalle={},
        politica="exploradora",
        tarea=None,
    )
    assert_true(respuesta_giro.accion == "girar_avatar", "Motor debe emitir accion de giro.")
    assert_true(
        respuesta_giro.parametros["grados"] == 180.0,
        "La accion de giro debe conservar grados.",
    )

    print("[OK] test_motor_guardrails paso: comandos incompletos no llegan al LLM viejo.")


if __name__ == "__main__":
    main()
