"""
behavior_core.py - Capa 3 del sistema de comportamiento de Lazzaro.
Fachada principal que integra StateEngine, PersonalityTraits, TaskContext y PsychologyEngine.
Expone métodos para el servicio orquestador y gestiona las policies como modificadores dinámicos.
"""

from core.logger import get_logger
from core.state_engine import StateEngine
from core.personality import PersonalityTraits
from core.task_context import TaskContext
from core.psychology import PsychologyEngine

logger = get_logger(__name__)


class BehaviorCore:
    """
    Orquestador interno del agente.
    Mantiene el estado, la personalidad, el contexto de tarea actual y la política activa.
    """

    def __init__(self):
        # Componentes centrales
        self.state_engine = StateEngine()
        self.personality = PersonalityTraits()
        self.psychology = PsychologyEngine()

        # Contexto de tarea (puede ser None si no hay objetivo)
        self.task_context = None

        # Política actual
        self.policy = "exploradora"  # por defecto
        self.policies_disponibles = ["cautelosa", "exploradora", "eficiente", "social"]

        # Aplicar modificadores iniciales
        self._actualizar_modificadores_globales()

    # ------------------------------------------------------------------
    # Gestión de tareas
    # ------------------------------------------------------------------

    def iniciar_tarea(self, objetivo_id: str):
        """Crea o resetea el TaskContext para un nuevo objetivo."""
        if self.task_context is None:
            self.task_context = TaskContext(objetivo_id)
            logger.info(f"Nueva tarea iniciada: '{objetivo_id}'")
        else:
            self.task_context.reset(objetivo_id)
            logger.info(f"Tarea reiniciada con nuevo objetivo: '{objetivo_id}'")

    def finalizar_tarea(self, exito: bool):
        """Finaliza la tarea actual y opcionalmente genera un evento de cierre."""
        if self.task_context is not None:
            # Opcional: podríamos generar un evento de "tarea_completada" o "fracaso"
            if exito:
                self.procesar_evento("tarea_completada", {"intensidad": 1.0})
            else:
                self.procesar_evento("fracaso", {"intensidad": 1.0})
            self.task_context = None
            logger.info("Tarea finalizada y contexto liberado.")

    # ------------------------------------------------------------------
    # Procesamiento de eventos
    # ------------------------------------------------------------------

    def procesar_evento(self, tipo_evento: str, detalles: dict | None = None) -> dict:
        """
        Recibe un evento externo y actualiza todo el sistema.
        detalles puede contener:
            - 'intensidad': float (1.0 por defecto)
            - 'accion': str (si el evento es resultado de una herramienta)
            - 'exito': bool (para registrar en task_context)
        Retorna un diccionario con el nuevo estado, banderas de comportamiento y contexto LLM.
        """
        if detalles is None:
            detalles = {}
        intensidad = detalles.get("intensidad", 1.0)
        accion = detalles.get("accion", None)
        exito = detalles.get("exito", None)

        logger.info(f"Procesando evento: '{tipo_evento}' intensidad={intensidad}")

        # 1. Actualizar estado emocional crudo
        self.state_engine.aplicar_evento(tipo_evento, intensidad)

        # 2. Actualizar personalidad (si el evento es relevante)
        self.personality.update_from_event(tipo_evento, intensidad)

        # 3. Si hay una tarea activa y se proporciona acción/éxito, registrar en TaskContext
        if self.task_context is not None and accion is not None and exito is not None:
            self.task_context.registrar_intento(accion, exito)
            # Ajustar umbral de abandono según la paciencia actual
            self.task_context.actualizar_umbral_abandono(self.personality.paciencia)
            # Si se detectan patrones, generar eventos adicionales automáticamente
            if self.task_context.detectar_loop():
                self.state_engine.aplicar_evento("loop_detectado", 1.0)
            if self.task_context.detectar_estancamiento():
                self.state_engine.aplicar_evento("fracaso", 0.5)  # leve refuerzo de frustración
            if self.task_context.detectar_compulsion():
                self.state_engine.aplicar_evento("sobrecarga", 0.3)

        # 4. Recalcular modificadores globales (personalidad + política)
        self._actualizar_modificadores_globales()

        # 5. Obtener interpretaciones y banderas
        contexto = self.psychology.generar_contexto_llm(
            self.state_engine,
            self.personality,
            self.task_context
        )

        # 6. Construir respuesta
        respuesta = {
            "estado": self.state_engine.obtener_estado(),
            "personalidad": self.personality.obtener_rasgos(),
            "banderas": contexto["umbrales"],
            "narrativa": contexto["estado_narrativo"],
            "contexto_llm": contexto,
            "policy_activa": self.policy,
            "tarea": self.task_context.obtener_resumen() if self.task_context else None,
        }
        return respuesta

    # ------------------------------------------------------------------
    # Sistema de políticas (modificadores)
    # ------------------------------------------------------------------

    def cambiar_policy(self, nombre: str):
        """Cambia la política activa si es válida."""
        if nombre in self.policies_disponibles:
            self.policy = nombre
            logger.info(f"Política cambiada a '{nombre}'")
            self._actualizar_modificadores_globales()
        else:
            logger.warning(f"Política '{nombre}' no reconocida. Usar: {self.policies_disponibles}")

    def _policy_modifiers(self) -> dict:
        """
        Devuelve un diccionario con los modificadores asociados a la política actual.
        Estos se suman/multiplican a los modificadores de personalidad.
        """
        # Modificadores base (todos 1.0 = sin efecto)
        mods = {
            "confianza_offset": 0.0,          # desplazamiento directo (suma)
            "tolerancia_riesgo_offset": 0.0,
            "curiosidad_offset": 0.0,
            "agresividad_offset": 0.0,
            "motivacion_offset": 0.0,
            "foco_offset": 0.0,
            "carga_cognitiva_offset": 0.0,
            "tasa_estres_mult": 1.0,         # multiplicador de tasa de cambio de estrés
            "tasa_energia_mult": 1.0,
            "tasa_confianza_mult": 1.0,
        }

        if self.policy == "cautelosa":
            mods["tolerancia_riesgo_offset"] = -25.0
            mods["confianza_offset"] = -10.0
            mods["tasa_estres_mult"] = 1.2    # el estrés sube más (más precavido)
            mods["tasa_confianza_mult"] = 0.8 # la confianza cambia más lentamente
        elif self.policy == "exploradora":
            mods["curiosidad_offset"] = 20.0
            mods["tolerancia_riesgo_offset"] = 15.0
            mods["tasa_energia_mult"] = 1.1   # gasta un poco más de energía explorando
        elif self.policy == "eficiente":
            mods["foco_offset"] = 10.0
            mods["carga_cognitiva_offset"] = -10.0
            mods["tasa_estres_mult"] = 0.9    # menos estrés al trabajar ordenado
            mods["motivacion_offset"] = 5.0   # motivación extra por productividad
        elif self.policy == "social":
            mods["agresividad_offset"] = -10.0
            mods["confianza_offset"] = 5.0
            mods["tasa_estres_mult"] = 0.8    # el apoyo social reduce estrés
            # La sociabilidad de personalidad también se potenciará indirectamente

        return mods

    def _actualizar_modificadores_globales(self):
        """
        Combina los modificadores de personalidad y política,
        y los inyecta en el StateEngine.
        """
        # Obtener modificadores desde la personalidad
        pers_mods = self.personality.get_modifiers()

        # Obtener modificadores de la política
        poli_mods = self._policy_modifiers()

        # Construir diccionario final para StateEngine
        # Los offsets de política se aplican directamente a las variables (StateEngine los aplicará en tick/aplicar_evento)
        # Pero StateEngine espera modificadores que multiplican tasas, no offsets directos.
        # Para integrar offsets sin romper la interfaz, podemos almacenar los offsets aparte y aplicarlos en procesar_evento o en un método dedicado.
        # Solución: guardar los offsets en self._offsets_politica y aplicarlos después de cada actualización importante.
        self._offsets_politica = {
            "confianza": poli_mods.get("confianza_offset", 0.0),
            "curiosidad": poli_mods.get("curiosidad_offset", 0.0),
            "tolerancia_riesgo": poli_mods.get("tolerancia_riesgo_offset", 0.0),
            "agresividad": poli_mods.get("agresividad_offset", 0.0),
            "motivacion": poli_mods.get("motivacion_offset", 0.0),
            "foco": poli_mods.get("foco_offset", 0.0),
            "carga_cognitiva": poli_mods.get("carga_cognitiva_offset", 0.0),
        }

        # Construir el diccionario de tasas que StateEngine acepta (multiplicadores)
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

        # Aplicar offsets de política al state_engine (suma directa a las variables)
        self._aplicar_offsets_politica()

    def _aplicar_offsets_politica(self):
        """Suma los offsets de la política actual a las variables conductuales y homeostáticas."""
        off = self._offsets_politica
        self.state_engine.confianza += off["confianza"]
        self.state_engine.curiosidad += off["curiosidad"]
        self.state_engine.tolerancia_riesgo += off["tolerancia_riesgo"]
        self.state_engine.agresividad += off["agresividad"]
        self.state_engine.motivacion += off["motivacion"]
        self.state_engine.foco += off["foco"]
        self.state_engine.carga_cognitiva += off["carga_cognitiva"]
        self.state_engine._limitar()  # respetar rangos

    # ------------------------------------------------------------------
    # Métodos de consulta rápida
    # ------------------------------------------------------------------

    def obtener_contexto_llm(self) -> dict:
        """Wrapper para obtener el contexto listo para Gemini."""
        return self.psychology.generar_contexto_llm(
            self.state_engine,
            self.personality,
            self.task_context
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
                self.state_engine, self.personality, self.task_context
            ),
        }


# Instancia global para usar desde main.py y servicios
behavior_core = BehaviorCore()