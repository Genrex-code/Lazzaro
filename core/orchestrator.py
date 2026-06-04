"""
core/orchestrator.py - Orquestador central de LAZZARO.
Coordina el flujo completo: evento -> estado interno -> memoria -> IA -> accion.
"""

from collections import OrderedDict, deque
from datetime import datetime
from typing import List, Optional
import uuid

from ai_engine.identity_state import IdentityState
from ai_engine.motor_engine import MotorEngine
from ai_engine.oracle_client import OracleClient
from ai_engine.oracle_engine import OracleEngine
from ai_engine.oracle_memory import OracleMemory
from ai_engine.persona_engine import PersonaEngine
from ai_engine.schemas import ActionResponse
from ai_engine.triage_router import TriageRouter
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
            "alejarse_de_objeto",
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
            "alejarse_de_objeto",
            "moverse_entorno_3d",
            "interactuar_objeto_3d",
            "agarrar_objeto",
            "soltar_objeto",
            "aplicar_fuerza",
            "hablar",
        },
    }
    HERRAMIENTAS_CONVERSACION = {"hablar"}
    HERRAMIENTAS_INTERACCION = {"interactuar_objeto_3d", "agarrar_objeto", "soltar_objeto", "aplicar_fuerza"}
    MAX_TRANSACCIONES_CACHE = 2000

    def __init__(self, behavior_core, memory_manager, reasoning_engine, tool_registry):
        self.behavior = behavior_core
        self.memory = memory_manager
        self.reasoning = reasoning_engine
        self.tool_registry = tool_registry
        self.triage_router = TriageRouter()
        self.motor_engine = MotorEngine()
        self.identity_state = IdentityState()
        self.persona_engine = PersonaEngine()
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

        memoria_texto = ""
        memoria_texto_completa = ""
        try:
            query_memoria = self._construir_query_memoria(evento, narrativa, percepcion_resumida, tarea)
            memoria_texto = self.memory.obtener_contexto_memoria(
                query=query_memoria,
                limite=5,
                max_caracteres=600,
            )
            memoria_corto_plazo = self.memory.obtener_ultimas_acciones()
            memoria_texto_completa = (
                f"{memoria_corto_plazo}\n\n"
                f"RECUERDOS A LARGO PLAZO:\n{memoria_texto}"
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

        triage_decision = self._evaluar_triage(evento, estado_actual)
        solicita_decision = self._evento_solicita_decision(evento, es_interrupcion_critica)
        herramientas = self._filtrar_herramientas_por_intencion(
            evento,
            self._herramientas_para_origen(evento.origen),
        )
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

        self._cachear_transaccion(evento.id_transaccion, accion_saliente)
        self._agregar_notificacion_si_corresponde(action_response)
        self._registrar_estado_identidad(evento, triage_decision, action_response)

        logger.info("Evento %s procesado. Accion: %s", evento.id_transaccion, accion_saliente.nombre)
        return accion_saliente

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
            prompt = self._mensajes_chat_a_prompt(mensajes)
            raw_response = self._generar_persona_llm(prompt, politica)
            datos = self.persona_engine.parsear_respuesta(raw_response)
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

    def _generar_persona_llm(self, prompt: str, politica: str) -> str:
        """Llama al cliente LLM activo usando parametros conservadores para social."""
        ai_client = getattr(self.reasoning, "ai_client", None)
        if ai_client is None:
            raise RuntimeError("ReasoningEngine no expone ai_client para Capa Social.")

        proveedor = getattr(ai_client, "provider_name", ai_client.__class__.__name__.lower())
        generation = self.reasoning.policy_manager.ajustar_parametros(
            politica,
            herramientas=["hablar"],
            herramientas_riesgosas=[],
            proveedor=proveedor,
        )["generation"]
        generation["temperature"] = min(float(generation.get("temperature", 0.7)), 0.45)
        generation["max_output_tokens"] = min(int(generation.get("max_output_tokens", 512)), 768)
        # No forzamos application/json aqui: Ollama usaria el schema global LLMResponse,
        # mientras PersonaEngine espera pensamiento_privado. El prompt social ya exige JSON.
        generation["response_mime_type"] = "text/plain"
        return ai_client.generate(prompt, **generation)

    @staticmethod
    def _mensajes_chat_a_prompt(mensajes: list[dict[str, str]]) -> str:
        """Adapta mensajes tipo chat al prompt plano que usan los clientes actuales."""
        partes: list[str] = []
        for mensaje in mensajes:
            if not isinstance(mensaje, dict):
                continue
            rol = str(mensaje.get("role", "user")).upper()
            contenido = str(mensaje.get("content", "")).strip()
            if contenido:
                partes.append(f"[{rol}]\n{contenido}")
        return "\n\n".join(partes)

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
        politica: str,
    ) -> ActionResponse:
        """Usa Capa 3 para conocimiento complejo con cache y fallback local."""
        mensaje_usuario = str(evento.detalles.get("mensaje", "")).strip()
        if not mensaje_usuario:
            return self._action_response_desde_oracle(
                {},
                error="La consulta al Oraculo no contiene mensaje de usuario.",
            )

        errores: list[str] = []
        try:
            respuesta_cacheada = self.oracle_engine.evaluar_cache(
                mensaje_usuario,
                self.oracle_memory,
            )
        except Exception as e:
            logger.error("Error consultando OracleMemory: %s", e, exc_info=True)
            errores.append("OracleMemory no pudo consultar el cache semantico.")
            respuesta_cacheada = None

        if respuesta_cacheada is not None:
            return self._action_response_desde_oracle(respuesta_cacheada)

        try:
            mensajes = self.oracle_engine.preparar_consulta(mensaje_usuario)
            fallo_externo_registrado = False
            try:
                respuesta_cruda = self.oracle_client.consultar_oraculo(mensajes)
            except Exception as e:
                logger.error("Error inesperado en OracleClient: %s", e, exc_info=True)
                errores.append("El Oraculo externo fallo; se utilizo fallback local.")
                fallo_externo_registrado = True
                respuesta_cruda = None

            if not respuesta_cruda or not respuesta_cruda.strip():
                if self.oracle_client.configurado and not fallo_externo_registrado:
                    errores.append("El Oraculo externo no respondio; se utilizo fallback local.")
                respuesta_cruda = self._generar_oracle_local(mensajes, politica)

            datos = self.oracle_engine.keep_y_save(
                respuesta_cruda or "",
                mensaje_usuario,
                self.oracle_memory,
            )
            error_engine = str(datos.get("error", "")).strip() if isinstance(datos, dict) else ""
            if error_engine:
                errores.append(error_engine)

            return self._action_response_desde_oracle(
                datos,
                error=" ".join(errores) or None,
            )
        except Exception as e:
            logger.error("Error en OracleEngine: %s", e, exc_info=True)
            return self._action_response_desde_oracle(
                {},
                error=f"Error interno en Capa Oraculo: {e}",
            )

    def _generar_oracle_local(self, mensajes: list[dict[str, str]], politica: str) -> str:
        """Consulta Ollama local conservando el contenido de los mensajes del Oraculo."""
        ai_client = getattr(self.reasoning, "ai_client", None)
        if ai_client is None:
            raise RuntimeError("ReasoningEngine no expone ai_client para fallback del Oraculo.")

        proveedor = getattr(ai_client, "provider_name", ai_client.__class__.__name__.lower())
        if str(proveedor).strip().lower() != "ollama":
            raise RuntimeError("El cliente LLM activo no es Ollama; fallback local no disponible.")

        generation = self.reasoning.policy_manager.ajustar_parametros(
            politica,
            herramientas=["hablar"],
            herramientas_riesgosas=[],
            proveedor=proveedor,
        )["generation"]
        generation["temperature"] = min(float(generation.get("temperature", 0.7)), 0.1)
        generation["max_output_tokens"] = max(int(generation.get("max_output_tokens", 1024)), 2048)
        # OllamaClient recibe prompts planos; este adaptador conserva roles y contenido.
        # No se fuerza el schema global LLMResponse porque Oraculo usa pensamiento_privado.
        generation["response_mime_type"] = "text/plain"
        prompt = self._mensajes_chat_a_prompt(mensajes)
        return ai_client.generate(prompt, **generation)

    @staticmethod
    def _action_response_desde_oracle(
        datos: dict,
        error: str | None = None,
    ) -> ActionResponse:
        """Convierte cualquier salida de Capa 3 en una respuesta hablada segura."""
        if not isinstance(datos, dict):
            datos = {}

        parametros = datos.get("parametros", {})
        if not isinstance(parametros, dict):
            parametros = {}

        texto = str(parametros.get("texto_a_comunicar", "")).strip()
        if not texto:
            texto = "No pude completar esa consulta compleja en este momento."
            error = error or "Capa Oraculo no produjo texto para comunicar."

        return ActionResponse(
            pensamiento=str(datos.get("pensamiento_privado", "")),
            accion="hablar",
            parametros={"texto_a_comunicar": texto},
            razon=str(datos.get("razon", "Respuesta analitica de Capa 3.")),
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

        accion_motora = self.motor_engine.resolver_comando_usuario(
            mensaje,
            percepcion=percepcion,
            herramientas_disponibles=herramientas,
        )
        if accion_motora is not None:
            return self._action_response_desde_motor(
                accion_motora,
                pensamiento="",
                emocion_facial="Fcl_ALL_Neutral",
            )

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

    def _filtrar_herramientas_por_intencion(
        self,
        evento: EventoEntrante,
        herramientas: list[str],
    ) -> list[str]:
        if evento.tipo_evento != "comando_usuario":
            return herramientas

        mensaje = str(evento.detalles.get("mensaje", "")).lower()
        if not self._es_intencion_fisica(mensaje):
            return [herramienta for herramienta in herramientas if herramienta in self.HERRAMIENTAS_CONVERSACION]

        if "sin interactuar" in mensaje or "no interact" in mensaje:
            return [
                herramienta
                for herramienta in herramientas
                if herramienta not in self.HERRAMIENTAS_INTERACCION
            ]
        return herramientas

    @staticmethod
    def _es_intencion_fisica(mensaje: str) -> bool:
        marcadores_conversacion = (
            "te gusta",
            "como te llamas",
            "cómo te llamas",
            "quien eres",
            "quién eres",
            "por que",
            "por qué",
            "fuiste",
            "eras",
            "historia",
            "hablemos",
            "charlemos",
        )
        marcadores_comando = (
            "podrias",
            "podrías",
            "intenta",
            "intentar",
            "deja de",
            "ve a",
            "vete",
            "dirigete",
            "dirígete",
        )
        verbos_fisicos = (
            "mov",
            "muev",
            "muév",
            "camina",
            "acerc",
            "alej",
            "aléj",
            "navega",
            "retrocede",
            "atras",
            "atrás",
            "paso",
            "seguir",
            "sigue",
            "interact",
            "agarr",
            "suelta",
            "golpea",
            "empuja",
            "fuerza",
        )

        es_conversacion = any(marcador in mensaje for marcador in marcadores_conversacion)
        es_comando_explicito = any(marcador in mensaje for marcador in marcadores_comando)
        tiene_verbo_fisico = any(verbo in mensaje for verbo in verbos_fisicos)

        if es_conversacion and not es_comando_explicito:
            return False
        return tiene_verbo_fisico

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
