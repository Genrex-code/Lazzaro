"""
tools/simulated/mouse_sim.py - Simulación de mouse en entorno virtual.
"""

from tools.registry import ToolRegistry
from pydantic import BaseModel, Field
from tools.base import ToolResult

class MoverMouseParams(BaseModel):
    x: float = Field(..., description="Coordenada X en pantalla virtual")
    y: float = Field(..., description="Coordenada Y en pantalla virtual")

@ToolRegistry.register(
    name="mover_mouse",
    description="Mueve el cursor del mouse a (x,y) en el entorno simulado.",
    params_schema=MoverMouseParams
)
def mover_mouse(params: MoverMouseParams) -> ToolResult:
    mensaje = f"[Mouse Sim] Cursor movido a ({params.x}, {params.y})."
    return ToolResult(exito=True, mensaje=mensaje)


class ClickMouseParams(BaseModel):
    boton: str = Field("izquierdo", description="'izquierdo', 'derecho', 'medio'")

@ToolRegistry.register(
    name="click_mouse",
    description="Realiza un clic con el mouse virtual.",
    params_schema=ClickMouseParams
)
def click_mouse(params: ClickMouseParams) -> ToolResult:
    mensaje = f"[Mouse Sim] Clic {params.boton}."
    return ToolResult(exito=True, mensaje=mensaje)