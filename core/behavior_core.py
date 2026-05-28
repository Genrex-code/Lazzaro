"""
behavior_core.py - Capa 3 del sistema de comportamiento de Lazzaro.
Fachada principal que integra StateEngine, PersonalityTraits, TaskContext y PsychologyEngine.
"""

import re
from typing import Any

from core.logger import get_logger
from core.personality import PersonalityTraits
from core.psychology import PsychologyEngine
from core.state_engine import StateEngine
from core.task_context import TaskContext

logger = get_logger(__name__)


class BehaviorCore:
    """
    Orquestador interno del agente.
    Mantiene el estado, la personalidad, el contexto de tarea actual y la politica activa.
    """

    EVENTOS_CON_RESULTADO = {"resultado_accion", "alerta_dolor_fisico"}
    EVENTOS_PERCEPTIVOS = {"inicio_simulacion", "actualizacion_estado", "percepcion_entorno"}
    ACCIONES_EXPLORATORIAS = {
        "moverse_entorno_3d",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "soltar_objeto",
        "aplicar_fuerza",
    }

    def __init__(self):
        self.state_engine = StateEngine()
        self.personality = PersonalityTraits()
        self.psychology = PsychologyEngine()

        self.task_context: TaskContext | None = None

        self.policy = "exploradora"
        self.policies_disponibles = ["cautelosa", "exploradora", "eficiente", "social", "autonoma"]
        self._offsets_politica: dict[str, float] = {}
        self._actualizar_modificadores_globales()

    def iniciar_tarea(self, objetivo_id: str):
        """Crea o resetea el TaskContext para un nuevo objetivo."""
        if self.task_context is None:
            self.task_context = TaskContext(objetivo_id)
            logger.info("Nueva tarea iniciada: '%s'", objetivo_id)
        else:
            self.task_context.reset(objetivo_id)
            logger.info("Tarea reiniciada con nuevo objetivo: '%s'", objetivo_id)

    def finalizar_tarea(self, exito: bool):
        """Finaliza la tarea actual y genera un evento de cierre."""
        if self.task_context is None:
            return

        if exito:
            self.procesar_evento("tarea_completada", {"intensidad": 1.0})
        else:
            self.procesar_evento("fracaso", {"intensidad": 1.0})
        self.task_context = None
        logger.info("Tarea finalizada y contexto liberado.")

    def procesar_evento(self, tipo_evento: str, detalles: dict | None = None) -> dict:
        """
        Recibe un evento externo y actualiza todo el sistema.
        Retorna un diccionario con el nuevo estado, banderas y contexto LLM.
        """
        if detalles is None:
            detalles = {}

        intensidad = self._leer_intensidad(detalles)
        accion = detalles.get("accion")
        exito = detalles.get("exito")
        evento_emocional = self._resolver_evento_emocional(tipo_evento, detalles)

        self._sincronizar_tarea_desde_detalles(tipo_evento, detalles)
        percepcion = self._extraer_percepcion(detalles)
        percepcion_resumida = self._resumir_percepcion(percepcion)

        logger.info("Procesando evento: '%s' intensidad=%s", tipo_evento, intensidad)

        self.state_engine.aplicar_evento(evento_emocional, intensidad, contexto=detalles)
        self.personality.update_from_event(evento_emocional, intensidad)

        if self.task_context is not None and accion is not None and exito is not None:
            self.task_context.registrar_intento(str(accion), bool(exito))
            self.task_context.actualizar_umbral_abandono(self.personality.paciencia)
            if self.task_context.detectar_loop():
                self.procesar_evento("loop_detectado", {"intensidad": 1.0})
            if self.task_context.detectar_estancamiento():
                self.procesar_evento("fracaso", {"intensidad": 0.5})
            if self.task_context.detectar_compulsion():
                self.procesar_evento("sobrecarga", {"intensidad": 0.3})

        self._actualizar_modificadores_globales()

        contexto = self.psychology.generar_contexto_llm(
            self.state_engine,
            self.personality,
            self.task_context,
            policy_offsets=self._offsets_politica,
            percepcion=percepcion or None,
            percepcion_resumida=percepcion_resumida,
        )

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
            "percepcion_resumida": percepcion_resumida,
        }
        return respuesta

    def cambiar_policy(self, nombre: str):
        """Cambia la politica activa si es valida."""
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
        return self.psychology.generar_contexto_llm(
            self.state_engine,
            self.personality,
            self.task_context,
            policy_offsets=self._offsets_politica,
        )

    def obtener_resumen(self) -> dict:
        """Devuelve un resumen completo del estado del agente."""
        tarea = self.task_context.obtener_resumen() if self.task_context else None
        return {
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
        }

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

    def _sincronizar_tarea_desde_detalles(self, tipo_evento: str, detalles: dict[str, Any]) -> None:
        objetivo = self._extraer_objetivo_desde_detalles(tipo_evento, detalles)
        if objetivo:
            objetivo_actual = self.task_context.objetivo_id if self.task_context else None
            if objetivo_actual != objetivo:
                self.iniciar_tarea(objetivo)

        if self.task_context is not None and detalles.get("objetivo_completado") is True:
            self.finalizar_tarea(exito=True)

    def _extraer_objetivo_desde_detalles(self, tipo_evento: str, detalles: dict[str, Any]) -> str | None:
        mensaje = str(detalles.get("mensaje", "")).strip()
        if not mensaje:
            return None

        match = re.search(
            r"objetivo(?:\s+actual|\s+prioritario)?\s*:\s*(.+)",
            mensaje,
            re.IGNORECASE,
        )
        if match:
            objetivo = match.group(1).strip()
            objetivo = re.split(r"\bREGLAS?\b", objetivo, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            return objetivo.rstrip(".")

        if tipo_evento == "comando_usuario":
            return mensaje

        return None

    @staticmethod
    def _extraer_percepcion(detalles: dict[str, Any]) -> dict[str, Any]:
        claves_perceptivas = {
            "objetos_visibles",
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
        if isinstance(visibles, list) and visibles:
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


behavior_core = BehaviorCore()
