from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "app/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    GROQ_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    QDRANT_URL: Optional[str] = None
    QDRANT_API_KEY: Optional[str] = None
    LLAMA_CLOUD_API_KEY: Optional[str] = None

    GROQ_MODEL: str = "qwen/qwen3.8-27b"
    GEMINI_MODEL: str = "gemini-3.8-flash"


settings = Settings()
