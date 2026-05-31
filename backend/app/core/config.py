import secrets
import warnings
from typing import Annotated, Any, Literal

from pydantic import AnyUrl, BeforeValidator, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


def _parse_cors(v: Any) -> list[str] | str:
    """Accept CORS origins as a JSON list or a comma-separated string."""
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",") if i.strip()]
    if isinstance(v, list | str):
        return v
    raise ValueError(f"Invalid CORS origins value: {v!r}")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # In Docker the env vars are injected directly; .env is for local dev.
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    # ── API ───────────────────────────────────────────────────────────────────
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    # ── JWT Auth ──────────────────────────────────────────────────────────────
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days default

    # ── CORS ──────────────────────────────────────────────────────────────────
    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(_parse_cors)
    ] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(o).rstrip("/") for o in self.BACKEND_CORS_ORIGINS]

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    POSTGRES_SERVER: str
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── Redis (Celery broker) ─────────────────────────────────────────────────
    REDIS_URL: str = "redis://redis:6379/0"

    # ── First Superuser ────────────────────────────────────────────────────────
    FIRST_SUPERUSER: str
    FIRST_SUPERUSER_PASSWORD: str

    # ── MinIO (S3-compatible storage) ─────────────────────────────────────────
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str
    MINIO_BUCKET: str = "legal-document-citation-rag-documents"
    MINIO_USE_SSL: bool = False

    # ── AI Providers (Google AI Studio + OpenRouter) ────────────────────────
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # ── Embedding Model ────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = "openai/text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536  # MUST match Vector(1536) in DocumentChunk

    # ── LLM Models ────────────────────────────────────────────────────────────
    FAST_LLM_MODEL: str = "google/gemini-flash-1.5"              # ingestion tasks
    REASONING_LLM_MODEL: str = "google/gemini-pro-1.5"           # future reasoning
    QUERY_LLM_MODEL: str = "google/gemini-2.5-flash"             # Phase 4 RAG queries (free tier)

    # ── Validation: warn on default secrets, raise in production ──────────────
    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            msg = f'The value of {var_name} is "changethis". Please change it.'
            if self.ENVIRONMENT == "local":
                warnings.warn(msg, stacklevel=1)
            else:
                raise ValueError(msg)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("POSTGRES_PASSWORD", self.POSTGRES_PASSWORD)
        self._check_default_secret("MINIO_SECRET_KEY", self.MINIO_SECRET_KEY)
        self._check_default_secret(
            "FIRST_SUPERUSER_PASSWORD", self.FIRST_SUPERUSER_PASSWORD
        )
        return self


settings = Settings()  # type: ignore[call-arg]
