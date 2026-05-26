"""
gemini_client.py - Implementación concreta de BaseAIClient para Google Gemini.
Maneja autenticación, envío de prompts y recuperación de respuestas.
"""

import time
from typing import Optional

import google.generativeai as genai
from google.generativeai.types import GenerationConfig

from ai_engine.ai_client import BaseAIClient
from core.logger import get_logger
from config.settings import Settings

logger = get_logger(__name__)


class GeminiClient(BaseAIClient):
    """
    Cliente para interactuar con la API de Google Gemini.
    """

    def __init__(self, settings: Optional[Settings] = None):
        """
        Args:
            settings: instancia de config.settings (si es None, se crea una por defecto).
        """
        self.settings = settings or Settings()
        genai.configure(api_key=self.settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(
            model_name=self.settings.GEMINI_MODEL_NAME or "models/gemini-2.0-flash",
        )
        self.generation_config = GenerationConfig(
            temperature=self.settings.GEMINI_TEMPERATURE if hasattr(self.settings, 'GEMINI_TEMPERATURE') else 0.7,
            max_output_tokens=self.settings.GEMINI_MAX_TOKENS if hasattr(self.settings, 'GEMINI_MAX_TOKENS') else 1024,
            top_p=0.95,
            top_k=40,
        )
        logger.info("Cliente Gemini inicializado con modelo '%s'.", self.settings.GEMINI_MODEL_NAME)

    def generate(self, prompt: str, **kwargs) -> str:
        """
        Envía el prompt a Gemini y devuelve el texto generado.
        Soporta reintentos básicos ante errores transitorios.
        """
        # Permitir sobrescribir temperatura/max_tokens por llamada
        gen_config = self.generation_config
        if 'temperature' in kwargs:
            gen_config = gen_config.copy(update={'temperature': kwargs['temperature']})
        if 'max_tokens' in kwargs:
            gen_config = gen_config.copy(update={'max_output_tokens': kwargs['max_tokens']})

        max_retries = 3
        for intento in range(1, max_retries + 1):
            try:
                response = self.model.generate_content(
                    prompt,
                    generation_config=gen_config,
                )
                # Extraer texto de la respuesta
                texto = response.text or ""
                logger.debug("Gemini respondió con %d caracteres.", len(texto))
                return texto

            except Exception as e:
                logger.warning("Error en llamada a Gemini (intento %d/%d): %s", intento, max_retries, str(e))
                if intento < max_retries:
                    time.sleep(2 ** intento)  # backoff exponencial
                else:
                    logger.error("Todos los intentos fallaron. Devolviendo cadena vacía.")
                    return ""

    def health_check(self) -> bool:
        """
        Verifica que el servicio de Gemini esté accesible realizando una consulta mínima.
        """
        try:
            self.generate("Responde solo con 'ok'.")
            return True
        except Exception as e:
            logger.error("Health check de Gemini falló: %s", e)
            return False