"""
core/orchestrator.py - Orquestador central de LAZZARO.
Coordina el flujo completo: evento -> estado interno -> memoria -> IA -> accion.
"""

from collections import OrderedDict, deque
from datetime import datetime
from typing import List, Optional
import uuid

from ai_engine.identity_state import IdentityState
from ai_engine.llm_generation_adapter import LLMGenerationAdapter
from ai_engine.motor_engine import MotorEngine
from ai_engine.oracle_client import OracleClient
from ai_engine.oracle_engine import OracleEngine
from ai_engine.oracle_memory import OracleMemory
from ai_engine.persona_engine import PersonaEngine
from ai_engine.schemas import ActionResponse, OracleResult
from ai_engine.triage_router import TriageRouter
from api.schemas import AccionSaliente, EstadoAgente, EventoEntrante, NotificacionSaliente
from core.logger import get_logger
from core.tool_policy import ToolPolicy
from tools.executor import ActionExecutor

logger = get_logger(__name__)


class Orchestrator:
    """
    Punto central que une BehaviorCore, MemoryManager, ReasoningEngine y ToolRegistry.
    Expone metodos para procesar eventos y obtener el estado del agente.
    """

    MAX_TRANSACCIONES_CACHE = 2000

    def __init__(self, behavior_core, memory_manager, reasoning_engine, tool_registry):
        self.behavior = behavior_core
        self.memory = memory_manager
        self.reasoning = reasoning_engine
        self.tool_registry = tool_registry
        self.triage_router = TriageRouter()
        self.tool_policy = ToolPolicy()
        self.motor_engine = MotorEngine()
        self.identity_state = IdentityState()
        self.persona_engine = PersonaEngine()
        self.llm_adapter = LLMGenerationAdapter(
            getattr(reasoning_engine, "ai_client", None),
            getattr(reasoning_engine, "policy_manager", None),
        )
        self.oracle_engine = OracleEngine()
        oracle_db_path = getattr(getattr(memory_manager, "client", None), "db_path", "./lazzaro_data")
        self.oracle_memory = OracleMemory(db_path=str(oracle_db_path or "./lazzaro_data"))
        oracle_settings = getattr(getattr(reasoning_engine, "ai_client", None), "settings", None)
        self.oracle_client = OracleClient(settings=oracle_settings)
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

        triage_decision = self._evaluar_triage(evento, estado_actual)
        solicita_decision = self._evento_solicita_decision(evento, es_interrupcion_critica)

        memoria_texto_completa = ""
        if self._evento_requiere_contexto_memoria(
            triage_decision,
            solicita_decision,
            es_interrupcion_critica,
        ):
            memoria_texto_completa = self._obtener_contexto_memoria_completa(
                evento,
                narrativa,
                percepcion_resumida,
                tarea,
            )

        self._guardar_evento_en_memoria(
            evento=evento,
            tipo_memoria=tipo_memoria,
            narrativa=narrativa,
            percepcion_resumida=percepcion_resumida,
            estado_actual=estado_actual,
            es_interrupcion_critica=es_interrupcion_critica,
        )

        herramientas: list[str] = []
        herramientas_detalle: dict[str, str] = {}
        if solicita_decision and not self._triage_debe_cortar_flujo(triage_decision):
            herramientas = self._herramientas_para_evento(evento, triage_decision)
            herramientas_detalle = (
                self.tool_registry.describir_herramientas(herramientas) if self.tool_registry else {}
            )

        if es_interrupcion_critica:
            action_response = self._resolver_interrupcion_critica(evento)
        elif self._triage_debe_cortar_flujo(triage_decision):
            razon_triage = str(
                triage_decision.get("reason")
                or "Evento filtrado por Triage Router."
            )
            action_response = self._accion_pasiva(razon_triage)
        elif self._es_resultado_hablar(evento):
            action_response = self._accion_pasiva()
        elif not solicita_decision:
            action_response = self._accion_pasiva()
        elif self._triage_es_motor(triage_decision):
            action_response = self._resolver_ruta_motor(
                evento=evento,
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto_completa,
                herramientas=herramientas,
                herramientas_detalle=herramientas_detalle,
                politica=politica,
                tarea=tarea,
            )
        elif self._triage_es_persona(triage_decision):
            action_response = self._resolver_ruta_persona(
                evento=evento,
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto_completa,
                politica=politica,
            )
        elif self._triage_es_oracle(triage_decision):
            action_response = self._resolver_ruta_oracle(
                evento=evento,
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto_completa,
                politica=politica,
            )
        else:
            try:
                action_response = self.reasoning.process(
                    contexto_llm=contexto_llm,
                    memoria_texto=memoria_texto_completa,
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
        self._registrar_turno_conversacional_si_aplica(
            evento=evento,
            triage_decision=triage_decision,
            action_response=action_response,
            accion_saliente=accion_saliente,
        )

        self._cachear_transaccion(evento.id_transaccion, accion_saliente)
        self._agregar_notificacion_si_corresponde(action_response)
        self._registrar_estado_identidad(evento, triage_decision, action_response)

        logger.info("Evento %s procesado. Accion: %s", evento.id_transaccion, accion_saliente.nombre)
        return accion_saliente

    def _obtener_contexto_memoria_completa(
        self,
        evento: EventoEntrante,
        narrativa: str,
        percepcion_resumida: str,
        tarea: dict | None,
    ) -> str:
        """Lee memoria solo para eventos que realmente llegaran a una capa cognitiva."""
        try:
            query_memoria = self._construir_query_memoria(evento, narrativa, percepcion_resumida, tarea)
            memoria_texto = self.memory.obtener_contexto_memoria(
                query=query_memoria,
                limite=5,
                max_caracteres=600,
            )
            memoria_conversacional = self._obtener_contexto_conversacional()
            memoria_corto_plazo = self.memory.obtener_ultimas_acciones()
            bloques_memoria = []
            if memoria_conversacional:
                bloques_memoria.append(memoria_conversacional)
            bloques_memoria.append(f"MEMORIA DE TRABAJO OPERATIVA:\n{memoria_corto_plazo}")
            bloques_memoria.append(f"RECUERDOS A LARGO PLAZO:\n{memoria_texto}")
            return "\n\n".join(bloques_memoria)
        except Exception as e:
            logger.error("Error leyendo contexto de memoria: %s", e, exc_info=True)
            return ""

    def _obtener_contexto_conversacional(self) -> str:
        """Lee continuidad conversacional solo cuando ya se autorizo leer memoria."""
        obtener = getattr(self.memory, "obtener_contexto_conversacional", None)
        if not callable(obtener):
            return ""
        try:
            return str(obtener(n=6) or "").strip()
        except Exception as e:
            logger.error("Error leyendo memoria conversacional: %s", e, exc_info=True)
            return ""

    def _guardar_evento_en_memoria(
        self,
        evento: EventoEntrante,
        tipo_memoria: str,
        narrativa: str,
        percepcion_resumida: str,
        estado_actual: dict,
        es_interrupcion_critica: bool,
    ) -> None:
        """Escribe todos los eventos para no romper consolidacion/sueno."""
        try:
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
            logger.error("Error guardando evento en MemoryManager: %s", e, exc_info=True)

    def _registrar_turno_conversacional_si_aplica(
        self,
        evento: EventoEntrante,
        triage_decision: dict,
        action_response: ActionResponse,
        accion_saliente: AccionSaliente,
    ) -> None:
        """Guarda en RAM el par usuario/respuesta cuando la salida final fue hablar."""
        if evento.tipo_evento != "comando_usuario":
            return
        if accion_saliente.nombre != "hablar":
            return

        registrar = getattr(self.memory, "registrar_turno_conversacional", None)
        if not callable(registrar):
            return

        mensaje_usuario = self._extraer_mensaje_usuario(evento)
        respuesta_lazzaro = self._extraer_texto_hablar(action_response.parametros)
        if not mensaje_usuario and not respuesta_lazzaro:
            return

        try:
            registrar(
                mensaje_usuario=mensaje_usuario,
                respuesta_lazzaro=respuesta_lazzaro,
                pensamiento=action_response.pensamiento,
                emocion=accion_saliente.emocion_facial,
                route=str(triage_decision.get("route", "")),
                intent=str(triage_decision.get("intent", "")),
                metadata={
                    "origen": evento.origen,
                    "id_transaccion": evento.id_transaccion,
                    "accion": accion_saliente.nombre,
                },
            )
        except Exception as e:
            logger.error("Error registrando turno conversacional: %s", e, exc_info=True)

    @staticmethod
    def _extraer_mensaje_usuario(evento: EventoEntrante) -> str:
        for clave in ("mensaje", "texto_usuario", "texto"):
            valor = evento.detalles.get(clave)
            if valor:
                return str(valor).strip()
        return ""

    @staticmethod
    def _extraer_texto_hablar(parametros: dict) -> str:
        if not isinstance(parametros, dict):
            return ""
        for clave in ("texto_a_comunicar", "texto", "mensaje"):
            valor = parametros.get(clave)
            if valor:
                return str(valor).strip()
        return ""

    @staticmethod
    def _accion_pasiva(razon: str | None = None) -> ActionResponse:
        return ActionResponse(
            pensamiento="",
            accion="none",
            parametros={},
            razon=razon or "Evento tratado como telemetria pasiva.",
            valido=True,
            emocion_facial="Fcl_ALL_Neutral",
        )

    def _evaluar_triage(self, evento: EventoEntrante, estado_actual: dict) -> dict:
        """Consulta Capa 0 para decidir si el evento debe llegar al LLM."""
        try:
            decision = self.triage_router.route_event(
                evento.model_dump(mode="python"),
                estado_agente=estado_actual if isinstance(estado_actual, dict) else {},
            )
        except Exception as e:
            logger.error("Error en TriageRouter; se conserva flujo anterior: %s", e, exc_info=True)
            return {}

        if not isinstance(decision, dict):
            logger.warning("TriageRouter devolvio una decision invalida: %r", decision)
            return {}

        logger.info(
            "Triage: intent=%s route=%s confidence=%s reason=%s",
            decision.get("intent"),
            decision.get("route"),
            decision.get("confidence"),
            decision.get("reason"),
        )
        return decision

    @staticmethod
    def _triage_debe_cortar_flujo(decision: dict) -> bool:
        route = str(decision.get("route", "")).strip().lower()
        return route in {"noop", "internal"}

    @staticmethod
    def _triage_es_motor(decision: dict) -> bool:
        return str(decision.get("route", "")).strip().lower() == "motor"

    @staticmethod
    def _triage_es_persona(decision: dict) -> bool:
        return str(decision.get("route", "")).strip().lower() == "persona"

    @staticmethod
    def _triage_es_oracle(decision: dict) -> bool:
        return str(decision.get("route", "")).strip().lower() == "oracle"

    def _evento_requiere_contexto_memoria(
        self,
        triage_decision: dict,
        solicita_decision: bool,
        es_interrupcion_critica: bool,
    ) -> bool:
        if es_interrupcion_critica:
            return False
        if not solicita_decision:
            return False
        return not self._triage_debe_cortar_flujo(triage_decision)

    def _resolver_ruta_persona(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        politica: str,
    ) -> ActionResponse:
        """Usa Capa 2 para conversacion social, sin herramientas fisicas."""
        mensaje_usuario = str(evento.detalles.get("mensaje", "")).strip()
        percepcion = self._extraer_percepcion_motor(evento, contexto_llm)

        try:
            mensajes = self.persona_engine.preparar_interaccion(
                mensaje_usuario=mensaje_usuario,
                estado_biologico=self.identity_state,
                percepcion=percepcion,
                recuerdos=memoria_texto,
            )
            raw_response = self.llm_adapter.ejecutar_texto(
                mensajes,
                politica=politica,
                temperature=0.45,
                max_output_tokens=768,
                token_strategy="cap",
                error_context="Capa Social",
            )
            datos = self.persona_engine.parsear_respuesta(raw_response)
            if datos.get("requiere_reintento"):
                logger.info("Capa Social solicita reintento breve por salida LLM irrecuperable.")
                mensajes_reintento = self.persona_engine.preparar_reintento_json(
                    mensaje_usuario=mensaje_usuario,
                    respuesta_fallida=raw_response or "",
                )
                raw_response_reintento = self.llm_adapter.ejecutar_texto(
                    mensajes_reintento,
                    politica=politica,
                    temperature=0.2,
                    max_output_tokens=256,
                    token_strategy="cap",
                    error_context="Reintento Capa Social",
                )
                datos_reintento = self.persona_engine.parsear_respuesta(raw_response_reintento)
                datos = datos_reintento
            return self._action_response_desde_persona(datos)
        except Exception as e:
            logger.error("Error en PersonaEngine: %s", e, exc_info=True)
            return ActionResponse(
                pensamiento="Fallback social por error interno.",
                accion="hablar",
                parametros={"texto_a_comunicar": "Me perdi un momento, pero sigo aqui."},
                razon="Error interno en Capa Social.",
                valido=True,
                emocion_facial="Fcl_ALL_Neutral",
                error=str(e),
            )

    @staticmethod
    def _action_response_desde_persona(datos: dict) -> ActionResponse:
        if not isinstance(datos, dict):
            datos = {}

        parametros = datos.get("parametros", {})
        if not isinstance(parametros, dict):
            parametros = {}

        texto = str(parametros.get("texto_a_comunicar", "")).strip()
        if not texto:
            texto = "No se que decir, mi mente se fue por una rendija."
            parametros = {"texto_a_comunicar": texto}

        return ActionResponse(
            pensamiento=str(datos.get("pensamiento_privado", "")),
            accion="hablar",
            parametros=parametros,
            razon=str(datos.get("razon", "Respuesta social de Capa 2.")),
            valido=True,
            emocion_facial=str(datos.get("emocion_facial", "Fcl_ALL_Neutral")),
        )

    def _resolver_ruta_oracle(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        politica: str,
    ) -> ActionResponse:
        """Usa Capa 3 para conocimiento frio y delega la voz publica a Persona."""
        mensaje_usuario = str(evento.detalles.get("mensaje", "")).strip()
        if not mensaje_usuario:
            oracle_result = OracleResult(
                pregunta_original="",
                contenido_tecnico="La consulta al Oraculo no contiene mensaje de usuario.",
                resumen_para_persona="No habia una consulta clara para procesar.",
                fuente="error",
                cache_hit=False,
                error="La consulta al Oraculo no contiene mensaje de usuario.",
            )
            return self._verbalizar_resultado_oracle(
                evento=evento,
                contexto_llm=contexto_llm,
                memoria_texto=memoria_texto,
                politica=politica,
                oracle_result=oracle_result,
            )

        errores: list[str] = []
        try:
            oracle_result = self.oracle_engine.evaluar_cache(
                mensaje_usuario,
                self.oracle_memory,
            )
        except Exception as e:
            logger.error("Error consultando OracleMemory: %s", e, exc_info=True)
            errores.append("OracleMemory no pudo consultar el cache semantico.")
            oracle_result = None

        if oracle_result is None:
            try:
                mensajes = self.oracle_engine.preparar_consulta(mensaje_usuario)
                respuesta_cruda = None
                fuente_oraculo = "api"

                if self.oracle_client.configurado:
                    try:
                        respuesta_cruda = self.oracle_client.consultar_oraculo(mensajes)
                    except Exception as e:
                        logger.error("Error inesperado en OracleClient: %s", e, exc_info=True)
                        errores.append("OracleClient externo fallo; se intento fallback activo.")
                        respuesta_cruda = None

                    if not respuesta_cruda or not respuesta_cruda.strip():
                        fuente_oraculo = "fallback"
                        logger.warning(
                            "El Oraculo externo no respondio; se usara el cliente LLM activo como fallback."
                        )
                else:
                    fuente_oraculo = "fallback"
                    logger.info(
                        "OracleClient sin API key; se usara el cliente LLM activo como Oraculo."
                    )

                if not respuesta_cruda or not respuesta_cruda.strip():
                    respuesta_cruda = self.llm_adapter.ejecutar_texto(
                        mensajes,
                        politica=politica,
                        temperature=0.1,
                        max_output_tokens=2048,
                        token_strategy="floor",
                        error_context="fallback activo del Oraculo",
                    )

                oracle_result = self.oracle_engine.keep_y_save(
                    respuesta_cruda or "",
                    mensaje_usuario,
                    self.oracle_memory,
                    fuente=fuente_oraculo,
                )
            except Exception as e:
                logger.error("Error en OracleEngine: %s", e, exc_info=True)
                oracle_result = OracleResult(
                    pregunta_original=mensaje_usuario,
                    contenido_tecnico="No pude completar esa consulta compleja en este momento.",
                    resumen_para_persona="Explica que la consulta compleja fallo de forma natural.",
                    fuente="error",
                    cache_hit=False,
                    error=f"Error interno en Capa Oraculo: {e}",
                )

        if oracle_result.error:
            errores.append(oracle_result.error)
        if errores:
            oracle_result = oracle_result.model_copy(update={"error": " ".join(errores)})

        return self._verbalizar_resultado_oracle(
            evento=evento,
            contexto_llm=contexto_llm,
            memoria_texto=memoria_texto,
            politica=politica,
            oracle_result=oracle_result,
        )

    def _verbalizar_resultado_oracle(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        politica: str,
        oracle_result: OracleResult,
    ) -> ActionResponse:
        """Pasa conocimiento frio del Oraculo por Persona antes de responder a Unity."""
        mensaje_usuario = str(evento.detalles.get("mensaje", "")).strip()
        percepcion = self._extraer_percepcion_motor(evento, contexto_llm)

        try:
            mensajes = self.persona_engine.preparar_verbalizacion_oraculo(
                mensaje_usuario=mensaje_usuario,
                estado_biologico=self.identity_state,
                datos_oraculo=oracle_result,
                percepcion=percepcion,
                recuerdos=memoria_texto,
            )
            raw_response = self.llm_adapter.ejecutar_texto(
                mensajes,
                politica=politica,
                temperature=0.35,
                max_output_tokens=768,
                token_strategy="cap",
                error_context="Verbalizacion Capa Social del Oraculo",
            )
            datos = self.persona_engine.parsear_respuesta(raw_response)
            if datos.get("requiere_reintento"):
                logger.info("Verbalizacion del Oraculo solicita reintento social breve.")
                mensajes_reintento = self.persona_engine.preparar_reintento_json(
                    mensaje_usuario=mensaje_usuario,
                    respuesta_fallida=raw_response or "",
                )
                raw_response_reintento = self.llm_adapter.ejecutar_texto(
                    mensajes_reintento,
                    politica=politica,
                    temperature=0.2,
                    max_output_tokens=256,
                    token_strategy="cap",
                    error_context="Reintento verbalizacion Capa Social del Oraculo",
                )
                datos = self.persona_engine.parsear_respuesta(raw_response_reintento)

            action_response = self._action_response_desde_persona(datos)
            if oracle_result.error:
                action_response = action_response.model_copy(update={"error": oracle_result.error})
            return action_response
        except Exception as e:
            logger.error("Error verbalizando resultado del Oraculo: %s", e, exc_info=True)
            error = str(e)
            if oracle_result.error:
                error = f"{oracle_result.error} {error}".strip()
            return ActionResponse(
                pensamiento="Fallback social por error al verbalizar Oraculo.",
                accion="hablar",
                parametros={"texto_a_comunicar": "No pude completar esa consulta compleja en este momento."},
                razon="Error verbalizando conocimiento de Oraculo en Capa Social.",
                valido=True,
                error=error,
                emocion_facial="Fcl_ALL_Neutral",
            )

    def _resolver_ruta_motor(
        self,
        evento: EventoEntrante,
        contexto_llm: dict,
        memoria_texto: str,
        herramientas: list[str],
        herramientas_detalle: dict[str, str],
        politica: str,
        tarea: dict | None,
    ) -> ActionResponse:
        """Usa Capa 1 para resolver/validar acciones fisicas."""
        percepcion = self._extraer_percepcion_motor(evento, contexto_llm)
        mensaje = str(evento.detalles.get("mensaje", ""))

        diagnostico_motor = self.motor_engine.diagnosticar_comando_usuario(
            mensaje,
            percepcion=percepcion,
            herramientas_disponibles=herramientas,
        )
        if diagnostico_motor["estado"] == "lista":
            accion_motora = self.motor_engine.procesar_comando_motor(
                str(diagnostico_motor["accion"]),
                diagnostico_motor["parametros"],
                percepcion=percepcion,
            )
            return self._action_response_desde_motor(
                accion_motora,
                pensamiento="",
                emocion_facial="Fcl_ALL_Neutral",
            )
        if diagnostico_motor["estado"] in {"falta_objetivo", "ambigua"}:
            return self._respuesta_aclaracion_motor(diagnostico_motor)

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
            logger.error("Error en ReasoningEngine para ruta motor: %s", e, exc_info=True)
            return ActionResponse(
                pensamiento="Error al razonar accion motora.",
                accion="none",
                parametros={},
                razon="",
                valido=False,
                error=str(e),
            )

        if action_response.accion in self.motor_engine.ACCIONES_MOTORAS:
            accion_validada = self.motor_engine.procesar_comando_motor(
                action_response.accion,
                action_response.parametros,
                percepcion=percepcion,
            )
            return self._action_response_desde_motor(
                accion_validada,
                pensamiento=action_response.pensamiento,
                emocion_facial=action_response.emocion_facial,
            )

        return action_response

    @staticmethod
    def _extraer_percepcion_motor(evento: EventoEntrante, contexto_llm: dict) -> dict:
        percepcion: dict = {}
        percepcion_contexto = contexto_llm.get("percepcion") if isinstance(contexto_llm, dict) else None
        if isinstance(percepcion_contexto, dict):
            percepcion.update(percepcion_contexto)

        claves_percepcion = {
            "objetos_visibles",
            "resumen_percepcion",
            "objeto_mas_cercano",
            "cantidad_objetos_visibles",
            "posicion_lazzaro",
            "estado_fisico",
            "salud_avatar",
            "mensaje_critico",
        }
        for clave in claves_percepcion:
            if clave in evento.detalles:
                percepcion[clave] = evento.detalles[clave]
        return percepcion

    @staticmethod
    def _action_response_desde_motor(
        motor_action: dict,
        pensamiento: str,
        emocion_facial: str,
    ) -> ActionResponse:
        if not isinstance(motor_action, dict):
            motor_action = {
                "accion": "none",
                "parametros": {},
                "razon": "Capa 1 devolvio una respuesta invalida.",
            }

        accion = str(motor_action.get("accion", "none"))
        valido = accion != "none"
        return ActionResponse(
            pensamiento=pensamiento,
            accion=accion,
            parametros=motor_action.get("parametros", {}) if isinstance(motor_action, dict) else {},
            razon=str(motor_action.get("razon", "")),
            valido=valido,
            error=None if valido else str(motor_action.get("razon", "Accion bloqueada por Capa 1.")),
            emocion_facial=emocion_facial or "Fcl_ALL_Neutral",
        )

    @staticmethod
    def _respuesta_aclaracion_motor(diagnostico_motor: dict) -> ActionResponse:
        accion = str(diagnostico_motor.get("accion", "")).strip()
        mensajes = {
            "navegar_a_objeto": "Dime a que objeto quieres que vaya.",
            "alejarse_de_objeto": "Dime de que objeto quieres que me aleje.",
            "interactuar_objeto_3d": "Dime con que objeto quieres que interactue.",
            "agarrar_objeto": "Dime que objeto quieres que agarre.",
            "aplicar_fuerza": "Dime a que objeto quieres que le aplique fuerza.",
        }
        texto = mensajes.get(accion, "Dime que objeto quieres que use para esa accion.")
        return ActionResponse(
            pensamiento="Comando motor con objetivo incompleto; pido aclaracion antes de actuar.",
            accion="hablar",
            parametros={"texto_a_comunicar": texto},
            razon="Capa 1 bloqueo un comando motor ambiguo para evitar alucinaciones fisicas.",
            valido=True,
            emocion_facial="Fcl_ALL_Neutral",
        )

    def _registrar_estado_identidad(
        self,
        evento: EventoEntrante,
        triage_decision: dict,
        action_response: ActionResponse,
    ) -> None:
        """Actualiza energia, estres y enfoque despues de procesar el evento."""
        try:
            route = str(triage_decision.get("route", "")).strip().lower()
            intent = str(triage_decision.get("intent", "")).strip()

            if evento.tipo_evento == "alerta_dolor_fisico":
                route = "motor"
                intent = intent or "INTERRUPCION_CRITICA"

            es_descanso = self._evento_es_descanso(evento, action_response)
            if es_descanso:
                try:
                    self.memory.consolidar_memorias_si_hace_falta()
                except Exception as e:
                    logger.error("Error consolidando memorias durante descanso: %s", e, exc_info=True)

            success = bool(action_response.valido and not action_response.error)
            self.identity_state.registrar_evento(
                intent=intent,
                route=route,
                success=success,
                es_descanso=es_descanso,
                intensidad=self._intensidad_identidad(evento, route),
            )
        except Exception as e:
            logger.error("Error actualizando IdentityState: %s", e, exc_info=True)

    @staticmethod
    def _evento_es_descanso(evento: EventoEntrante, action_response: ActionResponse) -> bool:
        texto = " ".join(
            str(parte or "")
            for parte in (
                evento.detalles.get("mensaje"),
                evento.detalles.get("mensaje_resultado"),
                action_response.parametros.get("texto_a_comunicar")
                if isinstance(action_response.parametros, dict)
                else "",
            )
        ).lower()
        return any(token in texto for token in ("descansa", "dormir", "duerme", "reposar"))

    @staticmethod
    def _intensidad_identidad(evento: EventoEntrante, route: str) -> float:
        if evento.tipo_evento in {"actualizacion_estado", "resultado_accion"}:
            return 0.2
        if evento.tipo_evento == "alerta_dolor_fisico":
            return 1.4
        if route == "oracle":
            return 1.2
        return 1.0

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

    def close(self) -> None:
        """Cierra recursos auxiliares persistentes del orquestador."""
        close_oracle_memory = getattr(self.oracle_memory, "close", None)
        if callable(close_oracle_memory):
            close_oracle_memory()

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

        objeto_escape = self._leer_objeto_escape(evento)
        if self.tool_registry and self.tool_registry.get_tool("alejarse_de_objeto") and objeto_escape:
            return ActionResponse(
                pensamiento=pensamiento,
                accion="alejarse_de_objeto",
                parametros={
                    "nombre_objeto": objeto_escape,
                    "distancia": self._leer_distancia_escape(distancia),
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
    def _leer_objeto_escape(evento: EventoEntrante) -> str | None:
        candidatos = (
            evento.objeto_involucrado,
            evento.detalles.get("objeto"),
            evento.detalles.get("nombre_objeto"),
            evento.detalles.get("objeto_colisionado"),
        )
        for candidato in candidatos:
            nombre = str(candidato or "").strip()
            if nombre and nombre.lower() not in {"ninguno", "none", "null", "desconocido"}:
                return nombre
        return None

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

    def _herramientas_para_evento(self, evento: EventoEntrante, triage_decision: dict) -> list[str]:
        herramientas = self.tool_registry.listar_nombres() if self.tool_registry else []
        return self.tool_policy.seleccionar(
            herramientas,
            origen=evento.origen,
            route=str(triage_decision.get("route", "")),
        )

    @staticmethod
    def _evento_solicita_decision(evento: EventoEntrante, es_interrupcion_critica: bool) -> bool:
        if es_interrupcion_critica:
            return True
        if evento.detalles.get("solicita_decision") is True:
            return True
        return evento.tipo_evento == "comando_usuario"

    @staticmethod
    def _es_resultado_hablar(evento: EventoEntrante) -> bool:
        return evento.tipo_evento == "resultado_accion" and evento.detalles.get("accion") == "hablar"

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
