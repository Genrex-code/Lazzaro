"""
response_parser.py - Valida y limpia la respuesta JSON del LLM.
Convierte la salida cruda del modelo en una ActionResponse segura.
"""

import json
from typing import Optional
from ai_engine.schemas import LLMResponse, ActionResponse
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
        Convierte la cadena devuelta por Gemini en una acción validada.

        Args:
            texto_crudo: respuesta textual del LLM (debe contener un JSON).
            acciones_permitidas: conjunto opcional de nombres de acción válidos.

        Returns:
            ActionResponse con los campos normalizados y la bandera 'valido'.
        """
        # 1. Extraer la porción JSON del texto
        json_str = ResponseParser._extraer_json(texto_crudo)
        if json_str is None:
            logger.warning("No se encontró JSON en la respuesta del LLM. Texto: %s", texto_crudo[:200])
            return ActionResponse(
                pensamiento=texto_crudo[:200],
                accion="none",
                parametros={},
                razon="",
                valido=False,
                error="Respuesta sin JSON válido."
            )

        # 2. Parsear JSON
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
                error=f"JSON inválido: {e}"
            )

        # 3. Validar estructura con Pydantic
        try:
            respuesta = LLMResponse(**datos)
        except Exception as e:
            logger.warning("Respuesta JSON no cumple el esquema LLMResponse: %s", e)
            # Intentamos construir un ActionResponse de fallback con lo que haya
            pensamiento = datos.get("pensamiento", json_str[:200])
            accion = datos.get("accion", "none")
            parametros = datos.get("parametros", {})
            razon = datos.get("razon", "")
            return ActionResponse(
                pensamiento=pensamiento,
                accion=accion,
                parametros=parametros if isinstance(parametros, dict) else {},
                razon=razon,
                valido=False,
                error=f"Estructura JSON incorrecta: {e}"
            )

        # 4. Validar acción contra las permitidas (si se especifican)
        if acciones_permitidas is not None and respuesta.accion not in acciones_permitidas:
            logger.warning("Acción '%s' no está en la lista de permitidas: %s", respuesta.accion, acciones_permitidas)
            return ActionResponse(
                pensamiento=respuesta.pensamiento,
                accion=respuesta.accion,
                parametros=respuesta.parametros,
                razon=respuesta.razon,
                valido=False,
                error=f"Acción '{respuesta.accion}' no permitida."
            )

        # 5. Todo correcto
        logger.debug("Respuesta parseada correctamente. Acción: %s", respuesta.accion)
        return ActionResponse(
            pensamiento=respuesta.pensamiento,
            accion=respuesta.accion,
            parametros=respuesta.parametros,
            razon=respuesta.razon,
            valido=True
        )

    @staticmethod
    def _extraer_json(texto: str) -> Optional[str]:
        """
        Intenta aislar un bloque JSON del texto, manejando fences de Markdown
        o texto adicional alrededor.
        """
        if not texto:
            return None

        # Buscar el primer '{' y el último '}'
        inicio = texto.find('{')
        fin = texto.rfind('}')
        if inicio != -1 and fin != -1 and fin > inicio:
            return texto[inicio:fin + 1]

        # Si no hay llaves, no hay JSON
        return None