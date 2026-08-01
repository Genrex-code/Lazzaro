"""
identity_state.py - Motor Biologico y Emocional (Capa 2)
Mantiene el estado interno de Lazzaro simulando desgaste de energia,
acumulacion de estres y nivel de atencion.
o en pocas palabras lo que le hiso falta a mi ex 
control emocional basico 👍
"""
from typing import Any, Dict
from core.logger import get_logger

logger = get_logger(__name__)

class IdentityState:
    """
    Estado interno biologico/emocional.
    Las variables evolucionan con cada evento y con el peso y paso del tiempo (tick).
    """

    def __init__(self, state_engine: Any | None = None):
        self._state_engine = state_engine
        self._energia: float = 100.0
        self._estres: float = 0.0
        self._enfoque: float = 100.0

    @property
    def state_engine(self) -> Any | None:
        """Reserva canonica usada en produccion; ``None`` conserva compatibilidad aislada."""
        return self._state_engine

    @property
    def energia(self) -> float:
        if self._state_engine is not None:
            estado = self._state_engine.obtener_estado_biologico()
            return float(estado["energia"])
        return self._energia

    @energia.setter
    def energia(self, valor: float) -> None:
        if self._state_engine is not None:
            self._state_engine.establecer_estado_biologico(energia=valor)
        else:
            self._energia = float(valor)

    @property
    def estres(self) -> float:
        if self._state_engine is not None:
            estado = self._state_engine.obtener_estado_biologico()
            return float(estado["estres"])
        return self._estres

    @estres.setter
    def estres(self, valor: float) -> None:
        if self._state_engine is not None:
            self._state_engine.establecer_estado_biologico(estres=valor)
        else:
            self._estres = float(valor)

    @property
    def enfoque(self) -> float:
        if self._state_engine is not None:
            estado = self._state_engine.obtener_estado_biologico()
            return float(estado["foco"])
        return self._enfoque

    @enfoque.setter
    def enfoque(self, valor: float) -> None:
        if self._state_engine is not None:
            self._state_engine.establecer_estado_biologico(foco=valor)
        else:
            self._enfoque = float(valor)

    #API principal
    def tick (self,segundos_transcurridos: float):
        """
        Decaimiento natural por el paso del tiempo.
        Debe ser llamado periodicamente (ej.cada 1-5 segundos) por el Orchestrator.
        #nota que no va relacionada: tan facil que era ponerle a esto orquestador y ya pero queria sonar elegante.
        """

        if segundos_transcurridos <= 0:
            return
        if self._state_engine is not None:
            self._state_engine.tick()
            return
        #la energia se gasta solo por existir
        self.energia -= 0.05 * segundos_transcurridos
        #el estres se disipa lentamente ( el cuerpo se relaja solo) # no le voy a poner drogas a mi niña 
        self.estres -= 0.1 * segundos_transcurridos
        #el enfoque fluctua ligeramente hacia un punto medio (70)
        if self.enfoque > 70:
            self.enfoque -= 0.15 * segundos_transcurridos
        elif self.enfoque < 70: 
            self.enfoque += 0.15 * segundos_transcurridos
        self._limitar()

    def registrar_evento ( 
            self,
            intent: str ="",
            route: str = "",
            success: bool = True,
            es_descanso: bool = False,
            intensidad: float = 1.0,
    ):
        """
        Modifica el estado biologico basandose en lo que Lazzaro acaba de hacer.
        """

        if es_descanso:
            self._aplicar_descanso()
            return

        delta_energia = 0.0
        delta_estres = 0.0
        delta_enfoque = 0.0

        #1.- coste por tipo de ruta ( procesamineto cognitivo poes)
        if route == "motor":
            delta_energia -= 5.0 * intensidad
            delta_enfoque -= 2.0 * intensidad
        elif route == "persona":
            delta_energia -= 1.0 * intensidad
            delta_enfoque += 3.0 * intensidad # socializar la concentra
        elif route == "oracle":
            delta_energia -= 3.0 * intensidad
            delta_estres += 2.0 * intensidad # razonar cansa mentalmente

        #2.- coste adicional si hubo fallo ( fallback negativo)
        if not success:
            delta_estres += 15.0 * intensidad
            delta_enfoque -= 10.0 * intensidad
            logger.info ("[BOLOGIA] Aumento de estres por fallo en tarea.")

        aplicar_cambios = getattr(self._state_engine, "aplicar_cambios_biologicos", None)
        if callable(aplicar_cambios):
            aplicar_cambios(
                delta_energia=delta_energia,
                delta_estres=delta_estres,
                delta_foco=delta_enfoque,
            )
        else:
            self.energia += delta_energia
            self.estres += delta_estres
            self.enfoque += delta_enfoque
            self._limitar()
    
    def _aplicar_descanso(self):
        """Regenera estadisticas. Llamado cuando lazzaro descansa explicitamente."""
        aplicar_cambios = getattr(self._state_engine, "aplicar_cambios_biologicos", None)
        if callable(aplicar_cambios):
            aplicar_cambios(
                delta_energia=40.0,
                delta_estres=-30.0,
                foco_absoluto=100.0,
            )
        else:
            self.energia += 40.0
            self.estres -= 30.0
            self.enfoque = 100.0
            self._limitar()
        logger.info("[BOLOGIA] Lazzaro ha descansado.regeneracion aplicada.")

    #Evaluacion cualitativa

    def obtener_estado_completo(self) -> Dict[str, Any]:
        """
        Devuelven un diccionario con los valores crudos y una etiqueta cualitativa
        Util para el PrompBuilder y para exponer en la API de estado
        """
        energia, estres, enfoque = self._obtener_valores()
        return {
            "energia": round(energia, 1),
            "estres": round(estres, 1),
            "enfoque": round(enfoque, 1),
            "nivel_energia": self._describir_energia(energia),
            "nivel_estres": self._describir_estres(estres),
            "nivel_enfoque": self._describir_enfoque(enfoque),
        }
    def inyectar_en_prompt (self) -> str:
        """
        Genera el bloque de texto que se inserta en el prompt del LLLM para
        que la personalidad de Lazzaro refleje su estado fisiologico actual.
        """
        energia, estres, enfoque = self._obtener_valores()
        e = self._describir_energia(energia)
        s = self._describir_estres(estres)
        f = self._describir_enfoque(enfoque)
        return (
            f"ESTADO FISIOLÓGICO ACTUAL:\n"
            f"- Energía: {energia:.1f}% ({e})\n"
            f"- Estrés: {estres:.1f}% ({s})\n"
            f"- Atención: {enfoque:.1f}% ({f})\n"
            "REGLA: Tu forma de hablar debe reflejar obligatoriamente este estado. "
            "Si estás exhausta, usa respuestas muy cortas (3-5 palabras); "
            "si estás frustrada, sé un poco más fría o sarcástica; "
            "si estás concentrada, sé precisa y sin rodeos; "
            "si estás relajada y con energía, puedes ser más cálida y detallada."
        )
    
    #helpers privados 

    def _obtener_valores(self) -> tuple[float, float, float]:
        obtener_estado = getattr(self._state_engine, "obtener_estado_biologico", None)
        if callable(obtener_estado):
            estado = obtener_estado()
            return (
                float(estado.get("energia", 100.0)),
                float(estado.get("estres", 0.0)),
                float(estado.get("foco", 100.0)),
            )
        return self.energia, self.estres, self.enfoque
    
    def _limitar(self):
        """Asegura que los valores nunca salgan del rango de 0-100."""
        aplicar_cambios = getattr(self._state_engine, "aplicar_cambios_biologicos", None)
        if callable(aplicar_cambios):
            aplicar_cambios()
            return
        self.energia = max(0.0,min(100.0,self.energia))
        self.estres = max (0.0,min(100.0,self.estres))
        self.enfoque = max(0.0,min(100.0,self.enfoque))

    def _describir_energia (self, valor: float | None = None) -> str:
        energia = self.energia if valor is None else valor
        if energia < 15:
            return "exhausta, apenas puede mantenerse activa"
        elif energia < 30:
            return "muy cansada, necesita descansar pronto"
        elif energia < 50:
            return "algo cansada, preferiría tareas ligeras"
        elif energia < 75:
            return "aceptable, puede continuar sin problemas"
        elif energia < 90:
            return "buena, se siente con vitalidad"
        else:
            return "óptima, llena de energía"
    def _describir_estres(self, valor: float | None = None) -> str:
        estres = self.estres if valor is None else valor
        if estres > 85:
            return "abrumada, al borde del colapso"
        elif estres > 60:
            return "muy tensa, notablemente irritada"
        elif estres > 35:
            return "moderadamente estresada"
        elif estres > 15:
            return "ligeramente tensa"
        else:
            return "relajada y tranquila"

    def _describir_enfoque(self, valor: float | None = None) -> str:
        enfoque = self.enfoque if valor is None else valor
        if enfoque < 20:
            return "completamente dispersa, le cuesta hilar ideas"
        elif enfoque < 40:
            return "algo distraída, puede perder detalles"
        elif enfoque < 60:
            return "atención normal"
        elif enfoque < 80:
            return "bastante concentrada"
        else:
            return "hiperconcentrada, puede manejar tareas complejas"
