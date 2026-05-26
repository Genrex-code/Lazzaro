"""
ai_client.py - Interfaz abstracta para clientes de modelos de lenguaje.
Define el contrato que deben cumplir todas las implementaciones (Gemini, OpenAI, etc.).
"""

from abc import ABC, abstractmethod


class BaseAIClient(ABC):
    """
    Cliente abstracto para interactuar con un modelo de lenguaje.
    Las implementaciones concretas se encargarán de la autenticación,
    la construcción de la petición HTTP y el parseo de la respuesta cruda.
    """

    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        """
        Envía un prompt al modelo y devuelve el texto generado.

        Args:
            prompt: texto completo del prompt.
            **kwargs: parámetros adicionales específicos del modelo
                      (temperatura, max_tokens, stop_sequences, etc.).

        Returns:
            Respuesta textual del modelo (sin procesar).
        """
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """
        Verifica rápidamente si el cliente puede comunicarse con el servicio.

        Returns:
            True si el servicio está accesible, False en caso contrario.
        """
        ...