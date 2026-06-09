"""
tools/simulated/movimiento_sim.py - Navegacion semantica y geometrica en el mundo 3D.
Las herramientas devuelven confirmaciones; Unity ejecuta la fisica real con NavMesh.
"""

from pydantic import BaseModel, Field

from tools.registry import ToolRegistry


class NavegarAObjetoParams(BaseModel):
    nombre_objeto: str = Field(
        ...,
        description="Nombre exacto o aproximado del objeto al que quieres dirigirte.",
    )


class NavegarAPosicionParams(BaseModel):
    x: float = Field(..., description="Coordenada X del destino.")
    y: float = Field(..., description="Coordenada Y del destino.")
    z: float = Field(..., description="Coordenada Z del destino.")


class AlejarseDeObjetoParams(BaseModel):
    nombre_objeto: str = Field(
        ...,
        description="Nombre exacto o aproximado del objeto del que Lazzaro debe alejarse.",
    )
    distancia: float = Field(
        2.0,
        ge=0.5,
        le=5.0,
        description="Metros a retroceder desde el objeto. Usa 2.0 si no se especifica distancia.",
    )


class GirarAvatarParams(BaseModel):
    grados: float = Field(
        ...,
        ge=-360.0,
        le=360.0,
        description=(
            "Grados relativos de giro sobre el eje Y. "
            "Usa positivo para girar a la derecha y negativo para girar a la izquierda."
        ),
    )


@ToolRegistry.register(
    name="navegar_a_objeto",
    description=(
        "Ordena a Unity que navegue autonomamente hacia el objeto indicado. "
        "Usa esta herramienta solo cuando el usuario pida acercarse, ir o moverse hacia un objeto."
    ),
    params_schema=NavegarAObjetoParams,
    requires_confirmation=False,
)
def navegar_a_objeto(params: NavegarAObjetoParams) -> str:
    return f"[Navegacion] Orden de ir hacia '{params.nombre_objeto}' enviada al NavMesh de Unity."


@ToolRegistry.register(
    name="navegar_a_posicion",
    description=(
        "Ordena a Unity que navegue hacia las coordenadas (x, y, z) proporcionadas. "
        "Util cuando conoces la posicion exacta pero no el nombre del objeto."
    ),
    params_schema=NavegarAPosicionParams,
    requires_confirmation=False,
)
def navegar_a_posicion(params: NavegarAPosicionParams) -> str:
    return f"[Navegacion] Orden de ir a la posicion ({params.x}, {params.y}, {params.z}) enviada a Unity."


@ToolRegistry.register(
    name="alejarse_de_objeto",
    description=(
        "Ordena a Unity que navegue alejandose del objeto indicado. "
        "Usa esta herramienta cuando el usuario pide apartarse, retroceder o dejar de seguir un objeto."
    ),
    params_schema=AlejarseDeObjetoParams,
    requires_confirmation=False,
)
def alejarse_de_objeto(params: AlejarseDeObjetoParams) -> str:
    return (
        f"[Navegacion] Orden de alejarse {params.distancia}m de "
        f"'{params.nombre_objeto}' enviada a Unity."
    )


@ToolRegistry.register(
    name="girar_avatar",
    description=(
        "Ordena a Unity que rote a Lazzaro en su lugar sobre el eje Y. "
        "Usa esta herramienta para comandos como girar, rotar, voltear, mirar alrededor "
        "o reorientarse sin avanzar hacia ningun objeto."
    ),
    params_schema=GirarAvatarParams,
    requires_confirmation=False,
)
def girar_avatar(params: GirarAvatarParams) -> str:
    return f"[Movimiento] Orden de girar {params.grados} grados enviada a Unity."
