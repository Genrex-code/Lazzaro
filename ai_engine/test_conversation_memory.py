from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.orchestrator import Orchestrator
from memory.conversation_memory import ConversationMemory


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class FakeMemory:
    def __init__(self):
        self.turnos = []

    def obtener_contexto_memoria(self, **kwargs):
        return "Recuerdo largo relevante."

    def obtener_ultimas_acciones(self):
        return "Ultimas acciones: hablar -> despachada"

    def obtener_contexto_conversacional(self, n: int = 6):
        return "CONTINUIDAD CONVERSACIONAL RECIENTE:\n[10:00] Usuario: te puse el apodo Lazz"

    def registrar_turno_conversacional(self, **kwargs):
        self.turnos.append(kwargs)


def main() -> None:
    print("=== INICIANDO PRUEBA DE MEMORIA CONVERSACIONAL ===\n")

    memoria = ConversationMemory(max_turns=3)
    memoria.registrar_turno(
        usuario="te puse el apodo Lazz",
        lazzaro="Lazz es corto y funcional. Me gusta.",
        route="persona",
        intent="MEMORY_UPDATE",
    )
    resumen = memoria.resumen()
    assert_true("Usuario: te puse el apodo Lazz" in resumen, "Debe conservar texto del usuario.")
    assert_true("Lazzaro: Lazz es corto" in resumen, "Debe conservar texto de Lazzaro.")

    orquestador = object.__new__(Orchestrator)
    orquestador.memory = FakeMemory()

    evento = EventoEntrante(
        origen="unity",
        tipo_evento="comando_usuario",
        id_transaccion="test-conversation-memory",
        detalles={"mensaje": "recuerdas mi apodo?"},
    )

    contexto = orquestador._obtener_contexto_memoria_completa(
        evento=evento,
        narrativa="El usuario pregunta por continuidad.",
        percepcion_resumida="",
        tarea={"objetivo": "recuerdas mi apodo?"},
    )
    assert_true(
        contexto.startswith("CONTINUIDAD CONVERSACIONAL RECIENTE"),
        "La continuidad conversacional debe preceder a otros recuerdos.",
    )
    assert_true(
        "MEMORIA DE TRABAJO OPERATIVA" in contexto,
        "Debe conservar memoria operativa existente.",
    )
    assert_true(
        "RECUERDOS A LARGO PLAZO" in contexto,
        "Debe conservar recuerdos largos existentes.",
    )

    respuesta = ActionResponse(
        pensamiento="El usuario comprueba continuidad.",
        accion="hablar",
        parametros={"texto_a_comunicar": "Claro, te digo Lazz."},
        razon="Responder con continuidad.",
        emocion_facial="Fcl_ALL_Joy",
    )
    accion = AccionSaliente(
        id_accion="accion-test",
        nombre="hablar",
        parametros=respuesta.parametros,
        razonamiento=respuesta.razon,
        pensamiento_privado=respuesta.pensamiento,
        emocion_facial=respuesta.emocion_facial,
    )

    orquestador._registrar_turno_conversacional_si_aplica(
        evento=evento,
        triage_decision={"route": "persona", "intent": "MEMORY_QUERY"},
        action_response=respuesta,
        accion_saliente=accion,
    )
    assert_true(len(orquestador.memory.turnos) == 1, "Debe registrar un turno hablado.")
    turno = orquestador.memory.turnos[0]
    assert_true(turno["mensaje_usuario"] == "recuerdas mi apodo?", "Debe guardar mensaje usuario.")
    assert_true(turno["respuesta_lazzaro"] == "Claro, te digo Lazz.", "Debe guardar respuesta final.")
    assert_true(turno["route"] == "persona", "Debe guardar ruta de Capa 0.")

    accion_none = AccionSaliente(id_accion="accion-none", nombre="none")
    orquestador._registrar_turno_conversacional_si_aplica(
        evento=evento,
        triage_decision={"route": "noop", "intent": "NOOP"},
        action_response=ActionResponse(pensamiento="", accion="none", parametros={}, razon=""),
        accion_saliente=accion_none,
    )
    assert_true(len(orquestador.memory.turnos) == 1, "No debe registrar acciones none.")

    print("[OK] test_conversation_memory paso: continuidad RAM conectada sin Chroma.")


if __name__ == "__main__":
    main()
