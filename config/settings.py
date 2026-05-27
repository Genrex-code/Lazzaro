from pydantic_settings import BaseSettings
from pydantic import validator

@validator("GEMINI_API_KEY")
def key_no_vacia(cls, v):
    if not v or not v.strip():
        raise ValueError("GEMINI_API_KEY no puede estar vacía. Revisa tu .env")
    return v

class Settings(BaseSettings):
    GEMINI_API_KEY: str = "AIzaSyAKp3KVmaocAephDVdF1lfEDQ7rUeS_YRc"
    GEMINI_MODEL_NAME: str = "gemini-3.1-flash-lite"
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_MAX_TOKENS: int = 1024
    CHROMA_DB_PATH: str = "./lazzaro_data"
    class Config:
        env_file = ".env"
        extra = "ignore"