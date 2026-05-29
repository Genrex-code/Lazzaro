from __future__ import annotations

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Cambio rapido de backend:
    # - "ollama" para DeepSeek local
    # - "gemini" para volver al proveedor remoto
    AI_PROVIDER: str = Field("ollama", description="Proveedor LLM activo: ollama o gemini")

    GEMINI_API_KEY: str | None = Field(default=None, description="API key de Gemini")
    GEMINI_MODEL_NAME: str = "gemini-3.1-flash-lite"
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_MAX_TOKENS: int = 1024

    OLLAMA_HOST: str = "127.0.0.1"
    OLLAMA_PORT: int = 11434
    OLLAMA_BASE_URL: str | None = None
    OLLAMA_MODEL_NAME: str | None = None
    OLLAMA_TIMEOUT_SECONDS: float = 120.0

    CHROMA_DB_PATH: str = "./lazzaro_data"
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
        if proveedor not in {"ollama", "gemini"}:
            raise ValueError("AI_PROVIDER debe ser 'ollama' o 'gemini'.")
        return proveedor

    @field_validator("GEMINI_API_KEY")
    @classmethod
    def normalizar_gemini_api_key(cls, value: str | None) -> str | None:
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

    @field_validator("OLLAMA_TIMEOUT_SECONDS")
    @classmethod
    def timeout_positivo(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("OLLAMA_TIMEOUT_SECONDS debe ser mayor que cero.")
        return value

    @model_validator(mode="after")
    def validar_configuracion_proveedor(self) -> "Settings":
        if self.AI_PROVIDER == "gemini" and not self.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY no puede estar vacia cuando AI_PROVIDER='gemini'.")
        return self
