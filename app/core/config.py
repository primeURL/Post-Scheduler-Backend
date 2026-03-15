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

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

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
