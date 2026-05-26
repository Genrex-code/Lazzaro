"""
core/orchestrator.py - Orquestador central de LAZZARO.
Coordina el flujo completo: evento → estado interno → memoria → IA → acción.
No contiene lógica de dominio, solo delega en los módulos especializados.
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from collections import deque
import uuid

from api.schemas import (
    EventoEntrante,
    AccionSaliente,
    EstadoAgente,
    NotificacionSaliente,
)
from ai_engine.schemas import ActionResponse
from core.logger import get_logger
from tools.executor import ActionExecutor

logger = get_logger(__name__)


class Orchestrator:
    """
    Punto central que une BehaviorCore, MemoryManager, ReasoningEngine y ToolRegistry.
    Expone métodos para procesar eventos y obtener el estado del agente.
    """

    def __init__(self, behavior_core, memory_manager, reasoning_engine, tool_registry):
        """
        Args:
            behavior_core: instancia de BehaviorCore.
            memory_manager: instancia de MemoryManager.
            reasoning_engine: instancia de ReasoningEngine (ya con su cliente IA).
            tool_registry: instancia de ToolRegistry (tools/registry.py).
        """
        self.behavior = behavior_core
        self.memory = memory_manager
        self.reasoning = reasoning_engine
        self.tool_registry = tool_registry

        # Registro de transacciones ya procesadas (idempotencia)
        self._transacciones_procesadas: Dict[str, AccionSaliente] = {}

        # Cola de notificaciones espontáneas (para el endpoint GET /notificaciones)
        self._notificaciones: deque[NotificacionSaliente] = deque(maxlen=50)

        logger.info("Orchestrator inicializado.")

    # ------------------------------------------------------------------
    # Flujo principal de procesamiento de eventos
    # ------------------------------------------------------------------

    def procesar_evento(self, evento: EventoEntrante) -> Optional[AccionSaliente]:
        """
        Procesa un evento externo y devuelve la acción que el agente decide ejecutar.
        Si el evento ya fue procesado (mismo id_transaccion), devuelve la acción almacenada.
        Si no se requiere acción, devuelve None.
        """
        # 0. Idempotencia
        if evento.id_transaccion in self._transacciones_procesadas:
            logger.info("Evento %s ya procesado, devolviendo acción cacheada.", evento.id_transaccion)
            return self._transacciones_procesadas[evento.id_transaccion]

        try:
            # 1. Actualizar el estado interno (emociones, personalidad, task_context)
            respuesta_core = self.behavior.procesar_evento(
                tipo_evento=evento.tipo_evento,
                detalles=evento.detalles if evento.detalles else {},
            )
        except Exception as e:
            logger.error("Error en BehaviorCore al procesar evento: %s", e)
            return None

        # Extraer datos del resultado
        estado_actual = respuesta_core.get("estado", {})
        narrativa = respuesta_core.get("narrativa", "")
        contexto_llm = respuesta_core.get("contexto_llm", {})
        tarea = respuesta_core.get("tarea")
        politica = respuesta_core.get("policy_activa", "exploradora")

        # 2. Recuperar recuerdos relevantes y guardar la experiencia
        memoria_texto = ""
        try:
            memoria_texto = self.memory.obtener_contexto_memoria(
                query=narrativa or evento.tipo_evento,
                limite=5,
                max_caracteres=600,
            )
            # Guardar experiencia actual en memoria episódica
            self.memory.guardar_experiencia(
                tipo_evento=evento.tipo_evento,
                narrativa=narrativa,
                estado_emocional=estado_actual,
                valencia=0.0,  # podríamos calcularlo más adelante
                impacto_emocional=0.5,
                extra={
                    "origen": evento.origen,
                    "objeto": evento.objeto_involucrado,
                },
            )
        except Exception as e:
            logger.error("Error en MemoryManager: %s", e)
            # Si falla la memoria, continuamos sin recuerdos

        # 3. Obtener herramientas disponibles filtradas por política y tarea
        herramientas = self.tool_registry.listar_nombres() if self.tool_registry else []
        # ToolRouter se invoca dentro de ReasoningEngine, así que pasamos la lista completa

        # 4. Razón con IA
        try:
            action_response = self.reasoning.process(
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto,
                herramientas=herramientas,
                politica=politica,
                tarea=tarea,
            )
        except Exception as e:
            logger.error("Error en ReasoningEngine: %s", e)
            action_response = ActionResponse(
                pensamiento="Error al razonar.",
                accion="none",
                parametros={},
                razon="",
                valido=False,
                error=str(e),
            )

        # 5. Convertir ActionResponse a AccionSaliente (modelo de API)
        accion_saliente = self._a_accion_saliente(action_response, politica)

        # 6. Ejecutar la acción final si aplica y registrar el resultado
        resultado_memoria = "sin_accion"
        detalles_resultado = None
        if accion_saliente.nombre != "none":
            resultado_ejecucion = ActionExecutor.ejecutar(accion_saliente)
            resultado_memoria = "ok" if resultado_ejecucion.exito else "error"
            detalles_resultado = {
                "mensaje": resultado_ejecucion.mensaje,
                "error": resultado_ejecucion.error,
                "datos_extra": resultado_ejecucion.datos_extra,
            }

        self.memory.agregar_accion(
            accion=action_response.accion,
            resultado=resultado_memoria,
            detalles=detalles_resultado,
        )

        # 7. Cache de idempotencia
        self._transacciones_procesadas[evento.id_transaccion] = accion_saliente

        # 8. Si el agente quiere comunicar algo espontáneo, encolar notificación
        self._agregar_notificacion_si_corresponde(action_response)

        logger.info("Evento %s procesado. Acción: %s", evento.id_transaccion, accion_saliente.nombre)
        return accion_saliente

    # ------------------------------------------------------------------
    # Métodos auxiliares para la API
    # ------------------------------------------------------------------

    def obtener_estado_publico(self) -> EstadoAgente:
        """Devuelve un snapshot del estado interno para el frontend."""
        resumen = self.behavior.obtener_resumen()
        estado = resumen.get("estado", {})
        personalidad = resumen.get("personalidad", {})
        tarea = resumen.get("tarea")

        return EstadoAgente(
            dopamina=estado.get("confianza", 50),  # adaptamos nombres si es necesario
            estres=estado.get("estres", 0),
            energia=estado.get("energia", 100),
            confianza=estado.get("confianza", 0),
            motivacion=estado.get("motivacion", 0),
            foco=estado.get("foco", 70),
            personalidad=personalidad,
            tarea_actual=tarea.get("objetivo_id") if tarea else None,
        )

    def obtener_notificaciones(self) -> List[NotificacionSaliente]:
        """Devuelve y limpia las notificaciones pendientes."""
        notificaciones = list(self._notificaciones)
        self._notificaciones.clear()
        return notificaciones

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    def _a_accion_saliente(self, action_response: ActionResponse, politica: str) -> AccionSaliente:
        """Convierte la ActionResponse interna en el modelo de API."""
        # Determinar si requiere confirmación usando la metadata real de la herramienta
        requiere_confirmacion = False
        tool_info = self.tool_registry.get_tool(action_response.accion) if self.tool_registry else None
        if tool_info and tool_info.get("requires_confirmation") and politica != "autonoma":
            requiere_confirmacion = True

        return AccionSaliente(
            id_accion=str(uuid.uuid4()),
            nombre=action_response.accion,
            parametros=action_response.parametros,
            razonamiento=action_response.razon,
            pensamiento_privado=action_response.pensamiento,
            requiere_confirmacion=requiere_confirmacion,
            # No rellenamos parametros_descripcion por ahora
        )

    def _agregar_notificacion_si_corresponde(self, action_response: ActionResponse):
        """Si el agente quiere comunicar algo espontáneo, lo encola."""
        # Si la acción es 'none' pero hay un pensamiento relevante, lo notificamos
        if action_response.accion == "none" and action_response.pensamiento:
            notif = NotificacionSaliente(
                tipo="pensamiento",
                mensaje=action_response.pensamiento,
                timestamp=datetime.utcnow(),
            )
            self._notificaciones.append(notif)
        # Si hay un error, lo notificamos
        if action_response.error:
            notif = NotificacionSaliente(
                tipo="error",
                mensaje=f"Error de razonamiento: {action_response.error}",
                timestamp=datetime.utcnow(),
            )
            self._notificaciones.append(notif)
