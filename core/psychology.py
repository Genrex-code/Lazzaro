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

    @staticmethod
    def _clamp(valor: float, minimo: float, maximo: float) -> float:
        return max(minimo, min(maximo, valor))

    @staticmethod
    def obtener_estado_efectivo(state_engine, policy_offsets: dict | None = None) -> dict:
        """
        Combina el estado crudo con los offsets de politica sin mutar el StateEngine.
        Esta vista derivada se usa solo para interpretacion y contexto narrativo.
        """
        estado = {
            "energia": state_engine.energia,
            "estres": state_engine.estres,
            "foco": state_engine.foco,
            "carga_cognitiva": state_engine.carga_cognitiva,
            "confianza": state_engine.confianza,
            "curiosidad": state_engine.curiosidad,
            "motivacion": state_engine.motivacion,
            "agresividad": state_engine.agresividad,
            "tolerancia_riesgo": state_engine.tolerancia_riesgo,
        }

        if policy_offsets:
            for clave, valor in policy_offsets.items():
                if clave in estado:
                    estado[clave] += valor

        estado["energia"] = PsychologyEngine._clamp(estado["energia"], 0.0, 100.0)
        estado["estres"] = PsychologyEngine._clamp(estado["estres"], 0.0, 100.0)
        estado["foco"] = PsychologyEngine._clamp(estado["foco"], 0.0, 100.0)
        estado["carga_cognitiva"] = PsychologyEngine._clamp(estado["carga_cognitiva"], 0.0, 100.0)
        estado["confianza"] = PsychologyEngine._clamp(estado["confianza"], -100.0, 100.0)
        estado["curiosidad"] = PsychologyEngine._clamp(estado["curiosidad"], -100.0, 100.0)
        estado["motivacion"] = PsychologyEngine._clamp(estado["motivacion"], -100.0, 100.0)
        estado["agresividad"] = PsychologyEngine._clamp(estado["agresividad"], -100.0, 100.0)
        estado["tolerancia_riesgo"] = PsychologyEngine._clamp(estado["tolerancia_riesgo"], -100.0, 100.0)
        estado["foco_efectivo"] = PsychologyEngine._calcular_foco_efectivo(estado)

        return {clave: round(valor, 2) for clave, valor in estado.items()}

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
    def evaluar_umbrales(state_engine, task_ctx=None, policy_offsets: dict | None = None) -> dict:
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

        estado = PsychologyEngine.obtener_estado_efectivo(state_engine, policy_offsets)

        # Estado general
        estres = estado["estres"]
        energia = estado["energia"]
        foco_efectivo = estado["foco_efectivo"]
        confianza = estado["confianza"]

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
        if estado["curiosidad"] > 50 and energia > 40:
            flags["debe_explorar"] = True

        return flags

    # --- Resumen narrativo ---

    @staticmethod
    def generar_resumen_narrativo(
        state_engine,
        personality_traits=None,
        task_ctx=None,
        policy_offsets: dict | None = None,
    ) -> str:
        """
        Produce un texto descriptivo del estado interno de Lazzaro, adecuado
        para ser leído por humanos o inyectado como contexto emocional en el prompt.
        """
        estado = PsychologyEngine.obtener_estado_efectivo(state_engine, policy_offsets)

        # Estado emocional general
        estres = estado["estres"]
        energia = estado["energia"]
        foco = estado["foco_efectivo"]
        confianza_etq = PsychologyEngine.etiqueta_confianza(estado["confianza"])
        curiosidad_etq = PsychologyEngine.etiqueta_curiosidad(estado["curiosidad"])
        motivacion_etq = PsychologyEngine.etiqueta_motivacion(estado["motivacion"])
        agresividad_etq = PsychologyEngine.etiqueta_agresividad(estado["agresividad"])
        riesgo_etq = PsychologyEngine.etiqueta_tolerancia_riesgo(estado["tolerancia_riesgo"])

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
    def generar_contexto_llm(
        state_engine,
        personality_traits=None,
        task_ctx=None,
        policy_offsets: dict | None = None,
    ) -> dict:
        """
        Construye un diccionario con información estructurada para inyectar en el prompt
        de Gemini. Incluye tanto el resumen narrativo como datos numéricos clave.
        """
        estado_efectivo = PsychologyEngine.obtener_estado_efectivo(state_engine, policy_offsets)
        contexto = {
            "estado_narrativo": PsychologyEngine.generar_resumen_narrativo(
                state_engine,
                personality_traits,
                task_ctx,
                policy_offsets=policy_offsets,
            ),
            "valores": estado_efectivo,
            "valores_crudos": state_engine.obtener_estado(),
            "umbrales": PsychologyEngine.evaluar_umbrales(
                state_engine,
                task_ctx,
                policy_offsets=policy_offsets,
            ),
        }
        if personality_traits is not None:
            contexto["personalidad"] = personality_traits.obtener_rasgos()
        if task_ctx is not None:
            contexto["tarea"] = task_ctx.obtener_resumen()
        if policy_offsets is not None:
            contexto["offsets_policy"] = {
                clave: round(valor, 2) for clave, valor in policy_offsets.items()
            }
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

    @staticmethod
    def _calcular_foco_efectivo(estado: dict) -> float:
        """
        Replica la logica de StateEngine.foco_efectivo, pero usando un estado derivado.
        """
        foco = estado["foco"]
        if estado["estres"] > 60:
            foco -= (estado["estres"] - 60) * 0.5
        if estado["energia"] < 40:
            foco -= (40 - estado["energia"]) * 0.4
        if estado["carga_cognitiva"] > 50:
            foco -= (estado["carga_cognitiva"] - 50) * 0.3
        if 20 < estado["curiosidad"] < 60:
            foco += 5
        elif estado["curiosidad"] > 80:
            foco -= 5
        return PsychologyEngine._clamp(foco, 0.0, 100.0)
