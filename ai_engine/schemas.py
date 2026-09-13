"""
schemas.py - Modelos de datos escritos para el modo ai_engine.
Centraliza las estructuras que viajan entre el core, la memoria, el LLM y las herramientas.
"""

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_engine.decision_outcome import DecisionOutcome
from ai_engine.identity_projection import IdentityContextV1


class AgentContext(BaseModel):
    """Estado completo del agente justo antes de armar el prompt."""

    identidad: IdentityContextV1 | None = None
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
    proveedor: str = "ollama"

class PromptSections(BaseModel):
    """Contrato legacy de fixtures; no es una autoridad productiva de prompts."""

    system: str = ""
    identidad: str = ""
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
            self.identidad,
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
    accion: str = Field(
        default="none",
        description="Nombre exacto de la accion a ejecutar o 'none' si no aplica.",
    )
    parametros: dict[str, Any] = Field(
        default_factory=dict,
        description="Parametros validos para la accion elegida. Si accion es 'none', usa {}.",
    )
    razon: str = Field(
        default="",
        description="Justificacion corta y concreta de la decision.",
    )


class ActionResponse(BaseModel):
    """Accion validada lista para ser ejecutada o rechazada."""

    pensamiento: str
    accion: str
    parametros: dict[str, Any] = Field(default_factory=dict)
    razon: str = ""
    valido: bool = True
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    outcome: DecisionOutcome

    @model_validator(mode="before")
    @classmethod
    def legacy_outcome_is_explicit(cls, value: Any) -> Any:
        """Mantiene constructores legacy sin promoverlos a decisiones primarias."""
        if not isinstance(value, dict) or value.get("outcome") is not None:
            return value
        enriched = dict(value)
        enriched["outcome"] = DecisionOutcome.legacy_for_action(value.get("accion"))
        return enriched


class OracleResult(BaseModel):
    """Conocimiento interno producido por Capa 3 antes de pasar por Persona."""

    pregunta_original: str
    contenido_tecnico: str
    resumen_para_persona: Optional[str] = None
    fuente: Literal["api", "cache", "fallback", "error"] = "api"
    cache_hit: bool = False
    error: Optional[str] = None
