"""
tools/base.py - Tipos base y resultado estándar para todas las herramientas.
"""

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    """Resultado de ejecutar una herramienta."""
    exito: bool
    mensaje: str = ""
    error: Optional[str] = None
    datos_extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.exito) is not bool:
            raise TypeError("ToolResult.exito debe ser un bool estricto")
