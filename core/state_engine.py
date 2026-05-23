"""
state_engine.py - Capa 1 del sistema de comportamiento de Lazzaro.
Contiene las variables emocionales/cognitivas crudas, el decaimiento temporal (tick),
y la aplicación de eventos. Los valores se actualizan con reglas matemáticas deterministas.
Los rasgos de personalidad (personality.py) actuarán como modificadores externos de las tasas.
"""

import time
from core.logger import get_logger

logger = get_logger(__name__)


class StateEngine:
    """
    Motor de estado interno de Lazzaro.
    Todas las variables son numéricas y se actualizan mediante tick() y aplicar_evento().
    """

    def __init__(self):
        # Variables Homeostáticas (0-100, 0 = ausencia, 100 = máximo)
        self.energia = 80.0          # Energía física/mental disponible
        self.estres = 10.0           # Carga negativa acumulada
        self.foco = 70.0             # Capacidad de atención basal
        self.carga_cognitiva = 20.0  # Saturación mental por multitarea/procesamiento

        # Variables Conductuales (-100 a 100, bipolar con significado psicológico)
        self.confianza = 0.0         # -100 (inseguridad/paranoia) ↔ +100 (sobreconfianza)
        self.curiosidad = 10.0       # -100 (apatía) ↔ +100 (hipercuriosidad)
        self.motivacion = 15.0       # -100 (anhedonia) ↔ +100 (obsesión)
        self.agresividad = 0.0       # -100 (pasividad) ↔ +100 (hostilidad)
        self.tolerancia_riesgo = 0.0 # -100 (aversión total) ↔ +100 (temeridad)

        # Control interno
        self._ultimo_tick = time.time()

        # Modificadores por defecto (sin personalidad). Luego behavior_core los actualizará.
        self.modificadores = {
            "tasa_estres": 1.0,
            "tasa_energia": 1.0,
            "tasa_foco": 1.0,
            "tasa_carga_cognitiva": 1.0,
            "tasa_confianza": 1.0,
            "tasa_curiosidad": 1.0,
            "tasa_motivacion": 1.0,
            "tasa_agresividad": 1.0,
            "tasa_tolerancia_riesgo": 1.0,
            "amortiguador_estres": 1.0,  # 1.0 = sin reducción adicional
            #aca pondria borrachera para que se ambientera
        }

    def actualizar_modificadores(self, nuevos_mods: dict):
        """Recibe un diccionario con los modificadores calculados desde personality.py"""
        self.modificadores.update(nuevos_mods)

    def tick(self):
        """
        Aplica decaimiento/recuperación natural según el tiempo transcurrido.
        Debe llamarse antes de cualquier evaluación para mantener el estado fresco.
        """
        ahora = time.time()
        delta_seg = ahora - self._ultimo_tick
        if delta_seg <= 0:
            return
        self._ultimo_tick = ahora

        # --- Tasas base por segundo (ajustables) ---
        # Homeostáticas
        decaimiento_energia = 0.1      # se gasta solo por existir
        recuperacion_estres = 0.2      # se relaja con el tiempo
        decaimiento_foco = 0.05        # se dispersa lentamente
        decaimiento_carga = 0.15       # la carga cognitiva también se disipa

        # Conductuales (tienden a volver a 0 si no hay refuerzo)
        reversion_conductual = 0.05    # tasa a la que regresan a neutro

        # Aplicar modificadores de personalidad (multiplican las tasas base)
        mod = self.modificadores

        # --- Actualizaciones Homeostáticas ---
        # Energía: decae siempre, más rápido si hay estrés o carga cognitiva altos
        factor_energia = 1.0 + (self.estres / 100.0) * 0.5 + (self.carga_cognitiva / 100.0) * 0.3
        self.energia -= decaimiento_energia * delta_seg * factor_energia * mod["tasa_energia"]

        # Estrés: se disipa naturalmente, pero más lento si la resiliencia es baja
        self.estres -= recuperacion_estres * delta_seg * mod["amortiguador_estres"] * mod["tasa_estres"]

        # Foco: decae con el tiempo, más rápido con estrés alto o energía baja
        factor_foco = 1.0 + (self.estres / 100.0) * 0.4 + ((100 - self.energia) / 100.0) * 0.3
        self.foco -= decaimiento_foco * delta_seg * factor_foco * mod["tasa_foco"]

        # Carga cognitiva: se disipa despacio, pero aumenta si el foco es bajo (esfuerzo compensatorio)
        if self.foco < 40:
            self.carga_cognitiva += 0.2 * delta_seg  # más difícil concentrarse → más carga
        self.carga_cognitiva -= decaimiento_carga * delta_seg * mod["tasa_carga_cognitiva"]

        # --- Actualizaciones Conductuales (reversión a la media 0) ---
        for attr in ["confianza", "curiosidad", "motivacion", "agresividad", "tolerancia_riesgo"]:
            valor = getattr(self, attr)
            if valor > 0:
                setattr(self, attr, valor - reversion_conductual * delta_seg * mod[f"tasa_{attr}"])
            elif valor < 0:
                setattr(self, attr, valor + reversion_conductual * delta_seg * mod[f"tasa_{attr}"])
            # Se acerca gradualmente a 0

        # --- Efectos cruzados internos ---
        # Estrés alto drena energía extra y reduce foco efectivo
        if self.estres > 70:
            self.energia -= 0.2 * delta_seg
            self.foco -= 0.3 * delta_seg
        # Curiosidad muy alta puede aumentar carga cognitiva (dispersión)
        if abs(self.curiosidad) > 70:
            self.carga_cognitiva += 0.1 * delta_seg

        # Asegurar rangos
        self._limitar()

    def aplicar_evento(self, tipo_evento: str, intensidad: float = 1.0, contexto: dict | None = None):
        """
        Ajusta las variables según un evento externo.
        - tipo_evento: cadena que describe el suceso (ver mapeo abajo)
        - intensidad: multiplicador de la magnitud del cambio (1.0 normal)
        - contexto: información adicional (opcional)
        """
        logger.debug(f"Aplicando evento '{tipo_evento}' con intensidad {intensidad}")
        # Primero, tick para actualizar por tiempo
        self.tick()

        # Mapeo de eventos a cambios (valores base, luego escalados por intensidad y modificadores)
        cambios = {
            # tipo_evento: (delta_energia, delta_estres, delta_foco, delta_carga,
            #               delta_confianza, delta_curiosidad, delta_motivacion,
            #               delta_agresividad, delta_tolerancia_riesgo)
        }

        if tipo_evento == "recompensa":
            cambios = (0, -15, 5, -5, 10, 5, 10, -5, 0)
        elif tipo_evento == "tarea_completada":
            cambios = (-5, -10, 0, -10, 15, 0, 20, 0, 5)
        elif tipo_evento == "error_critico":
            cambios = (-10, 25, -15, 15, -20, -5, -15, 5, -10)
        elif tipo_evento == "fracaso":
            cambios = (-5, 20, -10, 10, -25, 0, -20, 0, -5)
        elif tipo_evento == "interaccion_social":
            cambios = (2, -5, 0, 0, 5, 0, 5, 0, 0)
        elif tipo_evento == "critica":
            cambios = (0, 15, -5, 5, -15, 0, -10, 5, 0)
        elif tipo_evento == "descanso":
            cambios = (20, -10, 10, -10, 0, -5, 0, -5, 0)
        elif tipo_evento == "sobrecarga":
            cambios = (-15, 30, -20, 30, -5, 0, -5, 10, 5)
        elif tipo_evento == "exploracion_exitosa":
            cambios = (-5, -5, 0, 5, 10, 15, 10, 0, 5)
        elif tipo_evento == "exploracion_fallida":
            cambios = (-5, 10, -5, 10, -10, -5, -5, 0, -5)
        elif tipo_evento == "ayuda_recibida":
            cambios = (5, -15, 5, -5, 10, 0, 5, -10, 0)
        elif tipo_evento == "loop_detectado":
            cambios = (-5, 20, -10, 15, -10, 0, -10, 10, 0)
        else:
            logger.warning(f"Evento desconocido: '{tipo_evento}', sin cambios.")
            return

        # Aplicar cambios escalados por intensidad
        delta_energia, delta_estres, delta_foco, delta_carga, \
            delta_confianza, delta_curiosidad, delta_motivacion, \
            delta_agresividad, delta_tolerancia = cambios

        mod = self.modificadores
        self.energia += delta_energia * intensidad * mod["tasa_energia"]
        self.estres += delta_estres * intensidad * mod["tasa_estres"] * (1.0 / mod["amortiguador_estres"])
        self.foco += delta_foco * intensidad * mod["tasa_foco"]
        self.carga_cognitiva += delta_carga * intensidad * mod["tasa_carga_cognitiva"]
        self.confianza += delta_confianza * intensidad * mod["tasa_confianza"]
        self.curiosidad += delta_curiosidad * intensidad * mod["tasa_curiosidad"]
        self.motivacion += delta_motivacion * intensidad * mod["tasa_motivacion"]
        self.agresividad += delta_agresividad * intensidad * mod["tasa_agresividad"]
        self.tolerancia_riesgo += delta_tolerancia * intensidad * mod["tasa_tolerancia_riesgo"]

        # Asegurar rangos
        self._limitar()

    def _limitar(self):
        """Restringe todas las variables a sus rangos permitidos."""
        self.energia = max(0, min(100, self.energia))
        self.estres = max(0, min(100, self.estres))
        self.foco = max(0, min(100, self.foco))
        self.carga_cognitiva = max(0, min(100, self.carga_cognitiva))
        self.confianza = max(-100, min(100, self.confianza))
        self.curiosidad = max(-100, min(100, self.curiosidad))
        self.motivacion = max(-100, min(100, self.motivacion))
        self.agresividad = max(-100, min(100, self.agresividad))
        self.tolerancia_riesgo = max(-100, min(100, self.tolerancia_riesgo))

    def obtener_estado(self) -> dict:
        """Devuelve un diccionario con los valores crudos actuales."""
        return {
            "energia": round(self.energia, 2),
            "estres": round(self.estres, 2),
            "foco": round(self.foco, 2),
            "carga_cognitiva": round(self.carga_cognitiva, 2),
            "confianza": round(self.confianza, 2),
            "curiosidad": round(self.curiosidad, 2),
            "motivacion": round(self.motivacion, 2),
            "agresividad": round(self.agresividad, 2),
            "tolerancia_riesgo": round(self.tolerancia_riesgo, 2),
        }

    @property
    def foco_efectivo(self) -> float:
        """
        Foco real considerando estrés, energía y carga cognitiva.
        Se usa para calcular probabilidad de errores.
        """
        f = self.foco
        # Penalizaciones
        if self.estres > 60:
            f -= (self.estres - 60) * 0.5
        if self.energia < 40:
            f -= (40 - self.energia) * 0.4
        if self.carga_cognitiva > 50:
            f -= (self.carga_cognitiva - 50) * 0.3
        # Bonificación por curiosidad moderada (atención focalizada)
        if 20 < self.curiosidad < 60:
            f += 5
        elif self.curiosidad > 80:  # demasiada curiosidad dispersa
            f -= 5
        return max(0, min(100, f))

    @property
    def resiliencia_actual(self) -> float:
        """Nivel de resiliencia efectiva en este momento (0-1)."""
        # Base desde el rasgo de personalidad (se inyectará como modificador)
        base = self.modificadores.get("amortiguador_estres", 1.0)  # 1.0 = resiliencia neutra
        # Modulación por estado: estrés muy alto la reduce temporalmente
        if self.estres > 80:
            base *= 0.8
        elif self.estres < 20:
            base *= 1.1
        # Motivación alta puede dar resiliencia extra
        if self.motivacion > 50:
            base *= 1.05
        return round(base, 2)


# Instancia global para que el servidor mantenga el estado en memoria
estado_actual = StateEngine()