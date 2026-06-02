"""
identity_state.py - Motor Biologico y Emocional (Capa 2)
Mantiene el estado interno de Lazzaro simulando desgaste de energia,
acumulacion de estres y nivel de atencion.
o en pocas palabras lo que le hiso falta a mi ex 
control emocional basico 👍
"""
from typing import Dict,Any
from core.logger import get_logger

logger = get_logger(__name__)

class IdentityState:
    """
    Estado interno biologico/emocional.
    Las variables evolucionan con cada evento y con el peso y paso del tiempo (tick).
    """

    def __init__(self):
        self.energia: float = 100.0
        self.estres: float = 0.0
        self.enfoque: float = 100.0

    #------------------------------------------------------------------------------------
    #API principal
    #------------------------------------------------------------------------------------

    def tick (self,segundos_transcurridos: float):
        """
        Decaimiento natural por el paso del tiempo.
        Debe ser llamado periodicamente (ej.cada 1-5 segundos) por el Orchestrator.
        #nota que no va relacionada: tan facil que era ponerle a esto orquestador y ya pero queria sonar elegante.
        """

        if segundos_transcurridos <= 0:
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
        
        #1.- coste por tipo de ruta ( procesamineto cognitivo poes)

        if route == "motor":
            self.energia -= 5.0 * intensidad
            self.enfoque -= 2.0 * intensidad
        elif route == "persona":
            self.energia -= 1.0 * intensidad
            self.enfoque+= 3.0 * intensidad # socializar la concentra
        elif route == "oracle":
            self.energia -= 3.0 * intensidad
            self.estres += 2.0 * intensidad # razonar cansa mentalmente

        #2.- coste adicional si hubo fallo ( fallback negativo)
        if not success:
            self.estres += 15.0 * intensidad
            self.enfoque -= 10.0 *intensidad
            logger.info ("[BOLOGIA] Aumento de estres por fallo en tarea.")

        self._limitar()
    
    def _aplicar_descanso(self):
        """Regenera estadisticas. Llamado cuando lazzaro descansa explicitamente."""
        self.energia += 40.0
        self.estres -= 30.0
        self.enfoque = 100.0
        self._limitar()
        logger.info("[BOLOGIA] Lazzaro ha descansado.regeneracion aplicada.")

    # ==============================================================================
    #Evaluacion cualitativa
    # --------------------------------------------------------------------------------

    def obtener_estado_completo(self) -> Dict[str, Any]:
        """
        Devuelven un diccionario con los valores crudos y una etiqueta cualitativa
        Util para el PrompBuilder y para exponer en la API de estado
        """
        return {
        "energia": round(self.energia, 1),
            "estres": round(self.estres, 1),
            "enfoque": round(self.enfoque, 1),
            "nivel_energia": self._describir_energia(),
            "nivel_estres": self._describir_estres(),
            "nivel_enfoque": self._describir_enfoque(),
        }
    def inyectar_en_prompt (self) -> str:
        """
        Genera el bloque de texto que se inserta en el prompt del LLLM para
        que la personalidad de Lazzaro refleje su estado fisiologico actual.
        """
        e = self._describir_energia()
        s = self._describir_estres()
        f = self._describir_enfoque()
        return (
            f"ESTADO FISIOLÓGICO ACTUAL:\n"
            f"- Energía: {self.energia:.1f}% ({e})\n"
            f"- Estrés: {self.estres:.1f}% ({s})\n"
            f"- Atención: {self.enfoque:.1f}% ({f})\n"
            "REGLA: Tu forma de hablar debe reflejar obligatoriamente este estado. "
            "Si estás exhausta, usa respuestas muy cortas (3-5 palabras); "
            "si estás frustrada, sé un poco más fría o sarcástica; "
            "si estás concentrada, sé precisa y sin rodeos; "
            "si estás relajada y con energía, puedes ser más cálida y detallada."
        )
    
    #----------------------------------------------------------------------------------
    #helpers privados 
    # ---------------------------------------------------------------------------------
    
    def _limitar(self):
        """Asegura que los valores nunca salgan del rango de 0-100."""
        self.energia = max(0.0,min(100.0,self.energia))
        self.estres = max (0.0,min(100.0,self.estres))
        self.enfoque = max(0.0,min(100.0,self.enfoque))

    def _describir_energia (self) -> str:
        if self.energia < 15:
            return "exhausta, apenas puede mantenerse activa"
        elif self.energia < 30:
            return "muy cansada, necesita descansar pronto"
        elif self.energia < 50:
            return "algo cansada, preferiría tareas ligeras"
        elif self.energia < 75:
            return "aceptable, puede continuar sin problemas"
        elif self.energia < 90:
            return "buena, se siente con vitalidad"
        else:
            return "óptima, llena de energía"
    def _describir_estres(self) -> str:
        if self.estres > 85:
            return "abrumada, al borde del colapso"
        elif self.estres > 60:
            return "muy tensa, notablemente irritada"
        elif self.estres > 35:
            return "moderadamente estresada"
        elif self.estres > 15:
            return "ligeramente tensa"
        else:
            return "relajada y tranquila"

    def _describir_enfoque(self) -> str:
        if self.enfoque < 20:
            return "completamente dispersa, le cuesta hilar ideas"
        elif self.enfoque < 40:
            return "algo distraída, puede perder detalles"
        elif self.enfoque < 60:
            return "atención normal"
        elif self.enfoque < 80:
            return "bastante concentrada"
        else:
            return "hiperconcentrada, puede manejar tareas complejas"
