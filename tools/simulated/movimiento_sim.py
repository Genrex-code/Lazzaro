"""
tools/simulated/movimiento_sim.py - Navegacion semantica y geometrica en el mundo 3D.
Las herramientas devuelven confirmaciones; Unity ejecuta la fisica real con NavMesh.
"""

from ai_engine.motor_schemas import (
    CancelTaskParams as CancelarTareaParams,
    MoveAwayParams as AlejarseDeObjetoParams,
    NavigateToObjectParams as NavegarAObjetoParams,
    NavigateToPositionParams as NavegarAPosicionParams,
    RotateAvatarParams as GirarAvatarParams,
)
from tools.registry import ToolRegistry


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


@ToolRegistry.register(
    name="cancelar_tarea",
    description=(
        "Ordena a Unity detener el movimiento activo y limpiar la cola de subtareas. "
        "Usa esta herramienta solo cuando el usuario pida cancelar, detener o abandonar una tarea."
    ),
    params_schema=CancelarTareaParams,
    requires_confirmation=False,
)
def cancelar_tarea(params: CancelarTareaParams) -> str:
    return f"[Control] Cancelacion de tarea enviada a Unity. Motivo: {params.motivo}"
