"""Application configuration via environment variables."""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "DevOnboard AI"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION_devonboard_secret_key_2024"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./devonboard.db"
    DATABASE_SYNC_URL: str = "sqlite:///./devonboard.db"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_AVAILABLE: bool = False  # Set to True when Redis is running

    # AI Provider
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    COMPLETION_MODEL: str = "gpt-4o-mini"

    # Login providers
    GOOGLE_CLIENT_ID: Optional[str] = None
    MICROSOFT_CLIENT_ID: Optional[str] = None
    MICROSOFT_TENANT_ID: Optional[str] = None

    @property
    def AI_AVAILABLE(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    # Analysis limits
    MAX_REPO_FILES: int = 50_000
    MAX_REPO_SIZE_BYTES: int = 2 * 1024 * 1024 * 1024  # 2GB
    MAX_FILE_SIZE_BYTES: int = 1 * 1024 * 1024  # 1MB per file
    ANALYSIS_TIMEOUT_SECONDS: int = 600
    ARCHITECTURE_CLUSTER_THRESHOLD: int = 200
    PARSE_PROGRESS_INTERVAL: int = 25
    PARSE_CACHE_DIR: str = "./.devonboard-cache"

    # RAG
    CONFIDENCE_THRESHOLD: float = 0.4
    TOP_K_RETRIEVAL: int = 8
    ABSTAIN_CONFIDENCE: float = 0.3

    # Rate limiting (requests per minute per tenant)
    RATE_LIMIT_PER_MINUTE: int = 60

    # CORS
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:3001"]

    # Observability
    OTEL_ENABLED: bool = False
    OTEL_ENDPOINT: str = "http://localhost:4317"

    # Repo storage
    REPOS_DIR: str = "./repos"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
