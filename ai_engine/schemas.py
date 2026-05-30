"""
schemas.py - Modelos de datos escritos para el modo ai_engine.
Centraliza las estructuras que viajan entre el core, la memoria, el LLM y las herramientas.
"""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class AgentContext(BaseModel):
    """Estado completo del agente justo antes de armar el prompt."""

    estado_narrativo: str = ""
    valores_estado: dict[str, float] = Field(default_factory=dict)
    personalidad: dict[str, float] = Field(default_factory=dict)
    tarea: Optional[dict[str, Any]] = None
    memoria_texto: str = ""
    percepcion_texto: str = ""
    constelacion_resumen: str = ""
    herramientas: list[str] = Field(default_factory=list)
    herramientas_detalle: dict[str, str] = Field(default_factory=dict)
    politica: str = "exploradora"
    banderas: dict[str, bool] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    proveedor: str = "ollama" # agregado

class PromptSections(BaseModel):
    """Cada seccion del prompt final."""

    system: str = ""
    personalidad: str = ""
    estado_emocional: str = ""
    entorno: str = ""
    objetivo: str = ""
    memoria: str = ""
    herramientas: str = ""
    restricciones: str = ""
    formato_respuestas: str = ""

    def ensamblar(self) -> str:
        """Combina las secciones no vacias en un solo string."""
        partes = [
            self.system,
            self.personalidad,
            self.estado_emocional,
            self.entorno,
            self.objetivo,
            self.memoria,
            self.herramientas,
            self.restricciones,
            self.formato_respuestas,
        ]
        return "\n\n".join(parte for parte in partes if parte)


class LLMResponse(BaseModel):
    """Formato estructurado que el LLM debe devolver."""

    model_config = ConfigDict(extra="forbid")

    pensamiento: str = Field(
        ...,
        description="Resumen breve de la decision. No incluyas cadena de pensamiento extensa.",
    )
    accion: Optional[str] = Field(
        default=None,
        description="Nombre exacto de la accion a ejecutar o null si no aplica.",
    )
    parametros: dict[str, Any] = Field(
        default_factory=dict,
        description="Parametros validos para la accion elegida. Si accion es null, usa {}.",
    )
    razon: str = Field(
        default="",
        description="Justificacion corta y concreta de la decision.",
    )
    emocion_facial: str = Field(
        default="Fcl_ALL_Neutral",
        description="Expresion facial a mostrar (ej. Fcl_ALL_Joy, Fcl_ALL_Sorrow, Fcl_ALL_Angry, Fcl_ALL_Fun, Fcl_ALL_Neutral).",
    )


class ActionResponse(BaseModel):
    """Accion validada lista para ser ejecutada o rechazada."""

    pensamiento: str
    accion: str
    parametros: dict[str, Any] = Field(default_factory=dict)
    razon: str = ""
    valido: bool = True
    error: Optional[str] = None
    emocion_facial: str = "Fcl_ALL_Neutral"
