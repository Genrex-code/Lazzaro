"""
response_parser.py - Valida y limpia la respuesta JSON del LLM.
Convierte la salida cruda del modelo en una ActionResponse segura.
"""

import json
from typing import Optional

from ai_engine.schemas import ActionResponse, LLMResponse
from core.logger import get_logger

logger = get_logger(__name__)


class ResponseParser:
    """
    Parsea la respuesta cruda del LLM, la valida contra el esquema esperado
    y produce una ActionResponse lista para ser ejecutada (o rechazada).
    """

    @staticmethod
    def parse(texto_crudo: str, acciones_permitidas: Optional[set[str]] = None) -> ActionResponse:
        """
        Convierte la cadena devuelta por Gemini en una accion validada.

        Args:
            texto_crudo: respuesta textual del LLM (debe contener un JSON).
            acciones_permitidas: conjunto opcional de nombres de accion validos.

        Returns:
            ActionResponse con los campos normalizados y la bandera 'valido'.
        """
        json_str = ResponseParser._extraer_json(texto_crudo)
        if json_str is None:
            logger.warning("No se encontro JSON en la respuesta del LLM. Texto: %s", texto_crudo[:200])
            return ActionResponse(
                pensamiento=texto_crudo[:200],
                accion="none",
                parametros={},
                razon="",
                valido=False,
                error="Respuesta sin JSON valido.",
            )

        try:
            datos = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("JSON malformado del LLM: %s", e)
            return ActionResponse(
                pensamiento=json_str[:200],
                accion="none",
                parametros={},
                razon="",
                valido=False,
                error=f"JSON invalido: {e}",
            )

        try:
            respuesta = LLMResponse(**datos)
        except Exception as e:
            logger.warning("Respuesta JSON no cumple el esquema LLMResponse: %s", e)
            pensamiento = datos.get("pensamiento", json_str[:200])
            accion = ResponseParser._normalizar_accion(datos.get("accion"))
            parametros = datos.get("parametros", {})
            razon = datos.get("razon", "")
            return ActionResponse(
                pensamiento=pensamiento,
                accion=accion,
                parametros=parametros if isinstance(parametros, dict) else {},
                razon=razon,
                valido=False,
                error=f"Estructura JSON incorrecta: {e}",
            )

        accion_normalizada = ResponseParser._normalizar_accion(respuesta.accion)

        if (
            acciones_permitidas is not None
            and accion_normalizada != "none"
            and accion_normalizada not in acciones_permitidas
        ):
            logger.warning(
                "Accion '%s' no esta en la lista de permitidas: %s",
                accion_normalizada,
                acciones_permitidas,
            )
            return ActionResponse(
                pensamiento=respuesta.pensamiento,
                accion=accion_normalizada,
                parametros=respuesta.parametros,
                razon=respuesta.razon,
                valido=False,
                error=f"Accion '{accion_normalizada}' no permitida.",
            )

        logger.debug("Respuesta parseada correctamente. Accion: %s", accion_normalizada)
        return ActionResponse(
            pensamiento=respuesta.pensamiento,
            accion=accion_normalizada,
            parametros=respuesta.parametros,
            razon=respuesta.razon,
            valido=True,
        )

    @staticmethod
    def _extraer_json(texto: str) -> Optional[str]:
        """
        Intenta aislar un bloque JSON del texto, manejando fences de Markdown
        o texto adicional alrededor.
        """
        if not texto:
            return None

        inicio = texto.find("{")
        fin = texto.rfind("}")
        if inicio != -1 and fin != -1 and fin > inicio:
            return texto[inicio : fin + 1]

        return None

    @staticmethod
    def _normalizar_accion(accion: object) -> str:
        """Normaliza la accion del LLM a un nombre de herramienta o 'none'."""
        if accion is None or not isinstance(accion, str):
            return "none"

        accion_limpia = accion.strip()
        if not accion_limpia:
            return "none"
        if accion_limpia.lower() in {"none", "null"}:
            return "none"
        return accion_limpia
