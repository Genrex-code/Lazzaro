"""
core/orchestrator.py - Orquestador central de LAZZARO.
Coordina el flujo completo: evento -> estado interno -> memoria -> IA -> accion.
No contiene logica de dominio; solo delega en los modulos especializados.
"""

from collections import deque
from datetime import datetime
from typing import Dict, List, Optional
import uuid

from ai_engine.schemas import ActionResponse
from api.schemas import AccionSaliente, EstadoAgente, EventoEntrante, NotificacionSaliente
from core.logger import get_logger
from tools.executor import ActionExecutor

logger = get_logger(__name__)


class Orchestrator:
    """
    Punto central que une BehaviorCore, MemoryManager, ReasoningEngine y ToolRegistry.
    Expone metodos para procesar eventos y obtener el estado del agente.
    """

    def __init__(self, behavior_core, memory_manager, reasoning_engine, tool_registry):
        self.behavior = behavior_core
        self.memory = memory_manager
        self.reasoning = reasoning_engine
        self.tool_registry = tool_registry
        self._transacciones_procesadas: Dict[str, AccionSaliente] = {}
        self._notificaciones: deque[NotificacionSaliente] = deque(maxlen=50)
        logger.info("Orchestrator inicializado.")

    def procesar_evento(self, evento: EventoEntrante) -> Optional[AccionSaliente]:
        """
        Procesa un evento externo y devuelve la accion que el agente decide ejecutar.
        Si el evento ya fue procesado, devuelve la accion almacenada.
        """
        if evento.id_transaccion in self._transacciones_procesadas:
            logger.info("Evento %s ya procesado, devolviendo accion cacheada.", evento.id_transaccion)
            return self._transacciones_procesadas[evento.id_transaccion]

        try:
            respuesta_core = self.behavior.procesar_evento(
                tipo_evento=evento.tipo_evento,
                detalles=evento.detalles if evento.detalles else {},
            )
        except Exception as e:
            logger.error("Error en BehaviorCore al procesar evento: %s", e)
            return None

        estado_actual = respuesta_core.get("estado", {})
        narrativa = respuesta_core.get("narrativa", "")
        contexto_llm = respuesta_core.get("contexto_llm", {})
        tarea = respuesta_core.get("tarea")
        politica = respuesta_core.get("policy_activa", "exploradora")
        # --- PARCHE DE MISIONES DESDE UNITY ---
        # Si el core no generó una tarea formal, pero Unity mandó un objetivo, lo inyectamos a la fuerza
        if not tarea and evento.detalles and "mensaje" in evento.detalles:
            mensaje_unity = str(evento.detalles["mensaje"])
            if "objetivo" in mensaje_unity.lower():
                tarea = {
                    "objetivo": mensaje_unity,
                    "intentos": 0,
                    "fallos": 0,
                    "frustracion": 0.0,
                    "umbral_abandono": 80.0,
                    "loop_detectado": False,
                    "estancamiento": False
                }
        # --------------------------------------

        memoria_texto = ""
        try:
            memoria_texto = self.memory.obtener_contexto_memoria(
                query=narrativa or evento.tipo_evento,
                limite=5,
                max_caracteres=600,
            )
            self.memory.guardar_experiencia(
                tipo_evento=evento.tipo_evento,
                narrativa=narrativa,
                estado_emocional=estado_actual,
                valencia=0.0,
                impacto_emocional=0.5,
                extra={
                    "origen": evento.origen,
                    "objeto": evento.objeto_involucrado,
                },
            )
        except Exception as e:
            logger.error("Error en MemoryManager: %s", e)

        herramientas = self.tool_registry.listar_nombres() if self.tool_registry else []

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

        action_response = self._sanitizar_action_response(action_response)
        accion_saliente = self._a_accion_saliente(action_response, politica)

        resultado_memoria = "sin_accion"
        detalles_resultado = None
        if accion_saliente.nombre != "none":
            if accion_saliente.requiere_confirmacion:
                resultado_memoria = "pendiente_confirmacion"
                detalles_resultado = {
                    "mensaje": "La accion requiere confirmacion antes de ejecutarse.",
                    "error": None,
                    "datos_extra": None,
                }
            else:
                resultado_ejecucion = ActionExecutor.ejecutar(accion_saliente)
                resultado_memoria = "ok" if resultado_ejecucion.exito else "error"
                detalles_resultado = {
                    "mensaje": resultado_ejecucion.mensaje,
                    "error": resultado_ejecucion.error,
                    "datos_extra": resultado_ejecucion.datos_extra,
                }

        self.memory.agregar_accion(
            accion=accion_saliente.nombre,
            resultado=resultado_memoria,
            detalles=detalles_resultado,
        )

        self._transacciones_procesadas[evento.id_transaccion] = accion_saliente
        self._agregar_notificacion_si_corresponde(action_response)

        logger.info("Evento %s procesado. Accion: %s", evento.id_transaccion, accion_saliente.nombre)
        return accion_saliente

    def obtener_estado_publico(self) -> EstadoAgente:
        """Devuelve un snapshot del estado interno para el frontend."""
        resumen = self.behavior.obtener_resumen()
        estado = resumen.get("estado", {})
        personalidad = resumen.get("personalidad", {})
        tarea = resumen.get("tarea")

        return EstadoAgente(
            dopamina=estado.get("confianza", 50),
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

    def _sanitizar_action_response(self, action_response: ActionResponse) -> ActionResponse:
        """
        Convierte respuestas invalidas o herramientas inexistentes en un no-op seguro.
        Conserva el error para que el frontend pueda observarlo via notificaciones.
        """
        if not action_response.valido:
            logger.warning(
                "Respuesta del modelo invalida; se degradara a no-op. Error: %s",
                action_response.error,
            )
            return ActionResponse(
                pensamiento=action_response.pensamiento,
                accion="none",
                parametros={},
                razon=action_response.razon,
                valido=False,
                error=action_response.error or "Respuesta invalida del modelo.",
            )

        if (
            action_response.accion != "none"
            and self.tool_registry
            and self.tool_registry.get_tool(action_response.accion) is None
        ):
            error = f"Herramienta no registrada: {action_response.accion}"
            logger.warning("%s. Se degradara a no-op.", error)
            return ActionResponse(
                pensamiento=action_response.pensamiento,
                accion="none",
                parametros={},
                razon=action_response.razon,
                valido=False,
                error=error,
            )

        return action_response

    def _a_accion_saliente(self, action_response: ActionResponse, politica: str) -> AccionSaliente:
        """Convierte la ActionResponse interna en el modelo de API."""
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
        )

    def _agregar_notificacion_si_corresponde(self, action_response: ActionResponse):
        """Si el agente quiere comunicar algo espontaneo, lo encola."""
        if action_response.accion == "none" and action_response.pensamiento:
            notif = NotificacionSaliente(
                tipo="pensamiento",
                mensaje=action_response.pensamiento,
                timestamp=datetime.utcnow(),
            )
            self._notificaciones.append(notif)

        if action_response.error:
            notif = NotificacionSaliente(
                tipo="error",
                mensaje=f"Error de razonamiento: {action_response.error}",
                timestamp=datetime.utcnow(),
            )
            self._notificaciones.append(notif)
