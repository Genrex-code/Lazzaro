"""
core/orchestrator.py - Orquestador central de LAZZARO.
Coordina el flujo completo: evento -> estado interno -> memoria -> IA -> accion.
"""

from collections import OrderedDict, deque
from datetime import datetime
from typing import List, Optional
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

    HERRAMIENTAS_POR_ORIGEN = {
        "unity": {
            "navegar_a_objeto", #agregado
            "navegar_a_posicion", # agregado
            "moverse_entorno_3d",
            "interactuar_objeto_3d",
            "agarrar_objeto",
            "soltar_objeto",
            "aplicar_fuerza",
            "hablar",
        },
        "roblox": {
            "navegar_a_objeto",#agregado
            "navegar_a_posicion",#agregado
            "moverse_entorno_3d",
            "interactuar_objeto_3d",
            "agarrar_objeto",
            "soltar_objeto",
            "aplicar_fuerza",
            "hablar",
        },
    }
    MAX_TRANSACCIONES_CACHE = 2000

    def __init__(self, behavior_core, memory_manager, reasoning_engine, tool_registry):
        self.behavior = behavior_core
        self.memory = memory_manager
        self.reasoning = reasoning_engine
        self.tool_registry = tool_registry
        self._transacciones_procesadas: OrderedDict[str, AccionSaliente] = OrderedDict()
        self._notificaciones: deque[NotificacionSaliente] = deque(maxlen=50)
        logger.info("Orchestrator inicializado.")

    def procesar_evento(self, evento: EventoEntrante) -> Optional[AccionSaliente]:
        """
        Procesa un evento externo y devuelve la accion que el agente decide ejecutar.
        Si el evento ya fue procesado, devuelve la accion almacenada.
        """
        cacheada = self._obtener_transaccion_cacheada(evento.id_transaccion)
        if cacheada is not None:
            logger.info("Evento %s ya procesado, devolviendo accion cacheada.", evento.id_transaccion)
            return cacheada

        try:
            respuesta_core = self.behavior.procesar_evento(
                tipo_evento=evento.tipo_evento,
                detalles=evento.detalles if evento.detalles else {},
            )
        except Exception as e:
            logger.error("Error en BehaviorCore al procesar evento: %s", e, exc_info=True)
            return None

        estado_actual = respuesta_core.get("estado", {})
        narrativa = respuesta_core.get("narrativa", "")
        contexto_llm = respuesta_core.get("contexto_llm", {})
        tarea = respuesta_core.get("tarea")
        politica = respuesta_core.get("policy_activa", "exploradora")
        es_interrupcion_critica = bool(respuesta_core.get("es_interrupcion_critica"))
        tipo_memoria = str(respuesta_core.get("tipo_memoria", evento.tipo_evento))
        percepcion_resumida = str(respuesta_core.get("percepcion_resumida", ""))

        memoria_texto = ""
        try:
            query_memoria = self._construir_query_memoria(evento, narrativa, percepcion_resumida, tarea)
            memoria_texto = self.memory.obtener_contexto_memoria(
                query=query_memoria,
                limite=5,
                max_caracteres=600,
            )
            self.memory.guardar_experiencia(
                tipo_evento=tipo_memoria,
                narrativa=self._construir_narrativa_memoria(narrativa, percepcion_resumida, evento),
                estado_emocional=estado_actual,
                valencia=0.0,
                impacto_emocional=self._calcular_impacto_memoria(evento, es_interrupcion_critica),
                extra={
                    "origen": evento.origen,
                    "objeto": evento.objeto_involucrado,
                    "estado_fisico": evento.detalles.get("estado_fisico"),
                    "accion": evento.detalles.get("accion"),
                    "exito": evento.detalles.get("exito"),
                },
            )
        except Exception as e:
            logger.error("Error en MemoryManager: %s", e, exc_info=True)

        herramientas = self._herramientas_para_origen(evento.origen)
        herramientas_detalle = (
            self.tool_registry.describir_herramientas(herramientas) if self.tool_registry else {}
        )

        if es_interrupcion_critica:
            action_response = self._resolver_interrupcion_critica(evento)
        else:
            try:
                action_response = self.reasoning.process(
                    contexto_llm=contexto_llm,
                    memoria_texto=memoria_texto,
                    herramientas=herramientas,
                    herramientas_detalle=herramientas_detalle,
                    politica=politica,
                    tarea=tarea,
                )
            except Exception as e:
                logger.error("Error en ReasoningEngine: %s", e, exc_info=True)
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

        resultado_memoria, detalles_resultado = self._gestionar_ejecucion(evento, accion_saliente)
        self.memory.agregar_accion(
            accion=accion_saliente.nombre,
            resultado=resultado_memoria,
            detalles=detalles_resultado,
        )

        self._cachear_transaccion(evento.id_transaccion, accion_saliente)
        self._agregar_notificacion_si_corresponde(action_response)

        logger.info("Evento %s procesado. Accion: %s", evento.id_transaccion, accion_saliente.nombre)
        return accion_saliente

    def obtener_estado_publico(self) -> EstadoAgente:
        """Devuelve un snapshot del estado interno para el frontend."""
        resumen = self.behavior.obtener_resumen()
        estado = resumen.get("estado", {})
        personalidad = resumen.get("personalidad", {})
        tarea = resumen.get("tarea")

        objetivo = None
        if tarea:
            objetivo = tarea.get("objetivo") or tarea.get("objetivo_id")

        return EstadoAgente(
            dopamina=estado.get("confianza", 50),
            estres=estado.get("estres", 0),
            energia=estado.get("energia", 100),
            confianza=estado.get("confianza", 0),
            motivacion=estado.get("motivacion", 0),
            foco=estado.get("foco", 70),
            personalidad=personalidad,
            tarea_actual=objetivo,
        )

    def obtener_notificaciones(self) -> List[NotificacionSaliente]:
        """Devuelve y limpia las notificaciones pendientes."""
        notificaciones = list(self._notificaciones)
        self._notificaciones.clear()
        return notificaciones

    def obtener_constelacion_publica(self) -> dict:
        """Expone la constelacion cognitiva actual para depuracion o visualizacion."""
        return {
            "constelacion": self.behavior.obtener_constelacion(),
            "resumen": self.behavior.obtener_constelacion_resumen(),
        }

    def _resolver_interrupcion_critica(self, evento: EventoEntrante) -> ActionResponse:
        """Genera una accion inmediata y segura ante dolor fisico o colision fuerte."""
        direccion_escape = str(evento.detalles.get("direccion_escape", "atras"))
        if direccion_escape not in {"adelante", "atras", "izquierda", "derecha"}:
            direccion_escape = "atras"

        distancia = evento.detalles.get("distancia_escape", 1.5)
        velocidad = evento.detalles.get("velocidad_escape", "caminar")
        pensamiento = (
            "Se detecto dolor fisico o colision. Debo interrumpir la tarea actual, "
            "detener el avance y corregir la trayectoria de inmediato."
        )
        razon = (
            "Interrupcion critica por seguridad fisica. Se prioriza retroceder o desviarse "
            "antes de continuar con cualquier objetivo."
        )

        posicion_actual = self._leer_posicion_lazzaro(evento.detalles)
        if self.tool_registry and self.tool_registry.get_tool("navegar_a_posicion") and posicion_actual:
            x, y, z = posicion_actual
            dx, dz = self._vector_escape(direccion_escape)
            distancia_segura = self._leer_distancia_escape(distancia)
            return ActionResponse(
                pensamiento=pensamiento,
                accion="navegar_a_posicion",
                parametros={
                    "x": round(x + dx * distancia_segura, 2),
                    "y": round(y, 2),
                    "z": round(z + dz * distancia_segura, 2),
                },
                razon=razon,
                valido=True,
                emocion_facial="Fcl_ALL_Sorrow",
            )

        if self.tool_registry and self.tool_registry.get_tool("moverse_entorno_3d"):
            return ActionResponse(
                pensamiento=pensamiento,
                accion="moverse_entorno_3d",
                parametros={
                    "direccion": direccion_escape,
                    "distancia": distancia,
                    "velocidad": velocidad,
                },
                razon=razon,
                valido=True,
                emocion_facial="Fcl_ALL_Sorrow",
            )

        return ActionResponse(
            pensamiento=pensamiento,
            accion="none",
            parametros={},
            razon=razon,
            valido=False,
            error="No hay herramienta de movimiento disponible para reaccionar al dolor fisico.",
            emocion_facial="Fcl_ALL_Sorrow",
        )

    def _sanitizar_action_response(self, action_response: ActionResponse) -> ActionResponse:
        """
        Convierte respuestas invalidas o herramientas inexistentes en un no-op seguro.
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
                emocion_facial=action_response.emocion_facial,
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
                emocion_facial=action_response.emocion_facial,
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
            emocion_facial=action_response.emocion_facial,
        )

    @staticmethod
    def _leer_posicion_lazzaro(detalles: dict) -> tuple[float, float, float] | None:
        posicion = detalles.get("posicion_lazzaro")
        if not isinstance(posicion, dict):
            return None
        try:
            return (
                float(posicion.get("x", 0.0)),
                float(posicion.get("y", 0.0)),
                float(posicion.get("z", 0.0)),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _leer_distancia_escape(valor: object) -> float:
        try:
            distancia = float(valor)
        except (TypeError, ValueError):
            distancia = 1.5
        return max(0.5, min(5.0, distancia))

    @staticmethod
    def _vector_escape(direccion_escape: str) -> tuple[float, float]:
        vectores = {
            "adelante": (0.0, 1.0),
            "atras": (0.0, -1.0),
            "izquierda": (-1.0, 0.0),
            "derecha": (1.0, 0.0),
        }
        return vectores.get(direccion_escape, (0.0, -1.0))

    def _agregar_notificacion_si_corresponde(self, action_response: ActionResponse):
        """Si el agente quiere comunicar algo espontaneo, lo encola."""
        if action_response.accion == "none" and action_response.pensamiento:
            self._notificaciones.append(
                NotificacionSaliente(
                    tipo="pensamiento",
                    mensaje=action_response.pensamiento,
                    timestamp=datetime.utcnow(),
                )
            )

        if action_response.error:
            self._notificaciones.append(
                NotificacionSaliente(
                    tipo="error",
                    mensaje=f"Error de razonamiento: {action_response.error}",
                    timestamp=datetime.utcnow(),
                )
            )

    def _herramientas_para_origen(self, origen: str) -> list[str]:
        herramientas = self.tool_registry.listar_nombres() if self.tool_registry else []
        permitidas = self.HERRAMIENTAS_POR_ORIGEN.get(origen)
        if not permitidas:
            return herramientas
        return [herramienta for herramienta in herramientas if herramienta in permitidas]

    def _gestionar_ejecucion(
        self,
        evento: EventoEntrante,
        accion_saliente: AccionSaliente,
    ) -> tuple[str, dict | None]:
        if accion_saliente.nombre == "none":
            return "sin_accion", None

        if accion_saliente.requiere_confirmacion:
            return (
                "pendiente_confirmacion",
                {
                    "mensaje": "La accion requiere confirmacion antes de ejecutarse.",
                    "error": None,
                    "datos_extra": None,
                },
            )

        if self._debe_ejecutar_localmente(evento):
            resultado_ejecucion = ActionExecutor.ejecutar(accion_saliente)
            return (
                "ok" if resultado_ejecucion.exito else "error",
                {
                    "mensaje": resultado_ejecucion.mensaje,
                    "error": resultado_ejecucion.error,
                    "datos_extra": resultado_ejecucion.datos_extra,
                },
            )

        return (
            "despachada",
            {
                "mensaje": f"Accion enviada al cliente '{evento.origen}' para su ejecucion.",
                "error": None,
                "datos_extra": None,
            },
        )

    @staticmethod
    def _debe_ejecutar_localmente(evento: EventoEntrante) -> bool:
        return evento.origen == "sandbox"

    def _obtener_transaccion_cacheada(self, id_transaccion: str) -> Optional[AccionSaliente]:
        accion = self._transacciones_procesadas.get(id_transaccion)
        if accion is not None:
            self._transacciones_procesadas.move_to_end(id_transaccion)
        return accion

    def _cachear_transaccion(self, id_transaccion: str, accion: AccionSaliente) -> None:
        self._transacciones_procesadas[id_transaccion] = accion
        self._transacciones_procesadas.move_to_end(id_transaccion)
        while len(self._transacciones_procesadas) > self.MAX_TRANSACCIONES_CACHE:
            self._transacciones_procesadas.popitem(last=False)

    @staticmethod
    def _construir_query_memoria(
        evento: EventoEntrante,
        narrativa: str,
        percepcion_resumida: str,
        tarea: dict | None,
    ) -> str:
        partes = [narrativa, percepcion_resumida, evento.tipo_evento]
        if tarea:
            objetivo = tarea.get("objetivo") or tarea.get("objetivo_id")
            if objetivo:
                partes.append(str(objetivo))
        return " ".join(parte for parte in partes if parte).strip()

    @staticmethod
    def _construir_narrativa_memoria(
        narrativa: str,
        percepcion_resumida: str,
        evento: EventoEntrante,
    ) -> str:
        partes = [narrativa]
        if percepcion_resumida:
            partes.append(percepcion_resumida)
        if evento.tipo_evento == "resultado_accion":
            accion = evento.detalles.get("accion")
            exito = evento.detalles.get("exito")
            mensaje = evento.detalles.get("mensaje_resultado")
            partes.append(f"Resultado de accion: accion={accion}, exito={exito}, mensaje={mensaje}.")
        return " ".join(parte for parte in partes if parte).strip() or evento.tipo_evento

    @staticmethod
    def _calcular_impacto_memoria(evento: EventoEntrante, es_interrupcion_critica: bool) -> float:
        if es_interrupcion_critica:
            return 0.9
        if evento.tipo_evento == "resultado_accion" and evento.detalles.get("exito") is False:
            return 0.7
        if evento.tipo_evento == "resultado_accion" and evento.detalles.get("exito") is True:
            return 0.45
        if evento.tipo_evento in {"inicio_simulacion", "actualizacion_estado"}:
            return 0.2
        return 0.5
