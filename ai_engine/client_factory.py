"""
client_factory.py - Punto unico para seleccionar el proveedor LLM activo.

Si mas adelante vuelves a Gemini o agregas otro backend, este es el primer
archivo que conviene tocar.
"""

from ai_engine.ai_client import BaseAIClient
from ai_engine.gemini_client import GeminiClient
from ai_engine.ollama_client import OllamaClient


def build_ai_client(settings) -> BaseAIClient:
    """Construye el cliente de IA segun el proveedor configurado."""
    proveedor = str(getattr(settings, "AI_PROVIDER", "ollama")).strip().lower()
    if proveedor == "gemini":
        return GeminiClient(settings=settings)
    if proveedor == "ollama":
        return OllamaClient(settings=settings)
    raise ValueError(f"Proveedor de IA no soportado: {proveedor}")


def describe_ai_backend(settings) -> str:
    """Devuelve una descripcion legible del backend activo para logs."""
    proveedor = str(getattr(settings, "AI_PROVIDER", "ollama")).strip().lower()
    if proveedor == "gemini":
        modelo = getattr(settings, "GEMINI_MODEL_NAME", "gemini")
    else:
        modelo = getattr(settings, "OLLAMA_MODEL_NAME", None) or getattr(
            settings,
            "DEEPSEEK_MODEL",
            "deepseek-r1:8b",
        )
    return f"{proveedor}:{modelo}"
