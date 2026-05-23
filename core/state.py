import logging

logger = logging.getLogger(__name__)

class LazzaroState:
    def __init__(self):
        # Variables base
        self.dopamina = 50
        self.estres = 0
        self.energia = 100
    
    def evaluar_evento(self, tipo_evento: str, detalles: dict):
        """
        Aquí DeepSeek te puede ayudar a crear una lógica matemática compleja.
        Por ahora, un esqueleto básico basado en strings.
        """
        logger.info(f"Evaluando impacto emocional de: {tipo_evento}")
        
        if tipo_evento == "recompensa":
            self.dopamina = min(100, self.dopamina + 15)
            self.estres = max(0, self.estres - 10)
        elif tipo_evento == "error_critico":
            self.estres = min(100, self.estres + 25)
            self.energia = max(0, self.energia - 10)
        
        # El simple paso del tiempo o procesar algo gasta energía
        self.energia = max(0, self.energia - 1)
        
        logger.debug(f"Nuevos stats -> Dopamina: {self.dopamina} | Estrés: {self.estres} | Energía: {self.energia}")

    def obtener_estado_actual(self) -> dict:
        return {
            "dopamina": self.dopamina,
            "estres": self.estres,
            "energia": self.energia
        }

# Instancia global para que el estado persista mientras el servidor esté encendido
estado_actual = LazzaroState()