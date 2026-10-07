from __future__ import annotations

import math

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Cambio rapido de backend:
    # - "ollama" para DeepSeek local
    # - "gemini" para volver al proveedor remoto
    # - "groq", "xai", "kimi" o "deepseek_api" para APIs externas compatibles
    AI_PROVIDER: str = Field("gemini", description="Proveedor LLM primario.")
    AI_FALLBACK_ORDER: str | None = Field(
        default=None,
        description=(
            "Lista opcional separada por comas. Ej: gemini,groq,xai,kimi,deepseek_api,ollama"
        ),
    )
    LLM_FALLBACKS_ENABLED: bool = True
    LLM_TOTAL_BUDGET_SECONDS: float = 45.0

    GEMINI_API_KEY: str | None = Field(default=None, description="API key de Gemini")
    GEMINI_MODEL_NAME: str = "gemini-3.1-flash-lite"
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_MAX_TOKENS: int = 1024

    OLLAMA_HOST: str = "127.0.0.1"
    OLLAMA_PORT: int = 11434
    OLLAMA_BASE_URL: str | None = None
    OLLAMA_MODEL_NAME: str | None = None
    OLLAMA_TIMEOUT_SECONDS: float = 120.0

    ORACLE_API_URL: str = "https://api.groq.com/openai/v1/chat/completions"
    ORACLE_API_KEY: str | None = None
    ORACLE_MODEL: str | None = None
    ORACLE_TIMEOUT: float = 15.0

    GROQ_API_KEY: str | None = Field(default=None, description="API key de Groq")
    GROQ_API_URL: str = "https://api.groq.com/openai/v1/chat/completions"
    GROQ_MODEL: str = "llama-3.1-8b-instant"

    XAI_API_KEY: str | None = Field(default=None, description="API key de xAI")
    XAI_API_URL: str = "https://api.x.ai/v1/chat/completions"
    XAI_MODEL: str = "grok-3-mini"

    KIMI_API_KEY: str | None = Field(default=None, description="API key de Kimi/Moonshot")
    KIMI_API_URL: str = "https://api.moonshot.ai/v1/chat/completions"
    KIMI_MODEL: str = "moonshot-v1-8k"

    DEEPSEEK_API_KEY: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "DEEPSEEEK_API_KEY"),
        description="API key de DeepSeek remoto. Acepta tambien DEEPSEEEK_API_KEY por compatibilidad.",
    )
    DEEPSEEK_API_URL: str = "https://api.deepseek.com/chat/completions"
    DEEPSEEK_API_MODEL: str = "deepseek-chat"
    OPENAI_COMPAT_TIMEOUT: float = 45.0

    CHROMA_DB_PATH: str = "./lazzaro_data"
    ACTION_LEDGER_PATH: str = "./lazzaro_data/action_ledger.sqlite3"
    MEMORY_WRITE_LEDGER_PATH: str = "./lazzaro_data/memory_write_ledger.sqlite3"
    PERCEPTION_FRESHNESS_POLICY_ID: str | None = None
    PERCEPTION_MAX_CAPTURE_AGE_MS: int | None = None
    PERCEPTION_MAX_FUTURE_SKEW_MS: int | None = None
    PERCEPTION_AUTHORITY_POLICY_ID: str | None = None
    PERCEPTION_GENERATION_BINDING_MODE: str | None = None
    DEEPSEEK_MODEL: str = "deepseek-r1:8b"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        str_strip_whitespace=True,
    )

    @field_validator("AI_PROVIDER")
    @classmethod
    def proveedor_valido(cls, value: str) -> str:
        proveedor = value.strip().lower()
        permitidos = {"ollama", "gemini", "groq", "xai", "kimi", "deepseek_api"}
        if proveedor not in permitidos:
            raise ValueError(f"AI_PROVIDER debe ser uno de: {', '.join(sorted(permitidos))}.")
        return proveedor

    @field_validator(
        "GEMINI_API_KEY",
        "ORACLE_API_KEY",
        "GROQ_API_KEY",
        "XAI_API_KEY",
        "KIMI_API_KEY",
        "DEEPSEEK_API_KEY",
    )
    @classmethod
    def normalizar_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        valor = value.strip()
        return valor or None

    @field_validator("OLLAMA_BASE_URL")
    @classmethod
    def normalizar_ollama_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        valor = value.strip().rstrip("/")
        return valor or None

    @field_validator(
        "PERCEPTION_FRESHNESS_POLICY_ID",
        "PERCEPTION_AUTHORITY_POLICY_ID",
        "PERCEPTION_GENERATION_BINDING_MODE",
    )
    @classmethod
    def normalizar_config_percepcion(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator(
        "PERCEPTION_MAX_CAPTURE_AGE_MS",
        "PERCEPTION_MAX_FUTURE_SKEW_MS",
        mode="before",
    )
    @classmethod
    def milisegundos_percepcion(cls, value: object) -> int | None:
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            raise ValueError("Los limites perceptivos deben ser enteros no negativos.")
        try:
            normalized = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Los limites perceptivos deben ser enteros no negativos."
            ) from error
        if str(normalized) != str(value).strip() or normalized < 0:
            raise ValueError("Los limites perceptivos deben ser enteros no negativos.")
        return normalized

    @field_validator(
        "LLM_TOTAL_BUDGET_SECONDS",
        "OLLAMA_TIMEOUT_SECONDS",
        "ORACLE_TIMEOUT",
        "OPENAI_COMPAT_TIMEOUT",
        mode="before",
    )
    @classmethod
    def timeout_positivo(cls, value: object) -> float:
        if isinstance(value, bool):
            raise ValueError("Los budgets/timeouts deben ser numeros finitos mayores que cero.")
        try:
            normalized = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Los budgets/timeouts deben ser numeros finitos mayores que cero."
            ) from error
        if not math.isfinite(normalized) or normalized <= 0:
            raise ValueError("Los budgets/timeouts deben ser numeros finitos mayores que cero.")
        return normalized
