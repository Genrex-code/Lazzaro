"""
personality.py - Capa 2 (parte) del sistema de comportamiento de Lazzaro.
Define los rasgos de personalidad estables pero moldeables con el tiempo.
Calcula los modificadores que se inyectan en StateEngine para alterar
las velocidades de cambio y las reacciones emocionales.
"""

from core.logger import get_logger

logger = get_logger(__name__)


class PersonalityTraits:
    """
    Contiene los rasgos de personalidad de Lazzaro.
    Escala: -1.0 a 1.0, inicialmente 0.0 (neutro, sin influencia).
    """

    def __init__(self):
        # Rasgos base
        self.resiliencia = 0.0    # Capacidad de recuperación del estrés
        self.impulsividad = 0.0   # Tendencia a actuar sin deliberar
        self.paciencia = 0.0      # Tolerancia a la frustración y fallos
        self.sociabilidad = 0.0   # Preferencia por la interacción social
        self.metodico = 0.0       # Preferencia por el orden y la planificación

        # Tasas de aprendizaje (cuánto cambia un rasgo por evento)
        self.tasa_aprendizaje_base = 0.005  # Pequeño desplazamiento por evento normal
        self.tasa_aprendizaje_alta = 0.02   # Eventos con alta carga emocional

    def update_from_event(self, tipo_evento: str, intensidad_emocional: float = 1.0):
        """
        Ajusta lentamente los rasgos según el tipo de evento y su impacto emocional.
        - tipo_evento: cadena que identifica la naturaleza del suceso.
        - intensidad_emocional: factor de amplificación (1.0 normal, >1.0 evento muy intenso).
        """
        # Determinamos qué rasgo modificar y en qué dirección
        deltas = {
            "resiliencia": 0.0,
            "impulsividad": 0.0,
            "paciencia": 0.0,
            "sociabilidad": 0.0,
            "metodico": 0.0,
        }
        #se que esto lo peudo tratar mejor pero soy demasiado peresoso para eso asi que asi se queda 
        # Mapeo de eventos a cambios de rasgos (positivo = aumenta el rasgo)
        if tipo_evento == "recompensa":
            deltas["resiliencia"] += 0.01
            deltas["sociabilidad"] += 0.005
        elif tipo_evento == "tarea_completada":
            deltas["resiliencia"] += 0.015
            deltas["metodico"] += 0.01
            deltas["paciencia"] += 0.005
        elif tipo_evento == "error_critico":
            deltas["resiliencia"] -= 0.01
            deltas["impulsividad"] += 0.005
            deltas["paciencia"] -= 0.015
        elif tipo_evento == "fracaso":
            deltas["resiliencia"] -= 0.005
            deltas["paciencia"] -= 0.01
            deltas["impulsividad"] += 0.005  # Frustración puede llevar a actuar sin pensar
        elif tipo_evento == "interaccion_social":
            deltas["sociabilidad"] += 0.01
        elif tipo_evento == "critica":
            deltas["sociabilidad"] -= 0.005
            deltas["resiliencia"] -= 0.005
        elif tipo_evento == "descanso":
            deltas["resiliencia"] += 0.01
            deltas["paciencia"] += 0.01
        elif tipo_evento == "sobrecarga":
            deltas["resiliencia"] -= 0.02
            deltas["impulsividad"] += 0.01
            deltas["metodico"] -= 0.01  # El caos reduce el orden interno
        elif tipo_evento == "exploracion_exitosa":
            deltas["impulsividad"] += 0.005  # Refuerza conducta exploratoria
            deltas["resiliencia"] += 0.005
        elif tipo_evento == "exploracion_fallida":
            deltas["impulsividad"] -= 0.01   # Freno a la impulsividad
            deltas["paciencia"] -= 0.005
        elif tipo_evento == "ayuda_recibida":
            deltas["sociabilidad"] += 0.01
            deltas["paciencia"] += 0.005
        elif tipo_evento == "loop_detectado":
            deltas["paciencia"] -= 0.02
            deltas["impulsividad"] += 0.01
            deltas["metodico"] += 0.01  # Intenta ser más estructurado para salir del bucle
        else:
            logger.debug(f"Evento '{tipo_evento}' no modifica personalidad directamente.")

        # Aplicar los deltas escalados por intensidad y tasa de aprendizaje
        tasa = self.tasa_aprendizaje_alta if intensidad_emocional >= 2.0 else self.tasa_aprendizaje_base
        for rasgo, delta in deltas.items():
            if delta != 0:
                valor_actual = getattr(self, rasgo)
                nuevo_valor = valor_actual + delta * intensidad_emocional * tasa
                # Limitar al rango [-1, 1]
                nuevo_valor = max(-1.0, min(1.0, nuevo_valor))
                setattr(self, rasgo, round(nuevo_valor, 4))
                if abs(delta) > 0.001:
                    logger.debug(f"Rasgo '{rasgo}' actualizado de {valor_actual:.3f} a {nuevo_valor:.3f}")

    def get_modifiers(self) -> dict:
        """
        Calcula y devuelve un diccionario de modificadores para StateEngine.
        Estos modificadores ajustan las tasas de cambio de las variables emocionales.
        """
        mods = {
            # La resiliencia actúa como amortiguador del estrés (reduce incremento y acelera decaimiento)
            "amortiguador_estres": 1.0 + self.resiliencia * 0.5,
            # La paciencia modula la tasa de estrés (a mayor paciencia, menor tasa de aumento)
            "tasa_estres": 1.0 - self.paciencia * 0.3,
            # La impulsividad acelera cambios en agresividad y tolerancia al riesgo
            "tasa_agresividad": 1.0 + self.impulsividad * 0.4,
            "tasa_tolerancia_riesgo": 1.0 + self.impulsividad * 0.5,
            # El rasgo metódico reduce la tasa de caída del foco (más estable)
            "tasa_foco": 1.0 - self.metodico * 0.3,
            # La sociabilidad puede afectar la motivación (interacciones alimentan)
            "tasa_motivacion": 1.0 + self.sociabilidad * 0.2,
            # La resiliencia también influye en la recuperación de energía
            "tasa_energia": 1.0 + self.resiliencia * 0.2,
            # Por defecto, el resto de tasas quedan en 1.0 (sin modificación)
            "tasa_carga_cognitiva": 1.0 - self.metodico * 0.2,  # Metódico organiza mejor, menos carga
            "tasa_confianza": 1.0,
            "tasa_curiosidad": 1.0,
        }
        return mods

    def obtener_rasgos(self) -> dict:
        """Devuelve un diccionario con los valores actuales de los rasgos."""
        return {
            "resiliencia": round(self.resiliencia, 4),
            "impulsividad": round(self.impulsividad, 4),
            "paciencia": round(self.paciencia, 4),
            "sociabilidad": round(self.sociabilidad, 4),
            "metodico": round(self.metodico, 4),
        }
