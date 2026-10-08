"""
tools/simulated/interaccion_sim.py - Herramientas de interacción con objetos en el mundo 3D.
"""

from pydantic import BaseModel, Field
from ai_engine.motor_schemas import (
    ApplyForceParams as AplicarFuerzaParams,
    GrabObjectParams as AgarrarObjetoParams,
    InteractObjectParams as InteractuarObjeto3DParams,
    ReleaseObjectParams as SoltarObjetoParams,
)
from tools.registry import ToolRegistry


class HablarParams(BaseModel):
    texto_a_comunicar: str = Field(..., description="Texto que Lazzaro dirá en voz alta")


#Funciones registradas 
@ToolRegistry.register(
    name="interactuar_objeto_3d",
    description="Interactua de forma simple con un objeto cercano del entorno 3D.",
    params_schema=InteractuarObjeto3DParams
)
def interactuar_objeto_3d(params: InteractuarObjeto3DParams) -> str:
    return f"[Mundo Virtual] Lazzaro ejecuta '{params.tipo_accion.value}' sobre '{params.nombre_objeto}'."


@ToolRegistry.register(
    name="agarrar_objeto",
    description="Agarra un objeto del entorno 3D identificado por su nombre.",
    params_schema=AgarrarObjetoParams
)
def agarrar_objeto(params: AgarrarObjetoParams) -> str:
    return f"[Mundo Virtual] Lazzaro agarra el objeto '{params.nombre_objeto}'."


@ToolRegistry.register(
    name="soltar_objeto",
    description="Suelta el objeto que tiene en las manos, opcionalmente con fuerza de lanzamiento.",
    params_schema=SoltarObjetoParams
)
def soltar_objeto(params: SoltarObjetoParams) -> str:
    if params.fuerza_lanzamiento > 0:
        return f"[Mundo Virtual] Lazzaro lanza el objeto con fuerza {params.fuerza_lanzamiento}."
    return "[Mundo Virtual] Lazzaro suelta el objeto suavemente."


@ToolRegistry.register(
    name="aplicar_fuerza",
    description="Aplica una fuerza a un objeto en la dirección y magnitud especificadas.",
    params_schema=AplicarFuerzaParams
)
def aplicar_fuerza(params: AplicarFuerzaParams) -> str:
    return (
        f"[Mundo Virtual] Aplicando fuerza {params.fuerza} al objeto '{params.nombre_objeto}' "
        f"en dirección ({params.vector_x}, {params.vector_y}, {params.vector_z})."
    )
#agregado de aca hacia abajo
@ToolRegistry.register(
    name="hablar",
    description="Comunica un mensaje directamente al usuario. NUNCA pongas tu monólogo interno aquí, solo lo que el usuario debe escuchar.",
    params_schema=HablarParams,
    requires_confirmation=False
)
def hablar(params: HablarParams) -> str:
    return f"[Comunicación] Lazzaro dijo: '{params.texto_a_comunicar}'"
