"""Application configuration using Pydantic Settings.

Adapted from CLU backend/app/config.py with:
- LTI 1.3 settings (tool private key, session secret)
- Canvas OAuth2 settings (Phase 2 stub)
- PostgreSQL + pgvector database
- Removed IMSCC-specific settings (upload_dir, db_path, export tokens)
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LTI 1.3 ---
    lti_tool_private_key_pem: str = ""
    lti_tool_private_key_pem_file: str = ""
    session_secret_key: str = "dev-secret-change-me-in-production"
    session_ttl_hours: int = 8

    @property
    def lti_private_key(self) -> str:
        """Return PEM key from env var or file."""
        if self.lti_tool_private_key_pem:
            return self.lti_tool_private_key_pem
        if self.lti_tool_private_key_pem_file:
            return Path(self.lti_tool_private_key_pem_file).read_text()
        return ""

    # --- Canvas OAuth2 (Phase 2: stubbed) ---
    canvas_oauth2_client_id: str = ""
    canvas_oauth2_client_secret: str = ""

    # --- Database (Postgres + pgvector) ---
    database_url: str = ""

    # --- Auth ---
    auth_bypass_for_local: bool = False

    # --- Local Canvas dev ---
    local_canvas_base_url: str = "http://canvas.docker"
    local_canvas_client_id: str = "10000000000002"
    local_canvas_deployment_id: str = ""
    local_canvas_api_token: str = ""

    # --- AI: Ollama Cloud (sole provider) ---
    ollama_base_url: str = "https://ollama.com/v1"
    ollama_api_key: str = ""
    ollama_model: str = "kimi-k2.6:cloud"

    # --- AI: Escalation (Tier 2 — stronger model for complex cases) ---
    escalation_backend: str = "ollama"
    escalation_model: str = "qwen3-vl:235b-cloud"

    # --- AI: Concurrency ---
    ai_backend: str = "ollama"
    alt_text_provider: str = "ollama"
    alt_text_auto_fallback: bool = True
    alt_text_min_confidence: float = 0.72
    ai_max_concurrency: int = 2  # Ollama Cloud rate-limits vision requests
    ai_per_run_cap: int = 4
    # Per-job total budget for alt-text generation. After this many seconds,
    # remaining images are skipped with a "budget_exhausted" reason and the
    # job continues with whatever was generated so far. A single rate-limited
    # image can otherwise stall the loop for 10+ minutes (CLU-58).
    alt_text_global_budget_seconds: int = 300  # 5 minutes

    # --- Server ---
    environment: str = "development"
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    app_base_url: str = ""
    allowed_hosts: str = "*"

    # --- CORS ---
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- Security ---
    security_headers_enabled: bool = True
    dochub_requires_auth: bool = True
    dochub_max_upload_bytes: int = 50_000_000
    canvas_webhook_secret: str = ""

    # --- Observability ---
    log_format: str = "json"
    log_file_path: str = ""
    log_file_max_bytes: int = 50_000_000
    log_file_backup_count: int = 3

    # --- Caption overlay ---
    clu_public_url: str = "http://localhost:8001"  # Override in production
    caption_max_video_duration: int = 7200  # 2 hours

    @property
    def cors_origins_list(self) -> list[str]:
        return self._split_csv(self.cors_origins)

    @property
    def allowed_hosts_list(self) -> list[str]:
        return self._split_csv(self.allowed_hosts) or ["*"]

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() == "production"

    def validate_for_startup(self) -> None:
        """Fail fast for configuration that must never reach production."""
        errors: list[str] = []

        if self.auth_bypass_for_local and self.is_production:
            errors.append("AUTH_BYPASS_FOR_LOCAL must be false when ENVIRONMENT=production")

        if not self.auth_bypass_for_local:
            if self.session_secret_key == "dev-secret-change-me-in-production":
                errors.append(
                    "SESSION_SECRET_KEY must be set. Generate one with: "
                    "python -c \"import secrets; print(secrets.token_urlsafe(32))\""
                )
            if not self.database_url:
                errors.append("DATABASE_URL must be set")

        if self.is_production:
            if not self.app_base_url.startswith("https://"):
                errors.append("APP_BASE_URL must be set to the public https:// URL")
            if any(origin == "*" for origin in self.cors_origins_list):
                errors.append("CORS_ORIGINS must not contain '*' in production")
            if not self.ollama_model.strip():
                errors.append("OLLAMA_MODEL must be set")
            if self.ollama_base_url.rstrip("/").lower() == "https://ollama.com/v1":
                if not self.ollama_api_key:
                    errors.append("OLLAMA_API_KEY must be set when using Ollama Cloud")

        if errors:
            raise RuntimeError("Invalid startup configuration: " + "; ".join(errors))

    @staticmethod
    def _split_csv(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
