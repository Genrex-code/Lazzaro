"""
client_factory.py - Punto unico para seleccionar el proveedor LLM activo.

Si mas adelante vuelves a Gemini o agregas otro backend, este es el primer
archivo que conviene tocar.
"""

from ai_engine.ai_client import BaseAIClient
from ai_engine.fallback_ai_client import FallbackAIClient
from ai_engine.gemini_client import GeminiClient
from ai_engine.ollama_client import OllamaClient
from ai_engine.openai_compatible_client import OpenAICompatibleClient
from core.logger import get_logger

logger = get_logger(__name__)

PROVEEDORES_SOPORTADOS = ("gemini", "groq", "xai", "kimi", "deepseek_api", "ollama")
PROVEEDORES_FALLBACK_DEFAULT = ("gemini", "groq", "xai", "kimi", "deepseek_api")


def build_ai_client(settings) -> BaseAIClient:
    """Construye el cliente de IA segun el proveedor configurado y sus fallbacks."""
    proveedores = _resolver_orden_proveedores(settings)
    clientes: list[BaseAIClient] = []

    for proveedor in proveedores:
        try:
            cliente = _build_single_client(settings, proveedor)
        except ValueError as e:
            logger.info("Proveedor LLM %s omitido: %s", proveedor, e)
            continue
        clientes.append(cliente)

        if not getattr(settings, "LLM_FALLBACKS_ENABLED", True):
            break

    if not clientes:
        raise ValueError(
            "No hay proveedores LLM configurados. Revisa AI_PROVIDER, AI_FALLBACK_ORDER y API keys."
        )

    if len(clientes) == 1:
        return clientes[0]

    logger.info(
        "Cadena de fallback LLM activa: %s",
        " -> ".join(str(getattr(cliente, "provider_name", "?")) for cliente in clientes),
    )
    return FallbackAIClient(clientes, settings=settings)


def _build_single_client(settings, proveedor: str) -> BaseAIClient:
    """Construye un cliente concreto sin envolverlo en fallback."""
    proveedor = str(proveedor).strip().lower()
    if proveedor == "gemini":
        if not getattr(settings, "GEMINI_API_KEY", None):
            raise ValueError("GEMINI_API_KEY no esta configurada.")
        return GeminiClient(settings=settings)
    if proveedor == "ollama":
        return OllamaClient(settings=settings)
    if proveedor == "groq":
        return _build_openai_compatible(settings, "groq")
    if proveedor == "xai":
        return _build_openai_compatible(settings, "xai")
    if proveedor == "kimi":
        return _build_openai_compatible(settings, "kimi")
    if proveedor == "deepseek_api":
        return _build_openai_compatible(settings, "deepseek_api")
    raise ValueError(f"Proveedor de IA no soportado: {proveedor}")


def _build_openai_compatible(settings, proveedor: str) -> OpenAICompatibleClient:
    config = {
        "groq": ("GROQ_API_KEY", "GROQ_API_URL", "GROQ_MODEL"),
        "xai": ("XAI_API_KEY", "XAI_API_URL", "XAI_MODEL"),
        "kimi": ("KIMI_API_KEY", "KIMI_API_URL", "KIMI_MODEL"),
        "deepseek_api": ("DEEPSEEK_API_KEY", "DEEPSEEK_API_URL", "DEEPSEEK_API_MODEL"),
    }[proveedor]
    key_attr, url_attr, model_attr = config
    api_key = getattr(settings, key_attr, None)

    if not api_key:
        raise ValueError(f"{key_attr} no esta configurada.")

    return OpenAICompatibleClient(
        provider_name=proveedor,
        api_key=str(api_key),
        api_url=str(getattr(settings, url_attr)),
        model_name=str(getattr(settings, model_attr)),
        timeout=float(getattr(settings, "OPENAI_COMPAT_TIMEOUT", 45.0)),
        supports_json_format=(proveedor != "deepseek_api"),
        settings=settings,
    )


def describe_ai_backend(settings) -> str:
    """Devuelve una descripcion legible del backend activo para logs."""
    orden = _resolver_orden_proveedores(settings)
    partes = []
    for proveedor in orden:
        modelo = _modelo_para_proveedor(settings, proveedor)
        partes.append(f"{proveedor}:{modelo}")
    return " -> ".join(partes)


def _resolver_orden_proveedores(settings) -> list[str]:
    proveedor = str(getattr(settings, "AI_PROVIDER", "ollama")).strip().lower()
    orden_configurada = str(getattr(settings, "AI_FALLBACK_ORDER", "") or "").strip()
    if orden_configurada:
        candidatos = [item.strip().lower() for item in orden_configurada.split(",") if item.strip()]
    else:
        candidatos = [proveedor, *PROVEEDORES_FALLBACK_DEFAULT]

    orden: list[str] = []
    for candidato in candidatos:
        if candidato not in PROVEEDORES_SOPORTADOS:
            logger.warning("Proveedor LLM desconocido en orden de fallback: %s", candidato)
            continue
        if candidato not in orden:
            orden.append(candidato)

    return orden or [proveedor]


def _modelo_para_proveedor(settings, proveedor: str) -> str:
    if proveedor == "gemini":
        return str(getattr(settings, "GEMINI_MODEL_NAME", "gemini"))
    if proveedor == "ollama":
        return str(getattr(settings, "OLLAMA_MODEL_NAME", None) or getattr(
            settings,
            "DEEPSEEK_MODEL",
            "deepseek-r1:8b",
        ))
    if proveedor == "deepseek_api":
        return str(getattr(settings, "DEEPSEEK_API_MODEL", "deepseek-chat"))

    prefix = proveedor.upper()
    return str(getattr(settings, f"{prefix}_MODEL", proveedor))
