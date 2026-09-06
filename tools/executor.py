"""
tools/executor.py - Ejecutor seguro de acciones.
Valida parámetros, aplica confirmaciones, ejecuta la herramienta y captura errores.
"""

from typing import Optional

from ai_engine.motor_schemas import PHYSICAL_NUMERIC_ACTIONS, validar_parametros_fisicos
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
            parametros = accion.parametros
            if accion.nombre in PHYSICAL_NUMERIC_ACTIONS:
                parametros = validar_parametros_fisicos(accion.nombre, parametros)
            params_validados = schema_cls(**parametros)
        except Exception as e:
            mensaje = f"Parámetros inválidos para {accion.nombre}: {e}"
            logger.warning(mensaje)
            return ToolResult(exito=False, mensaje=mensaje, error=str(e))

        # 3. Verificar confirmación requerida
        requires_confirmation = tool_info.get("requires_confirmation", False)
        if type(requires_confirmation) is not bool:
            mensaje = f"Contrato de confirmacion invalido para {accion.nombre}."
            logger.error(mensaje)
            return ToolResult(exito=False, mensaje=mensaje, error=mensaje)
        if requires_confirmation and accion.requiere_confirmacion is not True:
            mensaje = f"La herramienta {accion.nombre} requiere confirmación explícita."
            logger.warning(mensaje)
            return ToolResult(exito=False, mensaje=mensaje, error="Confirmación denegada automáticamente.")

        # 4. Ejecutar la función
        try:
            handler = tool_info["func"]
            resultado = handler(params_validados)
            # Los handlers productivos usan ToolResult o un mensaje legacy no vacio.
            if isinstance(resultado, ToolResult):
                logger.info("Herramienta %s ejecutada: %s", accion.nombre, resultado.mensaje)
                return resultado
            return_annotation = getattr(handler, "__annotations__", {}).get("return")
            if (
                return_annotation in (str, "str")
                and type(resultado) is str
                and resultado.strip()
                and resultado.strip().casefold() not in {"true", "false"}
            ):
                mensaje = resultado
                logger.info("Herramienta %s ejecutada: %s", accion.nombre, mensaje)
                return ToolResult(exito=True, mensaje=mensaje)

            tipo_resultado = "str_vacio" if type(resultado) is str else type(resultado).__name__
            mensaje = (
                f"Resultado invalido de {accion.nombre}: "
                f"contrato de retorno {tipo_resultado} no soportado."
            )
            logger.error(mensaje)
            return ToolResult(
                exito=False,
                mensaje=mensaje,
                error="Contrato de retorno invalido.",
            )
        except Exception as e:
            mensaje = f"Error al ejecutar {accion.nombre}: {e}"
            logger.error(mensaje, exc_info=True)
            return ToolResult(exito=False, mensaje=mensaje, error=str(e))
