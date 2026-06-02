"""
persona_engine.py - Capa 2 Orquestador Social.
Se encarga de manejar la chalra pura. prepara el prompt dinamico,
gestiona la salida del LLM  y garantiza que la unica accion devuelta sea "hablar".
"""
#Enserio duerman a este tipo ya me canse de las bromas tontas que escribe con las variables (Claude)

import json
import re
from typing import Dict, Any, List, Optional
from core.logger import get_logger
from .persona_prompt_buider import PersonaPromptBuilder
from .identity_state import IdentityState

logger = get_logger(__name__)


class PersonaEngine:
    """
    Cerebro conversacional de Lazzaro.
    No ejecuta herramientas físicas; solo gestiona el diálogo.
    """

    def __init__(self):
        self.prompt_builder = PersonaPromptBuilder()

    # ------------------------------------------------------------------
    # Construcción del prompt
    # ------------------------------------------------------------------

    def preparar_interaccion(
        self,
        mensaje_usuario: str,
        estado_biologico: IdentityState,
        percepcion: Optional[Dict[str, Any]] = None,
        recuerdos: str = "",
    ) -> List[Dict[str, str]]:
        """
        Prepara la lista de mensajes [system, user] para enviar al LLM local.

        Args:
            mensaje_usuario: texto del usuario.
            estado_biologico: instancia de IdentityState con el estado actual.
            percepcion: datos perceptuales enviados por Unity (puede ser None).
            recuerdos: texto con recuerdos relevantes.

        Returns:
            Lista de mensajes en formato compatible con la API del LLM.
        """
        percepcion = percepcion or {}
        percepcion_texto = percepcion.get("resumen_percepcion", "")

        estado_inyectado = estado_biologico.inyectar_en_prompt()

        system_prompt = self.prompt_builder.build_system_prompt(
            estado_inyectado=estado_inyectado,
            percepcion_texto=percepcion_texto,
            recuerdos=recuerdos,
        ) or ""

        # Bloqueo de seguridad: forzar JSON estricto
        system_prompt += (
            "\n=== FORMATO DE SALIDA ESTRICTO ===\n"
            "Responde ÚNICAMENTE con un objeto JSON válido. Nada de texto fuera del JSON.\n"
            "Estructura requerida:\n"
            "{\n"
            '  "accion": "hablar",\n'
            '  "parametros": {\n'
            '    "texto_a_comunicar": "Tu respuesta como Lazzaro aquí"\n'
            '  },\n'
            '  "pensamiento_privado": "Breve reflexión interna antes de hablar",\n'
            '  "razon": "Motivo por el que dices esto"\n'
            "}"
        )

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": mensaje_usuario},
        ]

    # ------------------------------------------------------------------
    # Parseo de la respuesta del LLM
    # ------------------------------------------------------------------

    def parsear_respuesta(self, respuesta_llm: str) -> Dict[str, Any]:
        """
        Extrae y valida el JSON de la respuesta cruda del LLM.
        Si el modelo alucina texto libre, lo degrada a un mensaje seguro.
        """
        if not respuesta_llm or not respuesta_llm.strip():
            logger.warning("Respuesta del LLM vacía.")
            return self._fallback("Me quedé en blanco por un momento.")

        try:
            # Limpiar fences de Markdown y otros adornos
            texto_limpio = self._limpiar_json(respuesta_llm)
            data = json.loads(texto_limpio)
        except json.JSONDecodeError:
            logger.error("El LLM no devolvió JSON válido. Respuesta cruda: %.200s", respuesta_llm)
            return self._fallback("Sentí un cortocircuito en mi lenguaje, no pude procesar eso.")

        # Forzar que la acción sea exclusivamente 'hablar'
        accion = data.get("accion", "none")
        if accion != "hablar":
            logger.warning("Acción '%s' bloqueada en Capa 2. Forzando 'hablar'.", accion)
            data["accion"] = "hablar"

        # Validar que el texto a comunicar existe y no está vacío
        parametros = data.get("parametros", {})
        if not parametros.get("texto_a_comunicar"):
            logger.warning("El JSON no contiene 'texto_a_comunicar'. Se usa fallback.")
            return self._fallback("No sé qué decir, mi mente divagó.")

        # Asegurar campos mínimos
        data.setdefault("pensamiento_privado", "")
        data.setdefault("razon", "")
        return data

    # ------------------------------------------------------------------
    # Helpers internos
    # ------------------------------------------------------------------

    @staticmethod
    def _limpiar_json(texto_crudo: str) -> str:
        """
        Elimina bloques de Markdown y espacios sobrantes.
        Soporta:
        - ```json ... ```
        - ``` ... ```
        - Texto antes/después del JSON
        """
        texto = texto_crudo.strip()

        # Eliminar fences de Markdown
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)

        # Si aún no empieza por '{', intentar extraer el primer objeto JSON
        if not texto.startswith("{"):
            match = re.search(r"\{.*\}", texto, re.DOTALL)
            if match:
                texto = match.group(0)

        return texto.strip()

    def _fallback(self, mensaje_seguridad: str) -> Dict[str, Any]:
        """Rescate cuando el LLM falla por completo."""
        logger.info("Activando fallback de seguridad en Capa 2.")
        return {
            "accion": "hablar",
            "parametros": {"texto_a_comunicar": mensaje_seguridad},
            "pensamiento_privado": "Fallback de seguridad por fallo del LLM.",
            "razon": "Error interno.",
        }