"""Server-side configuration. No provider token is placed in the React bundle."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    demo_mode: bool = True
    shopsync_demo_owner_token: str = ""
    mongodb_uri: str | None = None
    mongodb_db: str = "shopsync_ai"
    # Only one provider handles a request. Ollama runs locally and needs no key.
    llm_provider: Literal["gemini", "ollama"] = "gemini"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:1.5b"
    ollama_timeout_seconds: float = Field(default=60.0, ge=5.0, le=300.0)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_synthetic_demo: bool = False
    # Classifying a real request can take longer than the three-second SRS
    # routing target. Keep an explicit bound and report slow routing to Owner.
    gemini_classify_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cache configuration per process; tests can clear this cache after env changes."""
    return Settings()
