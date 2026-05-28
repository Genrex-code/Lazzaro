from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    GEMINI_API_KEY: str = Field(..., description="API key de Gemini")
    GEMINI_MODEL_NAME: str = "gemini-3.1-flash-lite"
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_MAX_TOKENS: int = 1024
    CHROMA_DB_PATH: str = "./lazzaro_data"

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    @field_validator("GEMINI_API_KEY")
    @classmethod
    def key_no_vacia(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("GEMINI_API_KEY no puede estar vacia. Revisa tu .env")
        return value.strip()
