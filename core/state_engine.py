"""
state_engine.py - Capa 1 del sistema de comportamiento de Lazzaro.
Contiene las variables emocionales/cognitivas crudas, el decaimiento temporal
y la aplicacion de eventos deterministas.
"""

import threading
import time
from collections.abc import Callable

from core.logger import get_logger

logger = get_logger(__name__)


class StateEngine:
    """
    Motor de estado interno de Lazzaro.
    Todas las variables son numericas y se actualizan mediante tick() y aplicar_evento().
    """

    MAX_TICK_DELTA_SEG = 300.0
    DOLOR_FISICO_DURACION_SEG = 15.0
    RECUPERACION_DESCANSO_POR_SEGUNDO = 0.10
    RECUPERACION_ESTRES_POR_SEGUNDO = 0.20
    RECUPERACION_CARGA_POR_SEGUNDO = 0.15

    def __init__(self, *, clock: Callable[[], float] | None = None):
        self._clock = clock or time.monotonic
        self._state_lock = threading.RLock()

        self.energia = 80.0
        self.estres = 10.0
        self.foco = 70.0
        self.carga_cognitiva = 20.0

        self.confianza = 0.0
        self.curiosidad = 10.0
        self.motivacion = 15.0
        self.agresividad = 0.0
        self.tolerancia_riesgo = 0.0

        self._ultimo_tick = self._clock()
        self.dolor_fisico_reciente = False
        self._dolor_fisico_expira = 0.0
        self._descansando = False
        self._descanso_inicio: float | None = None
        self._descanso_energia_inicio: float | None = None

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
        with self._state_lock:
            self.modificadores.update(nuevos_mods)

    def tick(self):
        """
        Aplica decaimiento/recuperacion natural segun el tiempo transcurrido.
        Debe llamarse antes de cualquier evaluacion para mantener el estado fresco.
        """
        with self._state_lock:
            ahora = self._clock()
            delta_seg = ahora - self._ultimo_tick
            if delta_seg <= 0:
                self._ultimo_tick = ahora
                return

            self._ultimo_tick = ahora
            if self.dolor_fisico_reciente and ahora >= self._dolor_fisico_expira:
                self.dolor_fisico_reciente = False

            if self._descansando:
                self._aplicar_intervalo_descanso(delta_seg)
                return

            if delta_seg > self.MAX_TICK_DELTA_SEG:
                logger.info(
                    "Tick limitado de %.2fs a %.2fs para evitar decaimiento excesivo tras una pausa larga.",
                    delta_seg,
                    self.MAX_TICK_DELTA_SEG,
                )
                delta_seg = self.MAX_TICK_DELTA_SEG

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

    def iniciar_descanso(self) -> bool:
        """Inicia un periodo no acumulable de recuperacion temporal."""
        with self._state_lock:
            if self._descansando:
                return False
            self.tick()
            self._descansando = True
            self._descanso_inicio = self._ultimo_tick
            self._descanso_energia_inicio = self.energia
            logger.info("Descanso biologico temporal iniciado.")
            return True

    def finalizar_descanso(self) -> dict[str, float | bool]:
        """Liquida la recuperacion acumulada y vuelve al desgaste activo."""
        with self._state_lock:
            if not self._descansando:
                return {
                    "aplicado": False,
                    "segundos": 0.0,
                    "energia_recuperada": 0.0,
                }

            inicio = self._descanso_inicio
            energia_inicio = self._descanso_energia_inicio
            self.tick()
            inicio_real = self._ultimo_tick if inicio is None else inicio
            segundos = max(0.0, self._ultimo_tick - float(inicio_real))
            recuperacion_teorica = segundos * self.RECUPERACION_DESCANSO_POR_SEGUNDO
            if energia_inicio is None:
                energia_recuperada = 0.0
            else:
                energia_recuperada = min(100.0 - energia_inicio, recuperacion_teorica)

            self._descansando = False
            self._descanso_inicio = None
            self._descanso_energia_inicio = None
            logger.info(
                "Descanso biologico finalizado tras %.2fs; recuperacion=%.2f.",
                segundos,
                max(0.0, energia_recuperada),
            )
            return {
                "aplicado": True,
                "segundos": segundos,
                "energia_recuperada": max(0.0, energia_recuperada),
            }

    @property
    def descansando(self) -> bool:
        with self._state_lock:
            return self._descansando

    def aplicar_cambios_biologicos(
        self,
        *,
        delta_energia: float = 0.0,
        delta_estres: float = 0.0,
        delta_foco: float = 0.0,
        foco_absoluto: float | None = None,
    ) -> None:
        """Aplica costes biologicos sobre la unica reserva de estado."""
        with self._state_lock:
            self.energia += float(delta_energia)
            self.estres += float(delta_estres)
            if foco_absoluto is None:
                self.foco += float(delta_foco)
            else:
                self.foco = float(foco_absoluto)
            self._limitar()

    def establecer_estado_biologico(
        self,
        *,
        energia: float | None = None,
        estres: float | None = None,
        foco: float | None = None,
    ) -> None:
        """Establece valores biologicos de forma atomica para adaptadores legacy."""
        with self._state_lock:
            if energia is not None:
                self.energia = float(energia)
            if estres is not None:
                self.estres = float(estres)
            if foco is not None:
                self.foco = float(foco)
            self._limitar()

    def obtener_estado_biologico(self, *, proyectar_descanso: bool = True) -> dict[str, float]:
        """Entrega un snapshot atomico, opcionalmente proyectado, sin redondear."""
        with self._state_lock:
            if proyectar_descanso:
                energia, estres, _ = self._proyectar_descanso()
            else:
                energia, estres = self.energia, self.estres
            return {
                "energia": float(energia),
                "estres": float(estres),
                "foco": float(self.foco),
            }

    def _aplicar_intervalo_descanso(self, delta_seg: float) -> None:
        """Recupera energia sin aplicar el desgaste activo del mismo intervalo."""
        if delta_seg <= 0:
            return
        mod = self.modificadores
        self.energia += self.RECUPERACION_DESCANSO_POR_SEGUNDO * delta_seg
        self.estres -= (
            self.RECUPERACION_ESTRES_POR_SEGUNDO
            * delta_seg
            * mod["amortiguador_estres"]
            * mod["tasa_estres"]
        )
        self.carga_cognitiva -= (
            self.RECUPERACION_CARGA_POR_SEGUNDO
            * delta_seg
            * mod["tasa_carga_cognitiva"]
        )
        # Durante el descanso el foco no decae y tampoco se aplica fatiga por estres alto.
        self._limitar()

    def _proyectar_descanso(self) -> tuple[float, float, float]:
        energia = self.energia
        estres = self.estres
        carga = self.carga_cognitiva
        if not self._descansando:
            return energia, estres, carga

        delta_seg = max(0.0, self._clock() - self._ultimo_tick)
        mod = self.modificadores
        energia += self.RECUPERACION_DESCANSO_POR_SEGUNDO * delta_seg
        estres -= (
            self.RECUPERACION_ESTRES_POR_SEGUNDO
            * delta_seg
            * mod["amortiguador_estres"]
            * mod["tasa_estres"]
        )
        carga -= (
            self.RECUPERACION_CARGA_POR_SEGUNDO
            * delta_seg
            * mod["tasa_carga_cognitiva"]
        )
        return (
            max(0.0, min(100.0, energia)),
            max(0.0, min(100.0, estres)),
            max(0.0, min(100.0, carga)),
        )

    def aplicar_evento(self, tipo_evento: str, intensidad: float = 1.0, contexto: dict | None = None):
        """
        Ajusta las variables segun un evento externo.
        """
        with self._state_lock:
            logger.debug("Aplicando evento '%s' con intensidad %s", tipo_evento, intensidad)
            self.tick()

            cambios = self._obtener_cambios_evento(tipo_evento)
            if cambios is None:
                logger.warning("Evento desconocido: '%s', sin cambios.", tipo_evento)
                return

            if tipo_evento == "alerta_dolor_fisico":
                self.dolor_fisico_reciente = True
                self._dolor_fisico_expira = self._clock() + self.DOLOR_FISICO_DURACION_SEG

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
        with self._state_lock:
            energia, estres, carga = self._proyectar_descanso()
            return {
                "energia": round(energia, 2),
                "estres": round(estres, 2),
                "foco": round(self.foco, 2),
                "carga_cognitiva": round(carga, 2),
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
