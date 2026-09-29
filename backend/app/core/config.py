from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables (and `.env` for local runs)."""

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CRE AI Deal Intelligence"
    environment: Literal["local", "staging", "production", "test"] = "local"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Database ---
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "cre_deal_intel"
    postgres_user: str = "cre"
    postgres_password: SecretStr = SecretStr("cre")
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- AI providers (keys optional until features use them) ---
    anthropic_api_key: SecretStr | None = None
    # Required only when the API key is not scoped to a single workspace.
    anthropic_workspace_id: str | None = None
    anthropic_model: str = "claude-opus-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    anthropic_max_tokens: int = 16000
    # Upload requests wait on Claude synchronously; keep timeout x (retries + 1) under the
    # frontend proxy's 300s response-header timeout.
    anthropic_timeout_seconds: float = 120.0
    anthropic_max_retries: int = 1

    voyage_api_key: SecretStr | None = None
    voyage_embedding_model: str = "voyage-4-large"
    embedding_dimensions: int = 1024

    cohere_api_key: SecretStr | None = None
    cohere_rerank_model: str = "rerank-v4.0-pro"

    # --- Email ingestion ---
    max_email_bytes: int = 10 * 1024 * 1024
    max_deal_candidates: int = 200

    def secret_values(self) -> list[str]:
        """Configured secrets, for redaction in logs and error messages."""
        secrets = (self.anthropic_api_key, self.voyage_api_key, self.cohere_api_key, self.postgres_password)
        return [v for s in secrets if s is not None and len(v := s.get_secret_value()) >= 8]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """SQLAlchemy URL using psycopg 3 (works for both sync Alembic and async app engine)."""
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+psycopg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
