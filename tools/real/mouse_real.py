"""
tools/real/mouse_real.py - Control real del mouse (¡SÓLO PARA SANDBOX!).
"""

from tools.registry import ToolRegistry
from pydantic import BaseModel, Field
from tools.base import ToolResult
from core.logger import get_logger

logger = get_logger(__name__)

class RealMouseMoveParams(BaseModel):
    x: int = Field(..., description="Coordenada X en píxeles")
    y: int = Field(..., description="Coordenada Y en píxeles")

@ToolRegistry.register(
    name="mouse_real_move",
    description="MUEVE EL MOUSE REAL. Requiere confirmación y sandbox.",
    params_schema=RealMouseMoveParams,
    requires_confirmation=True
)
def mouse_real_move(params: RealMouseMoveParams) -> ToolResult:
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.moveTo(params.x, params.y)
        mensaje = f"[Mouse Real] Movido a ({params.x}, {params.y})."
        logger.warning(mensaje)
        return ToolResult(exito=True, mensaje=mensaje)
    except ImportError:
        return ToolResult(exito=False, mensaje="pyautogui no instalado.", error="Falta librería")
    except Exception as e:
        return ToolResult(exito=False, mensaje="Error al mover mouse real.", error=str(e))


class RealMouseClickParams(BaseModel):
    boton: str = Field("left", description="'left', 'right', 'middle'")

@ToolRegistry.register(
    name="mouse_real_click",
    description="HACE CLIC REAL. Requiere confirmación.",
    params_schema=RealMouseClickParams,
    requires_confirmation=True
)
def mouse_real_click(params: RealMouseClickParams) -> ToolResult:
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        pyautogui.click(button=params.boton)
        mensaje = f"[Mouse Real] Clic {params.boton}."
        logger.warning(mensaje)
        return ToolResult(exito=True, mensaje=mensaje)
    except ImportError:
        return ToolResult(exito=False, mensaje="pyautogui no instalado.", error="Falta librería")
    except Exception as e:
        return ToolResult(exito=False, mensaje="Error al hacer clic real.", error=str(e))