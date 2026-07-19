"""Application configuration.

All configuration is sourced from the environment (or a local .env) via
pydantic-settings. Secrets never live in code; see .env.example for the full
catalog. Settings are validated once at startup and injected everywhere else, so
a misconfiguration fails fast and loudly instead of at first use.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    LOCAL = "local"
    CI = "ci"
    PRODUCTION = "production"


class IntegrationMode(StrEnum):
    REAL = "real"
    SANDBOX = "sandbox"


class Settings(BaseSettings):
    """Typed, validated view of the process environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Core ---
    app_env: AppEnv = AppEnv.LOCAL
    log_level: str = "INFO"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # --- Security / JWT ---
    jwt_secret: str = "change-me-in-production-use-a-48-byte-random-secret"
    jwt_issuer: str = "enterprise-ai-ops-agent"
    jwt_ttl_seconds: int = 3600
    service_account_id: str = "ops-service"
    service_account_password: str = "local-dev-password"

    # --- Rate limiting ---
    rate_limit_requests: int = 60
    rate_limit_window_seconds: int = 60

    # --- Datastores ---
    postgres_dsn: str = "postgresql+asyncpg://ops:ops@localhost:5432/ops"
    redis_url: str = "redis://localhost:6379/0"
    job_queue_key: str = "ops:jobs"
    max_attempts: int = 3

    # --- LLM ---
    llm_mode: IntegrationMode = IntegrationMode.SANDBOX
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_chat_model: str = "llama3.1"
    llm_embed_model: str = "nomic-embed-text"
    llm_timeout_seconds: float = 60.0

    # --- Knowledge / Qdrant ---
    knowledge_mode: IntegrationMode = IntegrationMode.SANDBOX
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "company_knowledge"
    qdrant_vector_size: int = 768

    # --- Slack ---
    slack_mode: IntegrationMode = IntegrationMode.SANDBOX
    slack_webhook_url: str = ""
    slack_default_channel: str = "#ops-alerts"

    # --- Jira ---
    jira_mode: IntegrationMode = IntegrationMode.SANDBOX
    jira_base_url: str = "https://your-org.atlassian.net"
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = "OPS"

    # --- Email ---
    email_mode: IntegrationMode = IntegrationMode.SANDBOX
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    email_from: str = "ops@example.com"

    # --- Observability ---
    sentry_dsn: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # Allow a comma-separated string in the environment, list in code/tests.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so the environment is read once. Tests clear the cache via
    ``get_settings.cache_clear()`` when they need to override values.
    """

    return Settings()
