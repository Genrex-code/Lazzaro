"""
motor_schemas.py - Esquemas Pydantic para acciones fisicas de la Capa 1.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class StrictParams(BaseModel):
    """Base estricta: el motor no acepta parametros fisicos inventados."""

    model_config = ConfigDict(extra="forbid")


class NavigateToObjectParams(StrictParams):
    nombre_objeto: str = Field(..., description="Nombre exacto o aproximado del objeto destino.")
    distancia: Optional[float] = Field(
        None,
        ge=0.1,
        le=10.0,
        description="Distancia opcional en metros a mantener del objeto.",
    )


class NavigateToPositionParams(StrictParams):
    x: float = Field(..., description="Coordenada X en el mundo 3D.")
    y: float = Field(..., description="Coordenada Y en el mundo 3D.")
    z: float = Field(..., description="Coordenada Z en el mundo 3D.")


class InteractObjectParams(StrictParams):
    nombre_objeto: str = Field(..., description="Objeto con el que se va a interactuar.")
    tipo_accion: Literal["mirar", "tocar", "inspeccionar", "golpear", "usar", "activar"] = Field(
        "mirar",
        description="Tipo de interaccion fisica permitida.",
    )


class GrabObjectParams(StrictParams):
    nombre_objeto: str = Field(..., description="Nombre del objeto a agarrar.")


class ReleaseObjectParams(StrictParams):
    fuerza_lanzamiento: float = Field(
        0.0,
        ge=0.0,
        le=20.0,
        description="Fuerza de lanzamiento (0 = soltar).",
    )


class MoveAwayParams(StrictParams):
    nombre_objeto: str = Field(..., description="Objeto del cual alejarse.")
    distancia: float = Field(2.0, ge=0.5, le=5.0, description="Metros a retroceder.")


class ApplyForceParams(StrictParams):
    nombre_objeto: str = Field(..., description="Objeto al que se aplicara fuerza.")
    vector_x: float = Field(..., description="Componente X de la direccion.")
    vector_y: float = Field(..., description="Componente Y de la direccion.")
    vector_z: float = Field(..., description="Componente Z de la direccion.")
    fuerza: float = Field(..., gt=0.0, le=100.0, description="Magnitud de la fuerza.")


ACTION_SCHEMA_MAP = {
    "navegar_a_objeto": NavigateToObjectParams,
    "navegar_a_posicion": NavigateToPositionParams,
    "interactuar_objeto_3d": InteractObjectParams,
    "agarrar_objeto": GrabObjectParams,
    "soltar_objeto": ReleaseObjectParams,
    "alejarse_de_objeto": MoveAwayParams,
    "aplicar_fuerza": ApplyForceParams,
}


class MotorAction(BaseModel):
    """Respuesta estricta de la Capa 1 lista para convertir a ActionResponse."""

    model_config = ConfigDict(extra="forbid")

    accion: Literal[
        "none",
        "navegar_a_objeto",
        "navegar_a_posicion",
        "interactuar_objeto_3d",
        "agarrar_objeto",
        "soltar_objeto",
        "alejarse_de_objeto",
        "aplicar_fuerza",
    ] = Field(..., description="Accion fisica registrada o none.")
    parametros: dict = Field(
        default_factory=dict,
        description="Parametros validados de la accion.",
    )
    razon: str = Field(
        "",
        description="Justificacion tecnica breve; no se renderiza en el avatar.",
    )
