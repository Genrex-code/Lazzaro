"""
state_engine.py - Capa 1 del sistema de comportamiento de Lazzaro.
Contiene las variables emocionales/cognitivas crudas, el decaimiento temporal
y la aplicacion de eventos deterministas.
"""

import time

from core.logger import get_logger

logger = get_logger(__name__)


class StateEngine:
    """
    Motor de estado interno de Lazzaro.
    Todas las variables son numericas y se actualizan mediante tick() y aplicar_evento().
    """

    MAX_TICK_DELTA_SEG = 300.0
    DOLOR_FISICO_DURACION_SEG = 15.0

    def __init__(self):
        self.energia = 80.0
        self.estres = 10.0
        self.foco = 70.0
        self.carga_cognitiva = 20.0

        self.confianza = 0.0
        self.curiosidad = 10.0
        self.motivacion = 15.0
        self.agresividad = 0.0
        self.tolerancia_riesgo = 0.0

        self._ultimo_tick = time.time()
        self.dolor_fisico_reciente = False
        self._dolor_fisico_expira = 0.0

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
            "amortiguador_estres": 1.0,
        }

    def actualizar_modificadores(self, nuevos_mods: dict):
        """Recibe un diccionario con los modificadores calculados desde personality.py."""
        self.modificadores.update(nuevos_mods)

    def tick(self):
        """
        Aplica decaimiento/recuperacion natural segun el tiempo transcurrido.
        Debe llamarse antes de cualquier evaluacion para mantener el estado fresco.
        """
        ahora = time.time()
        delta_seg = ahora - self._ultimo_tick
        if delta_seg <= 0:
            self._ultimo_tick = ahora
            return

        self._ultimo_tick = ahora
        if delta_seg > self.MAX_TICK_DELTA_SEG:
            logger.info(
                "Tick limitado de %.2fs a %.2fs para evitar decaimiento excesivo tras una pausa larga.",
                delta_seg,
                self.MAX_TICK_DELTA_SEG,
            )
            delta_seg = self.MAX_TICK_DELTA_SEG

        if self.dolor_fisico_reciente and ahora >= self._dolor_fisico_expira:
            self.dolor_fisico_reciente = False

        decaimiento_energia = 0.1
        recuperacion_estres = 0.2
        decaimiento_foco = 0.05
        decaimiento_carga = 0.15
        reversion_conductual = 0.05

        mod = self.modificadores

        factor_energia = 1.0 + (self.estres / 100.0) * 0.5 + (self.carga_cognitiva / 100.0) * 0.3
        self.energia -= decaimiento_energia * delta_seg * factor_energia * mod["tasa_energia"]

        self.estres -= recuperacion_estres * delta_seg * mod["amortiguador_estres"] * mod["tasa_estres"]
        self.energia = max(0.0, min(100.0, self.energia))
        self.estres = max(0.0, min(100.0, self.estres))

        factor_foco = 1.0 + (self.estres / 100.0) * 0.4 + ((100 - self.energia) / 100.0) * 0.3
        self.foco -= decaimiento_foco * delta_seg * factor_foco * mod["tasa_foco"]

        if self.foco < 40:
            self.carga_cognitiva += 0.2 * delta_seg
        self.carga_cognitiva -= decaimiento_carga * delta_seg * mod["tasa_carga_cognitiva"]

        for attr in ["confianza", "curiosidad", "motivacion", "agresividad", "tolerancia_riesgo"]:
            valor = getattr(self, attr)
            paso = reversion_conductual * delta_seg * mod[f"tasa_{attr}"]
            if valor > 0:
                setattr(self, attr, max(0.0, valor - paso))
            elif valor < 0:
                setattr(self, attr, min(0.0, valor + paso))

        if self.estres > 70:
            self.energia -= 0.2 * delta_seg
            self.foco -= 0.3 * delta_seg

        if abs(self.curiosidad) > 70:
            self.carga_cognitiva += 0.1 * delta_seg

        self._limitar()

    def aplicar_evento(self, tipo_evento: str, intensidad: float = 1.0, contexto: dict | None = None):
        """
        Ajusta las variables segun un evento externo.
        """
        logger.debug("Aplicando evento '%s' con intensidad %s", tipo_evento, intensidad)
        self.tick()

        cambios = self._obtener_cambios_evento(tipo_evento)
        if cambios is None:
            logger.warning("Evento desconocido: '%s', sin cambios.", tipo_evento)
            return

        if tipo_evento == "alerta_dolor_fisico":
            self.dolor_fisico_reciente = True
            self._dolor_fisico_expira = time.time() + self.DOLOR_FISICO_DURACION_SEG

        (
            delta_energia,
            delta_estres,
            delta_foco,
            delta_carga,
            delta_confianza,
            delta_curiosidad,
            delta_motivacion,
            delta_agresividad,
            delta_tolerancia,
        ) = cambios

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

        self._limitar()

    def _obtener_cambios_evento(self, tipo_evento: str) -> tuple[float, ...] | None:
        if tipo_evento == "recompensa":
            return (0, -15, 5, -5, 10, 5, 10, -5, 0)
        if tipo_evento == "tarea_completada":
            return (-5, -10, 0, -10, 15, 0, 20, 0, 5)
        if tipo_evento == "error_critico":
            return (-10, 25, -15, 15, -20, -5, -15, 5, -10)
        if tipo_evento == "fracaso":
            return (-5, 20, -10, 10, -25, 0, -20, 0, -5)
        if tipo_evento == "interaccion_social":
            return (2, -5, 0, 0, 5, 0, 5, 0, 0)
        if tipo_evento == "critica":
            return (0, 15, -5, 5, -15, 0, -10, 5, 0)
        if tipo_evento == "descanso":
            return (20, -10, 10, -10, 0, -5, 0, -5, 0)
        if tipo_evento == "sobrecarga":
            return (-15, 30, -20, 30, -5, 0, -5, 10, 5)
        if tipo_evento == "exploracion_exitosa":
            return (-5, -5, 0, 5, 10, 15, 10, 0, 5)
        if tipo_evento == "exploracion_fallida":
            return (-5, 10, -5, 10, -10, -5, -5, 0, -5)
        if tipo_evento == "ayuda_recibida":
            return (5, -15, 5, -5, 10, 0, 5, -10, 0)
        if tipo_evento == "loop_detectado":
            return (-5, 20, -10, 15, -10, 0, -10, 10, 0)
        if tipo_evento == "alerta_dolor_fisico":
            return (-20, 40, -25, 20, -20, -10, -15, 20, -25)
        if tipo_evento == "inicio_simulacion":
            return (0, -3, 5, 0, 0, 8, 6, 0, 0)
        if tipo_evento in {"actualizacion_estado", "percepcion_entorno"}:
            return (0, 0, 0, 0, 0, 0, 0, 0, 0)
        if tipo_evento == "comando_usuario":
            return (0, -2, 6, -2, 4, 0, 8, 0, 0)
        if tipo_evento == "resultado_accion":
            return (0, 0, 0, 0, 0, 0, 0, 0, 0)
        return None

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
            "dolor_fisico_reciente": self.dolor_fisico_reciente,
        }

    @property
    def foco_efectivo(self) -> float:
        """
        Foco real considerando estres, energia y carga cognitiva.
        Se usa para calcular probabilidad de errores.
        """
        foco = self.foco
        if self.estres > 60:
            foco -= (self.estres - 60) * 0.5
        if self.energia < 40:
            foco -= (40 - self.energia) * 0.4
        if self.carga_cognitiva > 50:
            foco -= (self.carga_cognitiva - 50) * 0.3
        if 20 < self.curiosidad < 60:
            foco += 5
        elif self.curiosidad > 80:
            foco -= 5
        return max(0, min(100, foco))

    @property
    def resiliencia_actual(self) -> float:
        """Nivel de resiliencia efectiva en este momento (0-1)."""
        base = self.modificadores.get("amortiguador_estres", 1.0)
        if self.estres > 80:
            base *= 0.8
        elif self.estres < 20:
            base *= 1.1
        if self.motivacion > 50:
            base *= 1.05
        return round(base, 2)


estado_actual = StateEngine()
