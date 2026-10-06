"""
motor_schemas.py - Esquemas Pydantic para acciones fisicas de la Capa 1.
"""

import math
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .motor_resolution import InteractionType


def _validar_numero_fisico_finito(value: Any) -> Any:
    """Rechaza bool y no-finitos antes de que Pydantic convierta a float."""
    if isinstance(value, bool):
        raise ValueError("Un booleano no es un numero fisico valido.")

    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("El parametro fisico debe ser numerico.") from exc

    if not math.isfinite(normalized):
        raise ValueError("El parametro fisico debe ser finito.")
    return value


PhysicalFloat = Annotated[float, BeforeValidator(_validar_numero_fisico_finito)]


class StrictParams(BaseModel):
    """Base estricta: el motor no acepta parametros fisicos inventados."""

    model_config = ConfigDict(extra="forbid")


class NavigateToObjectParams(StrictParams):
    nombre_objeto: str = Field(..., description="Nombre exacto o aproximado del objeto destino.")
    distancia: Optional[PhysicalFloat] = Field(
        None,
        ge=0.1,
        le=10.0,
        description="Distancia opcional en metros a mantener del objeto.",
    )


class NavigateToPositionParams(StrictParams):
    x: PhysicalFloat = Field(..., description="Coordenada X en el mundo 3D.")
    y: PhysicalFloat = Field(..., description="Coordenada Y en el mundo 3D.")
    z: PhysicalFloat = Field(..., description="Coordenada Z en el mundo 3D.")


class InteractObjectParams(StrictParams):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    nombre_objeto: str = Field(..., description="Objeto con el que se va a interactuar.")
    tipo_accion: InteractionType = Field(
        ...,
        description="Tipo de interaccion fisica permitida.",
    )


class GrabObjectParams(StrictParams):
    nombre_objeto: str = Field(..., description="Nombre del objeto a agarrar.")


class ReleaseObjectParams(StrictParams):
    fuerza_lanzamiento: PhysicalFloat = Field(
        0.0,
        ge=0.0,
        le=20.0,
        description="Fuerza de lanzamiento (0 = soltar).",
    )


class MoveAwayParams(StrictParams):
    nombre_objeto: str = Field(..., description="Objeto del cual alejarse.")
    distancia: PhysicalFloat = Field(2.0, ge=0.5, le=5.0, description="Metros a retroceder.")


class RotateAvatarParams(StrictParams):
    grados: PhysicalFloat = Field(
        ...,
        ge=-360.0,
        le=360.0,
        description="Grados relativos de giro en Y. Positivo=derecha, negativo=izquierda.",
    )


class ApplyForceParams(StrictParams):
    nombre_objeto: str = Field(..., description="Objeto al que se aplicara fuerza.")
    vector_x: PhysicalFloat = Field(..., description="Componente X de la direccion.")
    vector_y: PhysicalFloat = Field(..., description="Componente Y de la direccion.")
    vector_z: PhysicalFloat = Field(..., description="Componente Z de la direccion.")
    fuerza: PhysicalFloat = Field(..., gt=0.0, le=100.0, description="Magnitud de la fuerza.")


class CancelTaskParams(StrictParams):
    motivo: str = Field("Solicitud explicita del usuario.", description="Motivo de la cancelacion.")
    cancelar_movimiento: bool = Field(True, description="Detener movimiento activo en Unity.")
    cancelar_cola: bool = Field(True, description="Limpiar cola de subtareas en Unity.")
    texto_confirmacion: str = Field(
        "De acuerdo, cancelo la tarea actual.",
        description="Texto breve de confirmacion opcional.",
    )


ACTION_SCHEMA_MAP = {
    "navegar_a_objeto": NavigateToObjectParams,
    "navegar_a_posicion": NavigateToPositionParams,
    "interactuar_objeto_3d": InteractObjectParams,
    "agarrar_objeto": GrabObjectParams,
    "soltar_objeto": ReleaseObjectParams,
    "alejarse_de_objeto": MoveAwayParams,
    "girar_avatar": RotateAvatarParams,
    "aplicar_fuerza": ApplyForceParams,
    "cancelar_tarea": CancelTaskParams,
}

PHYSICAL_NUMERIC_ACTIONS = frozenset(
    {
        "navegar_a_objeto",
        "navegar_a_posicion",
        "soltar_objeto",
        "alejarse_de_objeto",
        "girar_avatar",
        "aplicar_fuerza",
    }
)


def validar_parametros_fisicos(accion: str, parametros: dict[str, Any]) -> dict[str, Any]:
    """Valida y normaliza parametros usando el schema canonico de la accion Motor."""
    schema_cls = ACTION_SCHEMA_MAP[accion]
    return schema_cls.model_validate(parametros).model_dump(exclude_none=True)


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
        "girar_avatar",
        "aplicar_fuerza",
        "cancelar_tarea",
    ] = Field(..., description="Accion fisica registrada o none.")
    parametros: dict = Field(
        default_factory=dict,
        description="Parametros validados de la accion.",
    )
    razon: str = Field(
        "",
        description="Justificacion tecnica breve; no se renderiza en el avatar.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Metadata interna de auditoria. No se envia como parametro fisico.",
    )
