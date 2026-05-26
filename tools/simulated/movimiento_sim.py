"""
tools/simulated/movimiento_sim.py - Acciones dentro del mundo virtual 3D.
"""

from tools.registry import ToolRegistry
from pydantic import BaseModel, Field

# 1. Definimos estrictamente qué necesita Lazzaro para moverse
class MoverseParams(BaseModel):
    direccion: str = Field(..., description="'adelante', 'atras', 'izquierda', 'derecha'")
    distancia: float = Field(..., description="Unidades o metros a recorrer en el entorno 3D")
    velocidad: str = Field("caminar", description="'caminar', 'correr' o 'sigilo'")

# 2. Registramos la herramienta
@ToolRegistry.register(
    name="moverse_entorno_3d",
    description="Mueve el avatar de Lazzaro dentro del entorno virtual (Roblox/Unity).",
    params_schema=MoverseParams,
    requires_confirmation=False
)
def moverse_entorno_3d(params: MoverseParams) -> str:
    """
    Como es simulado, no ejecutamos código físico.
    Devolvemos el string que Lazzaro leerá en su memoria de trabajo para saber qué hizo.
    El Orchestrator se encargará de mandar el JSON real a Roblox.
    """
    return f"[Mundo Virtual] Comando enviado al motor: {params.velocidad} hacia {params.direccion} por {params.distancia} unidades."


class InteraccionParams(BaseModel):
    nombre_objeto: str = Field(..., description="Nombre del objeto con el que interactuar (ej. 'puerta_principal')")
    tipo_accion: str = Field(..., description="'agarrar', 'presionar', 'mirar'")

@ToolRegistry.register(
    name="interactuar_objeto_3d",
    description="Permite a Lazzaro interactuar con elementos del mapa en Roblox o Unity.",
    params_schema=InteraccionParams
)
def interactuar_objeto_3d(params: InteraccionParams) -> str:
    return f"[Mundo Virtual] Intento de {params.tipo_accion} sobre el objeto '{params.nombre_objeto}'."