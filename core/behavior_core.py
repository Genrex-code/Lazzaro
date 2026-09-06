"""
behavior_core.py - Capa 3 del sistema de comportamiento de Lazzaro.
Fachada principal que integra StateEngine, PersonalityTraits, TaskContext y PsychologyEngine.
"""

import copy
from datetime import datetime, timezone
import threading
from typing import Any

from core.logger import get_logger
from core.personality import PersonalityTraits
from core.psychology import PsychologyEngine
from core.state_engine import StateEngine
from core.task_context import (
    TaskContext,
    TaskIntent,
    TaskTransitionReceipt,
    TaskTransitionStatus,
)
from telescopio.constelacion_cognitiva import ConstelacionCognitiva

logger = get_logger(__name__)


class BehaviorCore:
    """
    Orquestador interno del agente.
    Mantiene el estado, la personalidad, el contexto de tarea actual y la politica activa.
    """

    EVENTOS_CON_RESULTADO = {"resultado_accion", "alerta_dolor_fisico"}
    EVENTOS_PERCEPTIVOS = {"inicio_simulacion", "actualizacion_estado", "percepcion_entorno"}
    ACCIONES_EXPLORATORIAS = {
        "navegar_a_objeto",
        "navegar_a_posicion",
        "alejarse_de_objeto",
        "girar_avatar",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "soltar_objeto",
        "aplicar_fuerza",
    }

    def __init__(self):
        self._owner_lock = threading.RLock()
        self.state_engine = StateEngine()
        self.personality = PersonalityTraits()
        self.psychology = PsychologyEngine()

        self.task_context: TaskContext | None = None

        self.policy = "exploradora"
        self.policies_disponibles = ["cautelosa", "exploradora", "eficiente", "social", "autonoma"]
        self._offsets_politica: dict[str, float] = {}
        self._constelacion = ConstelacionCognitiva(nombre="lazzaro_cognitiva")
        self._ultima_percepcion: dict[str, Any] = {}
        self._ultima_percepcion_resumida = ""
        self._actualizar_modificadores_globales()

    def iniciar_tarea(self, objetivo_id: str) -> TaskTransitionReceipt:
        """Entrada explícita legacy: crea o reemplaza, nunca infiere desde prosa."""
        with self._owner_lock:
            intent = TaskIntent.START if self.task_context is None else TaskIntent.REPLACE
            return self._aplicar_transicion_tarea_locked(intent, objetivo_id=objetivo_id)

    def finalizar_tarea(self, exito: bool) -> TaskTransitionReceipt:
        """Compatibilidad explícita: éxito completa; no-éxito abandona la task."""
        if type(exito) is not bool:
            raise TypeError("BehaviorCore.finalizar_tarea exige un bool estricto")
        with self._owner_lock:
            if exito:
                current_id = self.task_context.task_instance_id if self.task_context else None
                result = self.procesar_evento(
                    "tarea_completada",
                    {
                        "intensidad": 1.0,
                        "task_intent": TaskIntent.COMPLETE.value,
                        "task_instance_id": current_id,
                    },
                )
                return TaskTransitionReceipt.from_dict(result["task_transition"])
            return self._abandonar_tarea_locked("Finalizacion explicita sin exito.")

    def cancelar_tarea(self, motivo: str = "") -> TaskTransitionReceipt:
        """Cancela explícitamente la instancia activa y conserva su identidad."""
        with self._owner_lock:
            return self._aplicar_transicion_tarea_locked(
                TaskIntent.CANCEL,
                reason=motivo or "Solicitud explicita.",
            )

    def aplicar_transicion_tarea(
        self,
        intent: TaskIntent | str,
        *,
        objetivo_id: str | None = None,
        expected_task_instance_id: str | None = None,
        reason: str = "",
    ) -> TaskTransitionReceipt:
        """Aplica una transición estructurada bajo el owner de BehaviorCore."""
        with self._owner_lock:
            try:
                parsed_intent = intent if isinstance(intent, TaskIntent) else TaskIntent(str(intent).lower())
            except ValueError:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    TaskIntent.NONE,
                    reason="Task intent fuera de contrato.",
                )
            if parsed_intent is TaskIntent.ABANDON:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    parsed_intent,
                    reason="ABANDON sólo puede producirlo la policy interna.",
                )
            return self._aplicar_transicion_tarea_locked(
                parsed_intent,
                objetivo_id=objetivo_id,
                expected_task_instance_id=expected_task_instance_id,
                reason=reason,
            )

    def procesar_evento(self, tipo_evento: str, detalles: dict | None = None) -> dict:
        """
        Recibe un evento externo y actualiza todo el sistema.
        Retorna un diccionario con el nuevo estado, banderas y contexto LLM.
        """
        with self._owner_lock:
            with self.state_engine.atomic():
                checkpoint = self._crear_checkpoint_operacional_locked()
                try:
                    return self._procesar_evento_locked(tipo_evento, detalles)
                except Exception:
                    self._restaurar_checkpoint_operacional_locked(checkpoint)
                    raise

    def _crear_checkpoint_operacional_locked(self) -> dict[str, Any]:
        """Snapshot local pequeño; no serializa prompts, modelos ni memoria externa."""
        return {
            "state_engine": self.state_engine.crear_checkpoint_operacional(),
            "personality": copy.deepcopy(self.personality.__dict__),
            "task_context": copy.deepcopy(self.task_context),
            "policy": self.policy,
            "offsets_politica": copy.deepcopy(self._offsets_politica),
            "constelacion": copy.deepcopy(self._constelacion),
            "ultima_percepcion": copy.deepcopy(self._ultima_percepcion),
            "ultima_percepcion_resumida": self._ultima_percepcion_resumida,
        }

    def _restaurar_checkpoint_operacional_locked(self, checkpoint: dict[str, Any]) -> None:
        """Rollback exacto del estado poseído por BehaviorCore."""
        self.state_engine.restaurar_checkpoint_operacional(checkpoint["state_engine"])
        self.personality.__dict__.clear()
        self.personality.__dict__.update(copy.deepcopy(checkpoint["personality"]))
        self.task_context = copy.deepcopy(checkpoint["task_context"])
        self.policy = checkpoint["policy"]
        self._offsets_politica = copy.deepcopy(checkpoint["offsets_politica"])
        self._constelacion = copy.deepcopy(checkpoint["constelacion"])
        self._ultima_percepcion = copy.deepcopy(checkpoint["ultima_percepcion"])
        self._ultima_percepcion_resumida = checkpoint["ultima_percepcion_resumida"]

    def _procesar_evento_locked(self, tipo_evento: str, detalles: dict | None = None) -> dict:
        """Implementa el turno mientras BehaviorCore y StateEngine son exclusivos."""
        if detalles is None:
            detalles = {}

        intensidad = self._leer_intensidad(detalles)
        accion = detalles.get("accion")
        exito = detalles.get("exito")
        evento_emocional = self._resolver_evento_emocional(tipo_evento, detalles)

        task_transition = self._sincronizar_tarea_desde_detalles(tipo_evento, detalles)
        lifecycle_event_applies = not (
            tipo_evento == "tarea_completada"
            and task_transition.status is not TaskTransitionStatus.COMPLETED
        )
        percepcion = self._extraer_percepcion(detalles)
        percepcion_resumida = self._resumir_percepcion(percepcion)
        if percepcion:
            self._ultima_percepcion = copy.deepcopy(percepcion)
            self._ultima_percepcion_resumida = percepcion_resumida

        logger.info("Procesando evento: '%s' intensidad=%s", tipo_evento, intensidad)

        if lifecycle_event_applies:
            self.state_engine.aplicar_evento(evento_emocional, intensidad, contexto=detalles)
            self.personality.update_from_event(evento_emocional, intensidad)

        task_outcome_allowed = self._resultado_pertenece_a_tarea_actual(
            tipo_evento,
            detalles,
        )
        if (
            self.task_context is not None
            and accion is not None
            and type(exito) is bool
            and task_outcome_allowed
        ):
            self.task_context.registrar_intento(str(accion), exito)
            self.task_context.actualizar_umbral_abandono(self.personality.paciencia)
            # Los detectores permanecen visibles en el resumen de TaskContext.
            # No son outcomes adicionales y, por tanto, no reaplican State/Personality.
        elif accion is not None and exito is not None and type(exito) is not bool:
            logger.warning(
                "Resultado de accion ignorado por exito no booleano: %s",
                type(exito).__name__,
            )

        if self.task_context is not None and self.task_context.debe_abandonar():
            task_transition = self._abandonar_tarea_locked(
                "Frustracion local alcanzo el umbral contractual."
            )

        self._actualizar_modificadores_globales()
        percepcion_contexto = percepcion or self._ultima_percepcion
        percepcion_resumida_contexto = percepcion_resumida or self._ultima_percepcion_resumida

        contexto = self.psychology.generar_contexto_llm(
            self.state_engine,
            self.personality,
            self.task_context,
            policy_offsets=self._offsets_politica,
            percepcion=percepcion_contexto or None,
            percepcion_resumida=percepcion_resumida_contexto,
        )
        self._actualizar_constelacion(contexto, percepcion_contexto, percepcion_resumida_contexto)
        constelacion_resumen = self._resumir_constelacion_texto()
        contexto["constelacion_resumen"] = constelacion_resumen

        respuesta = {
            "estado": self.state_engine.obtener_estado(),
            "personalidad": self.personality.obtener_rasgos(),
            "banderas": contexto["umbrales"],
            "narrativa": contexto["estado_narrativo"],
            "contexto_llm": contexto,
            "policy_activa": self.policy,
            "tarea": self.task_context.obtener_resumen() if self.task_context else None,
            "es_interrupcion_critica": tipo_evento == "alerta_dolor_fisico",
            "tipo_memoria": self._clasificar_tipo_memoria(tipo_evento, percepcion),
            "percepcion_resumida": percepcion_resumida_contexto,
            "constelacion_cognitiva": self._constelacion.a_dict(),
            "constelacion_resumen": constelacion_resumen,
            "task_transition": task_transition.to_dict(),
            "task_action_instance_id": self._task_action_instance_id(task_transition),
        }
        return copy.deepcopy(respuesta)

    def cambiar_policy(self, nombre: str):
        """Cambia la politica activa si es valida."""
        with self._owner_lock:
            with self.state_engine.atomic():
                if nombre in self.policies_disponibles:
                    self.policy = nombre
                    logger.info("Politica cambiada a '%s'", nombre)
                    self._actualizar_modificadores_globales()
                else:
                    logger.warning("Politica '%s' no reconocida. Usar: %s", nombre, self.policies_disponibles)

    def _policy_modifiers(self) -> dict:
        mods = {
            "confianza_offset": 0.0,
            "tolerancia_riesgo_offset": 0.0,
            "curiosidad_offset": 0.0,
            "agresividad_offset": 0.0,
            "motivacion_offset": 0.0,
            "foco_offset": 0.0,
            "carga_cognitiva_offset": 0.0,
            "tasa_estres_mult": 1.0,
            "tasa_energia_mult": 1.0,
            "tasa_confianza_mult": 1.0,
        }

        if self.policy == "cautelosa":
            mods["tolerancia_riesgo_offset"] = -25.0
            mods["confianza_offset"] = -10.0
            mods["tasa_estres_mult"] = 1.2
            mods["tasa_confianza_mult"] = 0.8
        elif self.policy == "exploradora":
            mods["curiosidad_offset"] = 20.0
            mods["tolerancia_riesgo_offset"] = 15.0
            mods["tasa_energia_mult"] = 1.1
        elif self.policy == "eficiente":
            mods["foco_offset"] = 10.0
            mods["carga_cognitiva_offset"] = -10.0
            mods["tasa_estres_mult"] = 0.9
            mods["motivacion_offset"] = 5.0
        elif self.policy == "social":
            mods["agresividad_offset"] = -10.0
            mods["confianza_offset"] = 5.0
            mods["tasa_estres_mult"] = 0.8
        elif self.policy == "autonoma":
            mods["confianza_offset"] = 12.0
            mods["tolerancia_riesgo_offset"] = 20.0
            mods["motivacion_offset"] = 8.0
            mods["foco_offset"] = 5.0
            mods["tasa_energia_mult"] = 1.1
            mods["tasa_confianza_mult"] = 1.1

        return mods

    def _actualizar_modificadores_globales(self):
        pers_mods = self.personality.get_modifiers()
        poli_mods = self._policy_modifiers()

        self._offsets_politica = {
            "confianza": poli_mods.get("confianza_offset", 0.0),
            "curiosidad": poli_mods.get("curiosidad_offset", 0.0),
            "tolerancia_riesgo": poli_mods.get("tolerancia_riesgo_offset", 0.0),
            "agresividad": poli_mods.get("agresividad_offset", 0.0),
            "motivacion": poli_mods.get("motivacion_offset", 0.0),
            "foco": poli_mods.get("foco_offset", 0.0),
            "carga_cognitiva": poli_mods.get("carga_cognitiva_offset", 0.0),
        }

        mods_engine = {
            "tasa_estres": pers_mods["tasa_estres"] * poli_mods.get("tasa_estres_mult", 1.0),
            "tasa_energia": pers_mods["tasa_energia"] * poli_mods.get("tasa_energia_mult", 1.0),
            "tasa_foco": pers_mods["tasa_foco"],
            "tasa_carga_cognitiva": pers_mods["tasa_carga_cognitiva"],
            "tasa_confianza": pers_mods["tasa_confianza"] * poli_mods.get("tasa_confianza_mult", 1.0),
            "tasa_curiosidad": pers_mods["tasa_curiosidad"],
            "tasa_motivacion": pers_mods["tasa_motivacion"],
            "tasa_agresividad": pers_mods["tasa_agresividad"],
            "tasa_tolerancia_riesgo": pers_mods["tasa_tolerancia_riesgo"],
            "amortiguador_estres": pers_mods["amortiguador_estres"],
        }
        self.state_engine.actualizar_modificadores(mods_engine)

    def obtener_contexto_llm(self) -> dict:
        """Wrapper para obtener el contexto listo para el LLM."""
        with self._owner_lock:
            with self.state_engine.atomic():
                contexto = self.psychology.generar_contexto_llm(
                    self.state_engine,
                    self.personality,
                    self.task_context,
                    policy_offsets=self._offsets_politica,
                    percepcion=self._ultima_percepcion or None,
                    percepcion_resumida=self._ultima_percepcion_resumida,
                )
                self._actualizar_constelacion(
                    contexto,
                    contexto.get("percepcion", {}),
                    contexto.get("percepcion_resumida", ""),
                )
                contexto["constelacion_resumen"] = self._resumir_constelacion_texto()
                return copy.deepcopy(contexto)

    def obtener_constelacion(self) -> dict[str, Any]:
        """Devuelve la constelacion cognitiva actual del agente."""
        with self._owner_lock:
            if self._constelacion.esta_vacia():
                self.obtener_contexto_llm()
            return copy.deepcopy(self._constelacion.a_dict())

    def obtener_constelacion_resumen(self) -> str:
        """Devuelve un resumen compacto de la constelacion actual."""
        with self._owner_lock:
            if self._constelacion.esta_vacia():
                self.obtener_contexto_llm()
            return self._resumir_constelacion_texto()

    def obtener_snapshot_audit(self) -> dict:
        """Lee solo estado/tarea, sin narrativa ni reevaluar detectores de tarea."""
        with self._owner_lock:
            with self.state_engine.atomic():
                tarea = self.task_context
                return {
                    "state": self.state_engine.obtener_estado(),
                    "task": {
                        "task_instance_id": tarea.task_instance_id,
                        "objetivo_id": tarea.objetivo_id,
                        "intentos_totales": tarea.intentos_totales,
                        "intentos_fallidos": tarea.intentos_fallidos,
                        "frustracion_local": tarea.frustracion_local,
                        "umbral_abandono": tarea.umbral_abandono,
                    } if tarea is not None else {},
                }

    def obtener_resumen(self) -> dict:
        """Devuelve un resumen completo del estado del agente."""
        with self._owner_lock:
            with self.state_engine.atomic():
                tarea = self.task_context.obtener_resumen() if self.task_context else None
                resumen = {
                    "estado": self.state_engine.obtener_estado(),
                    "personalidad": self.personality.obtener_rasgos(),
                    "tarea": tarea,
                    "policy": self.policy,
                    "narrativa": self.psychology.generar_resumen_narrativo(
                        self.state_engine,
                        self.personality,
                        self.task_context,
                        policy_offsets=self._offsets_politica,
                    ),
                    "constelacion_cognitiva": self.obtener_constelacion(),
                    "constelacion_resumen": self.obtener_constelacion_resumen(),
                }
                return copy.deepcopy(resumen)

    @staticmethod
    def _leer_intensidad(detalles: dict[str, Any]) -> float:
        valor = detalles.get("intensidad", 1.0)
        try:
            return float(valor)
        except (TypeError, ValueError):
            return 1.0

    def _resolver_evento_emocional(self, tipo_evento: str, detalles: dict[str, Any]) -> str:
        if tipo_evento == "resultado_accion":
            accion = str(detalles.get("accion", ""))
            exito = detalles.get("exito")
            if exito is True:
                if accion in self.ACCIONES_EXPLORATORIAS:
                    return "exploracion_exitosa"
                return "recompensa"
            if exito is False:
                if accion in self.ACCIONES_EXPLORATORIAS:
                    return "exploracion_fallida"
                return "fracaso"
        if tipo_evento == "comando_usuario":
            return "comando_usuario"
        return tipo_evento

    def _resultado_pertenece_a_tarea_actual(
        self,
        tipo_evento: str,
        detalles: dict[str, Any],
    ) -> bool:
        """Aplica outcomes correlacionados solo a la instancia de task de origen.

        Las llamadas internas legacy que no activan el enforcement conservan su
        contrato; produccion lo activa despues de resolver el action ledger.
        """
        if tipo_evento != "resultado_accion":
            return True
        if detalles.get("_task_correlation_enforced") is not True:
            return True
        expected = str(detalles.get("_task_instance_id_correlacion") or "").strip()
        return bool(
            expected
            and self.task_context is not None
            and self.task_context.task_instance_id == expected
        )

    def _sincronizar_tarea_desde_detalles(
        self,
        tipo_evento: str,
        detalles: dict[str, Any],
    ) -> TaskTransitionReceipt:
        """Consume sólo autoridad estructurada; nunca interpreta el mensaje libre."""
        parsed_intent = self._task_intent_desde_detalles(tipo_evento, detalles)
        if parsed_intent is None:
            return self._task_receipt_actual(
                TaskTransitionStatus.INVALID_TRANSITION,
                TaskIntent.NONE,
                reason="Task intent fuera de contrato.",
            )
        if parsed_intent is TaskIntent.ABANDON:
            return self._task_receipt_actual(
                TaskTransitionStatus.INVALID_TRANSITION,
                parsed_intent,
                reason="ABANDON sólo puede producirlo la policy interna.",
            )
        return self._aplicar_transicion_tarea_locked(
            parsed_intent,
            objetivo_id=self._extraer_objetivo_estructurado(detalles),
            expected_task_instance_id=self._extraer_task_instance_id(detalles),
            reason="Transicion solicitada por EventoEntrante.",
        )

    @staticmethod
    def _task_intent_desde_detalles(
        tipo_evento: str,
        detalles: dict[str, Any],
    ) -> TaskIntent | None:
        if tipo_evento == "tarea_completada" or detalles.get("objetivo_completado") is True:
            return TaskIntent.COMPLETE
        raw_intent = detalles.get("task_intent")
        if raw_intent is None or not str(raw_intent).strip():
            return TaskIntent.NONE
        try:
            return TaskIntent(str(raw_intent).strip().lower())
        except ValueError:
            return None

    @staticmethod
    def _extraer_objetivo_estructurado(detalles: dict[str, Any]) -> str | None:
        for key in ("task_objective", "objetivo_id", "objetivo"):
            value = detalles.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _extraer_task_instance_id(detalles: dict[str, Any]) -> str | None:
        value = detalles.get("task_instance_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _task_action_instance_id(receipt: TaskTransitionReceipt) -> str | None:
        if receipt.status in {
            TaskTransitionStatus.CREATED,
            TaskTransitionStatus.CONTINUED,
            TaskTransitionStatus.REPLACED,
        }:
            return receipt.task_instance_id
        return None

    def _task_receipt_actual(
        self,
        status: TaskTransitionStatus,
        intent: TaskIntent,
        *,
        reason: str = "",
    ) -> TaskTransitionReceipt:
        current = self.task_context
        return TaskTransitionReceipt(
            status=status,
            intent=intent,
            task_instance_id=current.task_instance_id if current else None,
            objetivo_id=current.objetivo_id if current else None,
            reason=reason,
        )

    def _aplicar_transicion_tarea_locked(
        self,
        intent: TaskIntent,
        *,
        objetivo_id: str | None = None,
        expected_task_instance_id: str | None = None,
        reason: str = "",
    ) -> TaskTransitionReceipt:
        current = self.task_context
        objective = str(objetivo_id or "").strip()
        expected_id = str(expected_task_instance_id or "").strip()

        if intent is TaskIntent.NONE:
            status = TaskTransitionStatus.UNCHANGED if current else TaskTransitionStatus.NO_TASK
            return self._task_receipt_actual(status, intent, reason="Sin transición solicitada.")

        if intent is TaskIntent.START:
            if current is not None or not objective:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    intent,
                    reason="START exige ausencia de task y objetivo no vacío.",
                )
            self.task_context = TaskContext(objective)
            logger.info("Nueva tarea iniciada: '%s'", objective)
            return self._task_receipt_actual(TaskTransitionStatus.CREATED, intent, reason=reason)

        if intent is TaskIntent.CONTINUE:
            if current is None:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    intent,
                    reason="CONTINUE exige una task activa.",
                )
            if expected_id and current.task_instance_id != expected_id:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    intent,
                    reason="CONTINUE no coincide con task_instance_id.",
                )
            if objective and current.objetivo_id != objective:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    intent,
                    reason="CONTINUE no puede cambiar el objetivo.",
                )
            return self._task_receipt_actual(TaskTransitionStatus.CONTINUED, intent, reason=reason)

        if intent is TaskIntent.REPLACE:
            if current is None or not objective:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    intent,
                    reason="REPLACE exige task activa y objetivo nuevo.",
                )
            if expected_id and current.task_instance_id != expected_id:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    intent,
                    reason="REPLACE no coincide con task_instance_id.",
                )
            if current.objetivo_id == objective:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    intent,
                    reason="REPLACE exige un objetivo conceptualmente distinto.",
                )
            previous_id = current.task_instance_id
            previous_objective = current.objetivo_id
            current.reset(objective)
            logger.info("Tarea reemplazada con nuevo objetivo: '%s'", objective)
            return TaskTransitionReceipt(
                status=TaskTransitionStatus.REPLACED,
                intent=intent,
                task_instance_id=current.task_instance_id,
                objetivo_id=current.objetivo_id,
                previous_task_instance_id=previous_id,
                previous_objetivo_id=previous_objective,
                reason=reason,
            )

        if intent in {TaskIntent.CANCEL, TaskIntent.COMPLETE}:
            if current is None:
                return TaskTransitionReceipt(
                    status=TaskTransitionStatus.NO_TASK,
                    intent=intent,
                    reason="No había task activa.",
                )
            if expected_id and current.task_instance_id != expected_id:
                return self._task_receipt_actual(
                    TaskTransitionStatus.INVALID_TRANSITION,
                    intent,
                    reason="La transición terminal no coincide con task_instance_id.",
                )
            terminal_id = current.task_instance_id
            terminal_objective = current.objetivo_id
            self.task_context = None
            status = (
                TaskTransitionStatus.CANCELLED
                if intent is TaskIntent.CANCEL
                else TaskTransitionStatus.COMPLETED
            )
            logger.info("Tarea %s y contexto liberado.", status.value)
            return TaskTransitionReceipt(
                status=status,
                intent=intent,
                task_instance_id=terminal_id,
                objetivo_id=terminal_objective,
                reason=reason,
            )

        return self._task_receipt_actual(
            TaskTransitionStatus.INVALID_TRANSITION,
            intent,
            reason="Transición no autorizada.",
        )

    def _abandonar_tarea_locked(self, reason: str) -> TaskTransitionReceipt:
        current = self.task_context
        if current is None:
            return TaskTransitionReceipt(
                status=TaskTransitionStatus.NO_TASK,
                intent=TaskIntent.ABANDON,
                reason="No había task activa.",
            )
        terminal_id = current.task_instance_id
        terminal_objective = current.objetivo_id
        self.task_context = None
        logger.info("Tarea abandonada por policy determinista.")
        return TaskTransitionReceipt(
            status=TaskTransitionStatus.ABANDONED,
            intent=TaskIntent.ABANDON,
            task_instance_id=terminal_id,
            objetivo_id=terminal_objective,
            reason=reason,
        )

    @staticmethod
    def _extraer_percepcion(detalles: dict[str, Any]) -> dict[str, Any]:
        claves_perceptivas = {
            "objetos_visibles",
            "resumen_percepcion",
            "objeto_mas_cercano",
            "cantidad_objetos_visibles",
            "posicion_lazzaro",
            "estado_fisico",
            "salud_avatar",
            "mensaje_critico",
        }
        return {clave: valor for clave, valor in detalles.items() if clave in claves_perceptivas}

    @staticmethod
    def _resumir_percepcion(percepcion: dict[str, Any]) -> str:
        if not percepcion:
            return ""

        lineas: list[str] = []
        posicion = percepcion.get("posicion_lazzaro")
        if isinstance(posicion, dict):
            x = posicion.get("x", 0)
            y = posicion.get("y", 0)
            z = posicion.get("z", 0)
            lineas.append(f"Posicion actual: x={x}, y={y}, z={z}.")

        cantidad = percepcion.get("cantidad_objetos_visibles")
        if isinstance(cantidad, (int, float)):
            lineas.append(f"Objetos visibles: {int(cantidad)}.")

        mas_cercano = percepcion.get("objeto_mas_cercano")
        if isinstance(mas_cercano, dict):
            nombre = mas_cercano.get("nombre", "desconocido")
            distancia = mas_cercano.get("distancia")
            if distancia is not None:
                lineas.append(f"Objeto mas cercano: {nombre} a {distancia} unidades.")
            else:
                lineas.append(f"Objeto mas cercano: {nombre}.")

        visibles = percepcion.get("objetos_visibles")
        if isinstance(visibles, str) and visibles.strip():
            lineas.append(f"Resumen visual: {visibles.strip()}")
        elif isinstance(visibles, list) and visibles:
            muestras = []
            for objeto in visibles[:3]:
                if not isinstance(objeto, dict):
                    continue
                nombre = objeto.get("nombre", "objeto")
                distancia = objeto.get("distancia")
                if distancia is None:
                    muestras.append(str(nombre))
                else:
                    muestras.append(f"{nombre} ({distancia})")
            if muestras:
                lineas.append("Muestras del entorno: " + ", ".join(muestras) + ".")

        resumen_percepcion = percepcion.get("resumen_percepcion")
        if isinstance(resumen_percepcion, str) and resumen_percepcion.strip():
            if resumen_percepcion.strip() not in " ".join(lineas):
                lineas.append(f"Resumen perceptivo: {resumen_percepcion.strip()}")

        estado_fisico = percepcion.get("estado_fisico")
        if estado_fisico:
            lineas.append(f"Estado fisico: {estado_fisico}.")

        mensaje_critico = percepcion.get("mensaje_critico")
        if mensaje_critico:
            lineas.append(f"Alerta: {mensaje_critico}.")

        return " ".join(lineas)

    def _clasificar_tipo_memoria(self, tipo_evento: str, percepcion: dict[str, Any]) -> str:
        if tipo_evento in self.EVENTOS_PERCEPTIVOS and percepcion:
            return "percepcion_entorno"
        if tipo_evento == "resultado_accion":
            return "resultado_accion"
        return tipo_evento

    def _actualizar_constelacion(
        self,
        contexto: dict[str, Any],
        percepcion: dict[str, Any],
        percepcion_resumida: str,
    ) -> None:
        tarea = self.task_context.obtener_resumen() if self.task_context else None
        estado = contexto.get("valores") or self.state_engine.obtener_estado()
        rasgos = self.personality.obtener_rasgos()
        banderas = contexto.get("umbrales", {})

        constelacion = ConstelacionCognitiva.desde_estado_bruto(
            estado=estado,
            rasgos=rasgos,
            banderas=banderas,
            contexto={
                "policy_activa": self.policy,
                "estado_narrativo": contexto.get("estado_narrativo", ""),
                "percepcion_resumida": percepcion_resumida,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            nombre="lazzaro_cognitiva",
        )

        constelacion.crear_nodo(
            nombre="meta:policy_activa",
            categoria="meta",
            valor=self.policy,
            descripcion="Politica conductual activa.",
        )

        if tarea:
            objetivo = tarea.get("objetivo") or tarea.get("objetivo_id")
            if objetivo:
                constelacion.crear_nodo(
                    nombre="tarea:objetivo_actual",
                    categoria="tarea",
                    valor=str(objetivo),
                    descripcion="Objetivo principal vigente.",
                )
            constelacion.crear_nodo(
                nombre="tarea:intentos",
                categoria="tarea",
                valor=int(tarea.get("intentos_totales", 0)),
                descripcion="Cantidad de intentos acumulados en la tarea actual.",
            )
            constelacion.crear_nodo(
                nombre="tarea:fallos",
                categoria="tarea",
                valor=int(tarea.get("intentos_fallidos", 0)),
                descripcion="Cantidad de intentos fallidos.",
            )
            constelacion.crear_nodo(
                nombre="tarea:frustracion_local",
                categoria="tarea",
                valor=float(tarea.get("frustracion_local", 0.0)),
                descripcion="Frustracion asociada al objetivo activo.",
            )
            self._conectar_si_existe(constelacion, "meta:policy_activa", "tarea:objetivo_actual", "modula")
            self._conectar_si_existe(constelacion, "estado:motivacion", "tarea:objetivo_actual", "sostiene")
            self._conectar_si_existe(constelacion, "estado:foco", "tarea:objetivo_actual", "orienta")
            self._conectar_si_existe(
                constelacion,
                "tarea:frustracion_local",
                "bandera:debe_cambiar_estrategia",
                "activa_si_aumenta",
            )

        if percepcion_resumida:
            constelacion.crear_nodo(
                nombre="percepcion:resumen",
                categoria="percepcion",
                valor=percepcion_resumida,
                descripcion="Resumen legible del entorno actual.",
            )
            self._conectar_si_existe(constelacion, "estado:curiosidad", "percepcion:resumen", "atiende")

        cantidad_objetos = percepcion.get("cantidad_objetos_visibles")
        if isinstance(cantidad_objetos, (int, float)):
            constelacion.crear_nodo(
                nombre="percepcion:cantidad_objetos_visibles",
                categoria="percepcion",
                valor=int(cantidad_objetos),
                descripcion="Cantidad de objetos detectados en el entorno.",
            )

        objeto_cercano = percepcion.get("objeto_mas_cercano")
        if isinstance(objeto_cercano, dict):
            nombre_objeto = str(objeto_cercano.get("nombre", "desconocido"))
            constelacion.crear_nodo(
                nombre="percepcion:objeto_mas_cercano",
                categoria="percepcion",
                valor=nombre_objeto,
                descripcion="Objeto detectado con menor distancia relativa.",
                metadata={"distancia": objeto_cercano.get("distancia")},
            )
            self._conectar_si_existe(
                constelacion,
                "percepcion:objeto_mas_cercano",
                "tarea:objetivo_actual",
                "puede_ser_relevante_para",
            )

        estado_fisico = percepcion.get("estado_fisico") or percepcion.get("salud_avatar")
        if estado_fisico is not None:
            constelacion.crear_nodo(
                nombre="percepcion:estado_fisico",
                categoria="percepcion",
                valor=str(estado_fisico),
                descripcion="Estado fisico reportado por el cliente.",
            )
            self._conectar_si_existe(constelacion, "estado:estres", "percepcion:estado_fisico", "reacciona_a")

        if banderas.get("dolor_fisico_critico"):
            self._conectar_si_existe(
                constelacion,
                "bandera:dolor_fisico_critico",
                "tarea:objetivo_actual",
                "interrumpe",
                peso=1.4,
            )
            self._conectar_si_existe(
                constelacion,
                "bandera:dolor_fisico_critico",
                "percepcion:estado_fisico",
                "prioriza_seguridad",
                peso=1.4,
            )

        self._constelacion = constelacion

    def _resumir_constelacion_texto(self) -> str:
        if self._constelacion.esta_vacia():
            return ""

        partes: list[str] = []
        politica = self._valor_nodo("meta:policy_activa")
        if politica:
            partes.append(f"Politica activa: {politica}.")

        objetivo = self._valor_nodo("tarea:objetivo_actual")
        if objetivo:
            partes.append(f"Objetivo vigente: {objetivo}.")

        objetos_visibles = self._valor_nodo("percepcion:cantidad_objetos_visibles")
        if objetos_visibles is not None:
            partes.append(f"Entorno detectado con {objetos_visibles} objetos visibles.")

        objeto_cercano = self._valor_nodo("percepcion:objeto_mas_cercano")
        if objeto_cercano:
            partes.append(f"Objeto mas cercano: {objeto_cercano}.")

        if self._valor_nodo("bandera:dolor_fisico_critico") is True:
            partes.append("Dolor fisico critico activo; la seguridad tiene prioridad inmediata.")

        if not partes:
            return self._constelacion.resumen()
        return " ".join(partes)

    def _valor_nodo(self, nombre: str) -> Any:
        nodo = self._constelacion.obtener_nodo(nombre)
        return nodo.valor if nodo is not None else None

    @staticmethod
    def _conectar_si_existe(
        constelacion: ConstelacionCognitiva,
        origen: str,
        destino: str,
        relacion: str,
        peso: float = 1.0,
    ) -> None:
        if constelacion.obtener_nodo(origen) is None or constelacion.obtener_nodo(destino) is None:
            return
        constelacion.conectar(origen, destino, relacion, peso=peso)


behavior_core = BehaviorCore()
