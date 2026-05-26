"""
tools/real/keyboard_real.py - Control real del teclado (¡SÓLO PARA SANDBOX!).
"""

from tools.registry import ToolRegistry
from pydantic import BaseModel, Field
from tools.base import ToolResult
from core.logger import get_logger

logger = get_logger(__name__)

class RealKeyboardParams(BaseModel):
    texto: str = Field(..., description="Texto a escribir en el sistema operativo real.")
    presionar_enter: bool = Field(False, description="Si debe presionar Enter después del texto.")

@ToolRegistry.register(
    name="keyboard_real",
    description="ESCRIBE TEXTO REAL EN EL SISTEMA. Requiere confirmación explícita y entorno sandbox.",
    params_schema=RealKeyboardParams,
    requires_confirmation=True
)
def keyboard_real(params: RealKeyboardParams) -> ToolResult:
    try:
        import pyautogui
        # Pequeña pausa de seguridad (se puede configurar)
        pyautogui.FAILSAFE = True
        pyautogui.write(params.texto)
        if params.presionar_enter:
            pyautogui.press('enter')
        mensaje = f"[Teclado Real] Texto escrito: '{params.texto}'{' + ENTER' if params.presionar_enter else ''}"
        logger.warning(mensaje)  # log de seguridad
        return ToolResult(exito=True, mensaje=mensaje)
    except ImportError:
        return ToolResult(exito=False, mensaje="pyautogui no instalado.", error="Falta librería")
    except Exception as e:
        return ToolResult(exito=False, mensaje="Error al usar teclado real.", error=str(e))