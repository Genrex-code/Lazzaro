"""
fallback_ai_client.py - Cadena de respaldo para clientes LLM.

El resto del sistema llama generate() una sola vez. Este wrapper intenta el
cliente primario y, si no produce texto util, pasa al siguiente proveedor.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ai_engine.ai_client import BaseAIClient
from core.logger import get_logger

logger = get_logger(__name__)


class FallbackAIClient(BaseAIClient):
    """Cliente compuesto que prueba varios proveedores en orden."""

    provider_name = "fallback"

    def __init__(self, clients: Iterable[BaseAIClient], settings: Any | None = None):
        self.clients = [client for client in clients if client is not None]
        if not self.clients:
            raise ValueError("FallbackAIClient requiere al menos un cliente LLM.")

        self.settings = settings or getattr(self.clients[0], "settings", None)
        self.provider_order = [
            str(getattr(client, "provider_name", client.__class__.__name__)).strip()
            for client in self.clients
        ]
        # Mantener el nombre del proveedor primario evita activar reglas de politica
        # de otros fallbacks (por ejemplo Ollama) antes de saber si se usaran.
        self.provider_name = self.provider_order[0]
        self.fallback_chain_name = "fallback:" + "->".join(self.provider_order)
        self.last_provider_used: str | None = None

    def generate(self, prompt: str, **kwargs) -> str:
        """Intenta generar texto con cada proveedor hasta obtener una respuesta no vacia."""
        for client in self.clients:
            proveedor = str(getattr(client, "provider_name", client.__class__.__name__))
            try:
                texto = client.generate(prompt, **kwargs)
            except Exception as e:
                logger.warning("Proveedor LLM %s fallo con excepcion: %s", proveedor, e)
                texto = ""

            if isinstance(texto, str) and texto.strip():
                self.last_provider_used = proveedor
                if proveedor != self.provider_order[0]:
                    logger.warning("Fallback LLM activado. Respuesta obtenida desde %s.", proveedor)
                return texto

            logger.warning("Proveedor LLM %s no produjo texto util; probando fallback.", proveedor)

        self.last_provider_used = None
        logger.error("Todos los proveedores LLM fallaron o devolvieron texto vacio.")
        return ""

    def health_check(self) -> bool:
        """El sistema se considera operativo si al menos un proveedor responde."""
        for client in self.clients:
            proveedor = str(getattr(client, "provider_name", client.__class__.__name__))
            try:
                if client.health_check():
                    logger.info("Health check LLM OK usando %s.", proveedor)
                    return True
            except Exception as e:
                logger.warning("Health check LLM fallo en %s: %s", proveedor, e)
        return False

    def close(self) -> None:
        for client in self.clients:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as e:
                    proveedor = str(getattr(client, "provider_name", client.__class__.__name__))
                    logger.warning("No se pudo cerrar proveedor %s: %s", proveedor, e)
