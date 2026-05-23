"""
psychology.py - Capa 2 (interpretación) del sistema de comportamiento de Lazzaro.
Convierte los valores crudos de StateEngine y PersonalityTraits en etiquetas,
umbrales de comportamiento y un resumen narrativo determinista.
No utiliza ningún modelo de lenguaje; todo se basa en reglas condicionales.
"""

from core.logger import get_logger

logger = get_logger(__name__)


class PsychologyEngine:
    """
    Interpreta el estado interno del agente y genera descripciones, banderas
    de comportamiento y el contexto que se inyectará al LLM (Gemini).
    """

    # --- Mapeo de valores conductuales a etiquetas ---

    @staticmethod
    def etiqueta_confianza(valor: float) -> str:
        if valor < -60:
            return "paranoico"
        elif valor < -20:
            return "inseguro"
        elif valor <= 20:
            return "neutral"
        elif valor <= 60:
            return "seguro"
        else:
            return "sobreconfiado"

    @staticmethod
    def etiqueta_curiosidad(valor: float) -> str:
        if valor < -60:
            return "apático"
        elif valor < -20:
            return "desinteresado"
        elif valor <= 20:
            return "curiosidad normal"
        elif valor <= 60:
            return "curioso"
        else:
            return "hipercurioso"

    @staticmethod
    def etiqueta_motivacion(valor: float) -> str:
        if valor < -60:
            return "anhedónico"
        elif valor < -20:
            return "desmotivado"
        elif valor <= 20:
            return "motivación normal"
        elif valor <= 60:
            return "motivado"
        else:
            return "obsesivo"

    @staticmethod
    def etiqueta_agresividad(valor: float) -> str:
        if valor < -60:
            return "sumiso"
        elif valor < -20:
            return "pasivo"
        elif valor <= 20:
            return "asertivo"
        elif valor <= 60:
            return "enérgico"
        else:
            return "hostil"

    @staticmethod
    def etiqueta_tolerancia_riesgo(valor: float) -> str:
        if valor < -60:
            return "averso al riesgo"
        elif valor < -20:
            return "cauteloso"
        elif valor <= 20:
            return "riesgo equilibrado"
        elif valor <= 60:
            return "arriesgado"
        else:
            return "temerario"

    # --- Umbrales de comportamiento ---

    @staticmethod
    def evaluar_umbrales(state_engine, task_ctx=None) -> dict:
        """
        Evalúa el estado actual y decide si se deben activar conductas especiales.
        Retorna un diccionario con flags booleanas o valores de intensidad.
        """
        flags = {
            "debe_pedir_ayuda": False,
            "debe_cambiar_estrategia": False,
            "debe_abandonar": False,
            "debe_descansar": False,
            "debe_explorar": False,
            "debe_ser_precavido": False,
            "esta_bloqueado": False,
        }

        # Estado general
        estres = state_engine.estres
        energia = state_engine.energia
        foco_efectivo = state_engine.foco_efectivo
        confianza = state_engine.confianza

        # Banderas basadas en umbrales fijos (luego pueden ser dinámicas con personalidad)
        if estres > 75 or energia < 20:
            flags["debe_descansar"] = True

        if confianza < -50:
            flags["debe_pedir_ayuda"] = True

        if foco_efectivo < 30:
            flags["debe_ser_precavido"] = True  # Alta probabilidad de error

        # Tarea específica (si hay task_context)
        if task_ctx is not None:
            frustracion = task_ctx.frustracion_local
            umbral_abandono = task_ctx.umbral_abandono
            if frustracion >= umbral_abandono:
                flags["debe_abandonar"] = True
            elif frustracion > umbral_abandono * 0.7:
                flags["debe_cambiar_estrategia"] = True

            # Detecciones de patrones
            if task_ctx.detectar_loop() or task_ctx.detectar_estancamiento():
                flags["debe_cambiar_estrategia"] = True
                flags["esta_bloqueado"] = True
            if task_ctx.detectar_compulsion():
                flags["debe_cambiar_estrategia"] = True

        # Curiosidad alta + energía suficiente puede sugerir exploración
        if state_engine.curiosidad > 50 and energia > 40:
            flags["debe_explorar"] = True

        return flags

    # --- Resumen narrativo ---

    @staticmethod
    def generar_resumen_narrativo(state_engine, personality_traits=None, task_ctx=None) -> str:
        """
        Produce un texto descriptivo del estado interno de Lazzaro, adecuado
        para ser leído por humanos o inyectado como contexto emocional en el prompt.
        """
        # Estado emocional general
        estres = state_engine.estres
        energia = state_engine.energia
        foco = state_engine.foco_efectivo
        confianza_etq = PsychologyEngine.etiqueta_confianza(state_engine.confianza)
        curiosidad_etq = PsychologyEngine.etiqueta_curiosidad(state_engine.curiosidad)
        motivacion_etq = PsychologyEngine.etiqueta_motivacion(state_engine.motivacion)
        agresividad_etq = PsychologyEngine.etiqueta_agresividad(state_engine.agresividad)
        riesgo_etq = PsychologyEngine.etiqueta_tolerancia_riesgo(state_engine.tolerancia_riesgo)

        lineas = []
        lineas.append(f"Lazzaro se siente {PsychologyEngine._describir_estres(estres)}.")
        lineas.append(f"Tiene {PsychologyEngine._describir_energia(energia)}.")
        lineas.append(f"Su nivel de atención es {PsychologyEngine._describir_foco(foco)}.")
        lineas.append(f"Confianza: {confianza_etq}.")
        lineas.append(f"Curiosidad: {curiosidad_etq}.")
        lineas.append(f"Motivación: {motivacion_etq}.")
        lineas.append(f"Agresividad: {agresividad_etq}.")
        lineas.append(f"Tolerancia al riesgo: {riesgo_etq}.")

        # Rasgos de personalidad (si se proporcionan)
        if personality_traits is not None:
            rasgos = personality_traits.obtener_rasgos()
            linea_rasgos = "Rasgos: " + ", ".join(
                f"{k}={v:.2f}" for k, v in rasgos.items()
            )
            lineas.append(linea_rasgos)

        # Información de la tarea (si hay)
        if task_ctx is not None and task_ctx.objetivo_id:
            resumen_tarea = (
                f"Objetivo actual: '{task_ctx.objetivo_id}'. "
                f"Intentos: {task_ctx.intentos_totales}, Fallos: {task_ctx.intentos_fallidos}. "
                f"Frustración local: {task_ctx.frustracion_local:.1f}/100 (umbral de abandono: {task_ctx.umbral_abandono:.1f})."
            )
            lineas.append(resumen_tarea)
            if task_ctx.detectar_loop():
                lineas.append("Se ha detectado un bucle de acciones repetidas.")
            if task_ctx.detectar_estancamiento():
                lineas.append("Hay estancamiento: muchos fallos consecutivos.")
            if task_ctx.detectar_compulsion():
                lineas.append("Se ha detectado compulsión por repetir una misma acción.")

        return " ".join(lineas)

    @staticmethod
    def generar_contexto_llm(state_engine, personality_traits=None, task_ctx=None) -> dict:
        """
        Construye un diccionario con información estructurada para inyectar en el prompt
        de Gemini. Incluye tanto el resumen narrativo como datos numéricos clave.
        """
        contexto = {
            "estado_narrativo": PsychologyEngine.generar_resumen_narrativo(
                state_engine, personality_traits, task_ctx
            ),
            "valores": state_engine.obtener_estado(),
            "umbrales": PsychologyEngine.evaluar_umbrales(state_engine, task_ctx),
        }
        if personality_traits is not None:
            contexto["personalidad"] = personality_traits.obtener_rasgos()
        if task_ctx is not None:
            contexto["tarea"] = task_ctx.obtener_resumen()
        return contexto

    # --- Helpers descriptivos ---

    @staticmethod
    def _describir_estres(valor: float) -> str:
        if valor < 20:
            return "relajado"
        elif valor < 50:
            return "ligeramente tenso"
        elif valor < 75:
            return "estresado"
        else:
            return "muy estresado y abrumado"

    @staticmethod
    def _describir_energia(valor: float) -> str:
        if valor < 20:
            return "muy poca energía, casi agotado"
        elif valor < 50:
            return "energía moderada, algo cansado"
        elif valor < 80:
            return "buena energía"
        else:
            return "mucha energía, enérgico"

    @staticmethod
    def _describir_foco(valor: float) -> str:
        if valor < 30:
            return "muy disperso y con dificultad para concentrarse"
        elif valor < 60:
            return "atención aceptable pero con lapsos"
        else:
            return "atención alta y enfocada"