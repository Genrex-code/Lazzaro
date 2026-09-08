from __future__ import annotations

from datetime import datetime, timedelta
import sys
from pathlib import Path


if __package__ in (None, ""):
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))

from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EventoEntrante
from core.orchestrator import Orchestrator
from memory.conversation_memory import ConversationMemory
from memory.memory_manager import MemoryManager
from memory.memory_types import MemoryMetadata, MemoryResult, SemanticMemory


def assert_true(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise AssertionError(mensaje)


class FakeMemory:
    def __init__(self):
        self.turnos = []

    def obtener_contexto_memoria(self, **kwargs):
        return "Recuerdo largo relevante."

    def obtener_contexto_relacional(self, **kwargs):
        return "MEMORIA RELACIONAL PERSISTENTE:\n- Memoria relacional: el usuario suele llamar a Lazzaro 'lazz'."

    def obtener_ultimas_acciones(self):
        return "Ultimas acciones: hablar -> despachada"

    def obtener_contexto_conversacional(self, n: int = 6):
        return "CONTINUIDAD CONVERSACIONAL RECIENTE:\n[10:00] Usuario: te puse el apodo Lazz"

    def registrar_turno_conversacional(self, **kwargs):
        self.turnos.append(kwargs)


class FakeSemanticStore:
    def __init__(self):
        self.where = None

    def buscar(self, query: str, n: int = 5, where: dict | None = None):
        self.where = where
        ahora = datetime.utcnow()
        return [
            MemoryResult(
                recuerdo=SemanticMemory(
                    id="rel-1",
                    metadata=MemoryMetadata(
                        tipo_evento="memoria_relacional",
                        valencia=0.25,
                        impacto_emocional=0.65,
                        timestamp=ahora - timedelta(minutes=10),
                        extra={
                            "categoria_relacional": "apodo_lazzaro",
                            "valor_relacional": "lazz",
                            "estado_relacional": "activo",
                        },
                    ),
                    regla="Memoria relacional: el usuario suele llamar a Lazzaro 'lazz'.",
                    origen="conversacion_selectiva",
                ),
                distancia=0.1,
            ),
            MemoryResult(
                recuerdo=SemanticMemory(
                    id="rel-2",
                    metadata=MemoryMetadata(
                        tipo_evento="memoria_relacional",
                        valencia=0.25,
                        impacto_emocional=0.65,
                        timestamp=ahora,
                        extra={
                            "categoria_relacional": "apodo_lazzaro",
                            "valor_relacional": "lazz",
                            "estado_relacional": "revocado",
                        },
                    ),
                    regla="Memoria relacional: el usuario pidio no volver a llamar a Lazzaro 'lazz'.",
                    origen="conversacion_selectiva",
                ),
                distancia=0.2,
            )
        ]


class FakeClientConHuella:
    def listar(self, where=None, limite=None):
        if where and where.get("huella_conversacional"):
            return {"ids": ["rel-existente"], "documents": [], "metadatas": []}
        return {"ids": [], "documents": [], "metadatas": []}


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

    memoria.registrar_turno(
        usuario="recuerdas eso?",
        lazzaro="Si, venimos hablando de tu apodo para mi.",
        route="persona",
        intent="MEMORY_QUERY",
    )
    resumen_enriquecido = memoria.resumen()
    assert_true("ULTIMO INTERCAMBIO" in resumen_enriquecido, "Debe destacar el ultimo intercambio.")
    assert_true("TEMAS ACTIVOS" in resumen_enriquecido, "Debe resumir temas activos.")
    assert_true("SENAL DE CONTINUIDAD" in resumen_enriquecido, "Debe detectar referencias al turno anterior.")

    hechos = MemoryManager._extraer_hechos_conversacionales(
        "te puse el apodo Lazz y yo soy tu desarrollador"
    )
    assert_true(
        any("llamar a Lazzaro 'lazz'" in hecho for hecho in hechos),
        "Debe extraer apodos explicitos como memoria relacional.",
    )
    assert_true(
        any("creador" in hecho or "desarrollador" in hecho for hecho in hechos),
        "Debe extraer autoridad relacional explicita.",
    )
    hechos_detallados = MemoryManager._extraer_hechos_relacionales_detallados("ya no te llamare Lazz")
    assert_true(
        any(
            hecho["categoria_relacional"] == "apodo_lazzaro"
            and hecho["estado_relacional"] == "revocado"
            for hecho in hechos_detallados
        ),
        "Debe extraer revocaciones de apodo como hechos relacionales.",
    )

    gestor_memoria = object.__new__(MemoryManager)
    gestor_memoria._hechos_conversacionales_guardados = set()
    reglas_guardadas = []

    def guardar_regla(**kwargs):
        reglas_guardadas.append(kwargs)
        return f"regla-{len(reglas_guardadas)}"

    gestor_memoria.guardar_regla = guardar_regla
    MemoryManager._registrar_hechos_conversacionales(
        gestor_memoria,
        mensaje_usuario="mi nombre es Perro",
        respuesta_lazzaro="Entendido.",
        route="persona",
        intent="MEMORY_UPDATE",
        metadata={"id_transaccion": "fact-1", "accion": "hablar"},
    )
    MemoryManager._registrar_hechos_conversacionales(
        gestor_memoria,
        mensaje_usuario="mi nombre es Perro",
        respuesta_lazzaro="Entendido.",
        route="persona",
        intent="MEMORY_UPDATE",
        metadata={"id_transaccion": "fact-1", "accion": "hablar"},
    )
    assert_true(len(reglas_guardadas) == 1, "Debe persistir hechos relacionales una sola vez.")
    assert_true(
        reglas_guardadas[0]["tipo_evento"] == "memoria_relacional",
        "Debe guardar hechos conversacionales como memoria relacional semantica.",
    )
    assert_true(
        reglas_guardadas[0]["extra"]["categoria_relacional"] == "nombre_usuario",
        "Debe guardar categoria relacional para resolver vigencia.",
    )

    gestor_memoria_reinicio = object.__new__(MemoryManager)
    gestor_memoria_reinicio._hechos_conversacionales_guardados = set()
    gestor_memoria_reinicio.client = FakeClientConHuella()
    reglas_duplicadas = []
    gestor_memoria_reinicio.guardar_regla = lambda **kwargs: reglas_duplicadas.append(kwargs) or "duplicada"
    MemoryManager._registrar_hechos_conversacionales(
        gestor_memoria_reinicio,
        mensaje_usuario="mi nombre es Perro",
        respuesta_lazzaro="Entendido.",
    )
    assert_true(
        not reglas_duplicadas,
        "Debe evitar duplicados persistidos si la huella ya existe en Chroma.",
    )

    gestor_memoria.semantic_store = FakeSemanticStore()
    contexto_relacional = MemoryManager.obtener_contexto_relacional(
        gestor_memoria,
        query="recuerdas mi apodo?",
    )
    assert_true(
        contexto_relacional.startswith("MEMORIA RELACIONAL PERSISTENTE"),
        "Debe exponer hechos relacionales persistentes en bloque propio.",
    )
    assert_true(
        gestor_memoria.semantic_store.where == {"tipo_evento": "memoria_relacional"},
        "Debe filtrar la busqueda semantica a memoria_relacional.",
    )
    assert_true(
        "no volver a llamar" in contexto_relacional,
        "Debe priorizar la correccion relacional mas reciente.",
    )
    assert_true(
        "suele llamar" not in contexto_relacional,
        "Debe ocultar el hecho relacional antiguo cuando hay correccion vigente.",
    )

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
        "MEMORIA RELACIONAL PERSISTENTE" in contexto,
        "Debe inyectar hechos relacionales persistentes.",
    )
    assert_true(
        contexto.index("MEMORIA RELACIONAL PERSISTENTE") < contexto.index("MEMORIA DE TRABAJO OPERATIVA"),
        "La memoria relacional debe ir antes de la memoria operativa.",
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
    )
    accion = AccionSaliente(
        id_accion="accion-test",
        nombre="hablar",
        parametros=respuesta.parametros,
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
