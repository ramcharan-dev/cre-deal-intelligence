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
    llm_provider: str = ""
    llm_model: str = ""

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

    # Which model runs email extraction. `ollama` uses a local Ollama server with the same prompt and schema.
    extraction_provider: Literal["claude", "ollama"] = "claude"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    # Must fit the prompt (instructions + candidate deals + email) plus the JSON output.
    ollama_num_ctx: int = 16384
    # Keep under the frontend proxy's 300s response-header timeout.
    ollama_timeout_seconds: float = 280.0

    voyage_api_key: SecretStr | None = None
    voyage_embedding_model: str = "voyage-4-large"
    embedding_dimensions: int = 1024

    cohere_api_key: SecretStr | None = None
    cohere_rerank_model: str = "rerank-v4.0-pro"

    # --- Copilot AI providers ---
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: float = 30.0

    groq_api_key: SecretStr | None = None
    groq_model: str = "llama-3.3-70b-versatile"
    groq_timeout_seconds: float = 30.0

    copilot_provider_order: str = "gemini,groq,deterministic"

    # --- Email ingestion ---
    max_email_bytes: int = 10 * 1024 * 1024
    max_deal_candidates: int = 200

    # --- Gmail integration (Google OAuth 2.0 web client) ---
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # Must exactly match an "Authorized redirect URI" of the OAuth client. Defaults to the frontend's
    # proxy route so the OAuth state cookie stays on the browser-facing origin.
    google_redirect_uri: str | None = None
    # Fernet key that encrypts stored refresh tokens and the OAuth state cookie.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: SecretStr | None = None
    # Where the OAuth callback sends the browser afterwards. Defaults to the first CORS origin.
    frontend_url: str | None = None
    gmail_sync_default_days: int = 30
    gmail_sync_max_messages: int = 100
    gmail_fetch_concurrency: int = 8
    gmail_timeout_seconds: float = 20.0
    # Keyword relevance score (0-100) at or above which a synced message is stored for processing.
    gmail_relevance_threshold: int = 40

    @property
    def frontend_base_url(self) -> str:
        return (self.frontend_url or self.cors_origins[0]).rstrip("/")

    @property
    def gmail_redirect_uri(self) -> str:
        return self.google_redirect_uri or f"{self.frontend_base_url}/api/gmail/oauth/callback"

    @property
    def gmail_configured(self) -> bool:
        return bool(
            self.google_client_id
            and self.google_client_secret
            and self.google_client_secret.get_secret_value()
            and self.token_encryption_key
            and self.token_encryption_key.get_secret_value()
        )

    def secret_values(self) -> list[str]:
        """Configured secrets, for redaction in logs and error messages."""
        secrets = (
            self.anthropic_api_key,
            self.voyage_api_key,
            self.cohere_api_key,

            self.postgres_password,
            self.google_client_secret,
            self.token_encryption_key,

            self.gemini_api_key,
            self.groq_api_key,
            

        )
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
