from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    GEMINI_API_KEY: str = "AIzaSyAKp3KVmaocAephDVdF1lfEDQ7rUeS_YRc"
    GEMINI_MODEL_NAME: str = "gemini-3.1-flash-lite"
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_MAX_TOKENS: int = 1024

    class Config:
        env_file = ".env"
        extra = "ignore"