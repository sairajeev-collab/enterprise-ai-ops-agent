"""Application configuration.

All configuration is sourced from the environment (or a local .env) via
pydantic-settings. Secrets never live in code; see .env.example for the full
catalog. Settings are validated once at startup and injected everywhere else, so
a misconfiguration fails fast and loudly instead of at first use.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The placeholder secret shipped in .env.example. Refused in production.
DEFAULT_JWT_SECRET = "change-me-in-production-use-a-48-byte-random-secret"


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
    jwt_secret: str = DEFAULT_JWT_SECRET
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
    max_attempts: int = 3

    # --- Reliable job queue (see app/jobs/queue.py) ---
    job_queue_key: str = "ops:jobs"
    # A job claimed but not acknowledged within this window is presumed lost
    # (worker crashed) and redelivered by the reaper.
    job_visibility_timeout_seconds: int = 300
    job_reaper_interval_seconds: int = 30
    # After this many crash-redeliveries a job is parked on the dead-letter queue.
    job_max_redeliveries: int = 5
    # A job still in-flight past this age is "stuck". An operational alert, separate
    # from crash recovery. The reaper Slacks #ops-alerts when it sees one.
    stuck_job_threshold_seconds: int = 1800

    # --- Ingress limits ---
    max_request_bytes: int = 1_048_576  # 1 MiB

    # --- Egress / SSRF guard for caller-supplied callback_url (ADR-0021) ---
    # Empty allowlist = allow any *public* host; block_private then rejects hosts
    # resolving to private/loopback/link-local (incl. cloud metadata) addresses.
    # Set an allowlist to restrict callbacks to known hosts.
    callback_allowed_hosts: list[str] = Field(default_factory=list)
    callback_block_private: bool = True

    # --- LLM ---
    llm_mode: IntegrationMode = IntegrationMode.SANDBOX
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_chat_model: str = "llama3.1"
    llm_embed_model: str = "nomic-embed-text"
    llm_timeout_seconds: float = 60.0
    llm_provider: str = "openai_compatible"  # label for cost logs / failover

    # --- Cost guardrails (ADR-0016) ---
    # Warn in logs past the soft limit; trip the pipeline to the free sandbox model
    # past the hard cap so a runaway can't produce a surprise bill. Defaults are
    # deliberately low. Override per deployment.
    daily_budget_warn_usd: float = 50.0
    daily_budget_cap_usd: float = 100.0

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
    # OpenTelemetry tracing is opt-in and dependency-free by default (ADR-0019):
    # left unset, tracing is a no-op and the OTel SDK isn't even imported. Set the
    # OTLP endpoint (and install the `otel` extra) to export spans to a collector.
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "ops-agent"

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

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> Settings:
        """Refuse to boot in production with insecure or incomplete config.

        Failing fast at startup is far safer than discovering a placeholder
        secret or a half-configured integration at first request.
        """

        if not self.is_production:
            return self

        problems: list[str] = []
        if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET must be set to a strong (>=32 char) value")

        required_when_real: list[tuple[IntegrationMode, str, list[tuple[str, str]]]] = [
            (self.slack_mode, "SLACK", [("SLACK_WEBHOOK_URL", self.slack_webhook_url)]),
            (
                self.jira_mode,
                "JIRA",
                [("JIRA_EMAIL", self.jira_email), ("JIRA_API_TOKEN", self.jira_api_token)],
            ),
            (self.email_mode, "EMAIL", [("SMTP_HOST", self.smtp_host)]),
        ]
        for mode, name, fields in required_when_real:
            if mode is IntegrationMode.REAL:
                missing = [key for key, value in fields if not value]
                if missing:
                    problems.append(f"{name}_MODE=real requires {', '.join(missing)}")

        if problems:
            raise ValueError("Insecure production configuration: " + "; ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so the environment is read once. Tests clear the cache via
    ``get_settings.cache_clear()`` when they need to override values.
    """

    return Settings()
