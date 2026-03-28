from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- App ---
    app_name: str = "Post Scheduler API"
    app_env: str = "development"
    frontend_url: str = "http://localhost:4321"
    cors_origins_str: str = "http://localhost:4321"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/post_scheduler"
    sql_echo: bool = False

    # --- JWT (HS256) ---
    jwt_secret_key: str = "dev-secret-CHANGE-in-production"
    jwt_access_expire_minutes: int = 15
    jwt_refresh_expire_days: int = 7

    # --- Google OAuth ---
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"

    # --- Encryption (Fernet) ---
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    fernet_key: str = ""

    # --- X (Twitter) OAuth 2.0 PKCE ---
    # https://developer.twitter.com/en/portal/dashboard
    x_client_id: str = ""
    x_client_secret: str = ""
    x_redirect_uri: str = "http://localhost:8000/accounts/x/callback"

    # --- X (Twitter) API v1.1 / App-level ---
    # API Key + Secret for OAuth 1.0a signing (media upload)
    x_api_key: str = ""
    x_api_secret: str = ""
    # App-only Bearer Token — fetch public tweet metrics without a user token
    x_bearer_token: str = ""

    # --- Firebase Admin SDK ---
    # Path to service-account JSON, relative to the working directory (backend/)
    firebase_sa_path: str = "post-scheduler-sa-dev.json"

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Durable jobs (ARQ + reconciler) ---
    # Keep legacy APScheduler execution enabled until cutover is complete.
    enable_legacy_publisher: bool = True
    enable_legacy_analytics: bool = True
    enable_reconciler: bool = False
    enable_arq_enqueue: bool = False
    arq_redis_url: str = "redis://localhost:6379/1"
    arq_job_timeout_seconds: int = 120
    arq_max_tries: int = 5
    reconciler_publish_interval_seconds: int = 30
    reconciler_analytics_interval_seconds: int = 21600
    reconciler_lock_ttl_seconds: int = 90
    reconciler_publish_lock_key: str = "reconciler:publish:lock"
    reconciler_analytics_lock_key: str = "reconciler:analytics:lock"

    # --- Cloudflare R2 ---
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_region: str = "auto"
    r2_bucket_name: str = ""
    r2_public_base_url: str = ""
    r2_upload_url_expiry_seconds: int = 900

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("database_url")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        """Ensure the asyncpg driver prefix is used for PostgreSQL URLs."""
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and "+asyncpg" not in v:
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins_str.split(",")]


settings = Settings()
