"""
tools/simulated/keyboard_sim.py - Simulación de teclado en entorno virtual.
"""

from tools.registry import ToolRegistry
from pydantic import BaseModel, Field
from tools.base import ToolResult

class EscribirTextoParams(BaseModel):
    texto: str = Field(..., description="Texto a escribir")
    presionar_enter: bool = Field(False, description="Si debe presionar Enter al final")

@ToolRegistry.register(
    name="escribir_texto",
    description="Simula la escritura de texto en Roblox/Unity.",
    params_schema=EscribirTextoParams
)
def escribir_texto(params: EscribirTextoParams) -> ToolResult:
    # En un entorno real, este string se enviaría como comando al motor 3D
    mensaje = f"[Teclado Sim] Texto escrito: '{params.texto}'{' + ENTER' if params.presionar_enter else ''}"
    return ToolResult(exito=True, mensaje=mensaje)


class TeclaEspecialParams(BaseModel):
    tecla: str = Field(..., description="Tecla especial: 'escape', 'tab', 'espacio', etc.")

@ToolRegistry.register(
    name="presionar_tecla_especial",
    description="Simula la pulsación de una tecla especial en el entorno virtual.",
    params_schema=TeclaEspecialParams
)
def presionar_tecla_especial(params: TeclaEspecialParams) -> ToolResult:
    mensaje = f"[Teclado Sim] Tecla especial '{params.tecla}' presionada."
    return ToolResult(exito=True, mensaje=mensaje)