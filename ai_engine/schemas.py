"""
schemas.py - Modelos de datos escritos para el modo ia_engine.
Centraliza las estructuras que viajan entre el core, la memoria , el LLM y las herramientas.
"""

from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field
from datetime import timezone
#==========================================================================================
#Contexto que recibe el reasoning engine desde el exterior
#==========================================================================================

class AgentContext(BaseModel):
    """Estado completo del agente justo antes de armar el prompt."""
    #estado emocinal / cognitivo (desde psychology / behavior_core)
    estado_narrativo: str = ""
    valores_estado: dict[str,float] = Field(default_factory=dict)

    #pesonalidad
    personalidad: dict[str,float] = Field(default_factory=dict)

    #tarea actual
    tarea: Optional[dict[str,Any]] = None # borren none quiero estar solo

    #memoria recuperada y resumida
    memoria_texto: str = ""

    #herramientas disponibles (lsita de nombres)
    herramientas: list[str] = Field(default_factory=list)

    #politica activa (cautelosa,exploradora,etc.)
    politica:str = "exploradora" #harcodeado? #nota por si esto truena ya tengo un sospechoso

    #umbrales /banderas de comportamiento
    banderas: dict[str,bool] = Field(default_factory=dict)

    #TIMESTAMP del contexto # te la comex sin pretexto jasjasjas
    #timestamp: datetime = Field(default_factory=datetime.utcnow) # ya se que esta obsoleto pero dejenme ser feliz :c
    #act: no me dejaron ser feliz :c

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

# ===========================================================================================
#secciones del promp que arma prompt_buider # chingaderita 1 
# ===========================================================================================

class PromptSections (BaseModel):
    """Cada seccion del prompt final."""

    system:str = ""
    personalidad:str = ""
    estado_emocional:str = ""    #de mejico 
    objetivo:str = ""
    memoria:str = ""
    herramientas:str = ""
    restricciones:str = ""
    formato_respuestas:str = ""

    def ensamblar(self) -> str:
        """combina las secciones no vacias en un solo string"""
        partes = [
            self.system,
            self.personalidad,
            self.estado_emocional,
            self.objetivo,
            self.memoria,
            self.herramientas,
            self.restricciones,
            self.formato_respuestas,
        ]
        return "\n\n".join(p for p in partes if p)
    
#===========================================================================================
#Respuestas cruda esperada del LLM (json obligatorio)
#===========================================================================================

class LLMResponse(BaseModel):
    """formato que el LLM debe devolver"""
    pensamiento: str = Field(...,description="Razonamiento interno del agente")
    accion: Optional[str] = Field(
        default=None,
        description="Nombre de la accion a ejecutar o null si no aplica",
    )
    parametros: dict[str,Any] = Field(default_factory=dict,description="Parametros de la accion")
    razon: str = Field("",description="Motivo por el que se elige esta accion")

#============================================================================================
#Resultado final despues de parsear y validar
#============================================================================================

class ActionResponse(BaseModel):
    """Accion validad lista para ser ejecutada (o rechazada)""" # cough cough maria antonieta couhg cough 
    #mejor si me cayo con el chiste del rechazo
    pensamiento: str
    accion: str
    parametros: dict[str, Any] = Field(default_factory=dict)
    razon: str = ""
    valido: bool = True
    error: Optional[str] = None
