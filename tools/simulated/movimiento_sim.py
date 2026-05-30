"""
tools/simulated/navegacion_sim.py - Navegación semántica y geométrica en el mundo 3D.
Reemplaza a movimiento_sim.py. Las herramientas devuelven solo una confirmación,
porque Unity se encarga de ejecutar la física real con NavMesh.
"""

from tools.registry import ToolRegistry
from pydantic import BaseModel, Field


# -------------------- Modelos de parámetros --------------------
class NavegarAObjetoParams(BaseModel):
    nombre_objeto: str = Field(
        ...,
        description="Nombre exacto del objeto al que quieres dirigirte (ej. 'CuboRojo', 'ArbolCentral').",
    )


class NavegarAPosicionParams(BaseModel):
    x: float = Field(..., description="Coordenada X del destino.")
    y: float = Field(..., description="Coordenada Y del destino.")
    z: float = Field(..., description="Coordenada Z del destino.")


# -------------------- Herramientas registradas --------------------
@ToolRegistry.register(
    name="navegar_a_objeto",
    description=(
        "Ordena a Unity que navegue autónomamente hacia el objeto indicado. "
        "El NavMeshAgent se encarga de esquivar obstáculos. Recibirás un evento cuando llegues o si falla."
    ),
    params_schema=NavegarAObjetoParams,
    requires_confirmation=False,
)
def navegar_a_objeto(params: NavegarAObjetoParams) -> str:
    return f"[Navegación] Orden de ir hacia '{params.nombre_objeto}' enviada al NavMesh de Unity."


@ToolRegistry.register(
    name="navegar_a_posicion",
    description=(
        "Ordena a Unity que navegue hacia las coordenadas (x, y, z) proporcionadas. "
        "Útil cuando conoces la posición exacta pero no el nombre del objeto."
    ),
    params_schema=NavegarAPosicionParams,
    requires_confirmation=False,
)
def navegar_a_posicion(params: NavegarAPosicionParams) -> str:
    return f"[Navegación] Orden de ir a la posición ({params.x}, {params.y}, {params.z}) enviada a Unity."