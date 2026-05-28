"""
tools/simulated/interaccion_sim.py - Herramientas de interacción con objetos en el mundo 3D.
"""

from pydantic import BaseModel, Field
from tools.registry import ToolRegistry


# -------------------- Modelos de parámetros --------------------
class AgarrarObjetoParams(BaseModel):
    nombre_objeto: str = Field(..., description="Nombre del objeto a agarrar")


class SoltarObjetoParams(BaseModel):
    fuerza_lanzamiento: float = Field(0.0, description="Fuerza de lanzamiento (0 = soltar suavemente)")


class AplicarFuerzaParams(BaseModel):
    nombre_objeto: str = Field(..., description="Objeto al que aplicar la fuerza")
    vector_x: float = Field(..., description="Componente X de la dirección")
    vector_y: float = Field(..., description="Componente Y de la dirección")
    vector_z: float = Field(..., description="Componente Z de la dirección")
    fuerza: float = Field(..., description="Magnitud de la fuerza")


class HablarParams(BaseModel):
    texto_a_comunicar: str = Field(..., description="Texto que Lazzaro dirá en voz alta")


# -------------------- Funciones registradas --------------------
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


@ToolRegistry.register(
    name="hablar",
    description="Lazzaro habla en voz alta para comunicar un mensaje.",
    params_schema=HablarParams
)
def hablar(params: HablarParams) -> str:
    return f"[Mundo Virtual] Lazzaro dice: '{params.texto_a_comunicar}'."