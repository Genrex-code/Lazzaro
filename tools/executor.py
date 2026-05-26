"""
tools/executor.py - Ejecutor seguro de acciones.
Valida parámetros, aplica confirmaciones, ejecuta la herramienta y captura errores.
"""

from typing import Optional
from api.schemas import AccionSaliente
from tools.base import ToolResult
from tools.registry import ToolRegistry
from core.logger import get_logger

logger = get_logger(__name__)


class ActionExecutor:
    """Responsable de llevar a cabo una acción decidida por el agente."""

    @staticmethod
    def ejecutar(accion: AccionSaliente) -> ToolResult:
        """
        Ejecuta la acción indicada.
        Retorna un ToolResult con el resultado de la herramienta o un error.
        """
        # 1. Buscar la herramienta registrada
        tool_info = ToolRegistry.get_tool(accion.nombre)
        if tool_info is None:
            mensaje = f"Herramienta no encontrada: {accion.nombre}"
            logger.error(mensaje)
            return ToolResult(exito=False, mensaje=mensaje, error=mensaje)

        # 2. Validar parámetros con el esquema Pydantic
        schema_cls = tool_info["schema"]
        try:
            params_validados = schema_cls(**accion.parametros)
        except Exception as e:
            mensaje = f"Parámetros inválidos para {accion.nombre}: {e}"
            logger.warning(mensaje)
            return ToolResult(exito=False, mensaje=mensaje, error=str(e))

        # 3. Verificar confirmación requerida
        if tool_info.get("requires_confirmation") and not accion.requiere_confirmacion:
            mensaje = f"La herramienta {accion.nombre} requiere confirmación explícita."
            logger.warning(mensaje)
            return ToolResult(exito=False, mensaje=mensaje, error="Confirmación denegada automáticamente.")

        # 4. Ejecutar la función
        try:
            resultado = tool_info["func"](params_validados)
            # La función puede devolver un string o un ToolResult; normalizamos
            if isinstance(resultado, ToolResult):
                logger.info("Herramienta %s ejecutada: %s", accion.nombre, resultado.mensaje)
                return resultado
            else:
                mensaje = str(resultado)
                logger.info("Herramienta %s ejecutada: %s", accion.nombre, mensaje)
                return ToolResult(exito=True, mensaje=mensaje)
        except Exception as e:
            mensaje = f"Error al ejecutar {accion.nombre}: {e}"
            logger.error(mensaje, exc_info=True)
            return ToolResult(exito=False, mensaje=mensaje, error=str(e))