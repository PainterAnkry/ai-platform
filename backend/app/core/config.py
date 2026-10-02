"""Application configuration loaded from environment variables.

Single source of truth for runtime config. All modules MUST import from
``app.core.config.settings`` rather than reading ``os.environ`` directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly-typed application settings.

    Values are loaded from environment variables (and an optional ``.env``
    file when running outside Docker). Variables are case-insensitive.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- App ----------
    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    PROJECT_NAME: str = "ai-platform"
    API_V1_PREFIX: str = "/api/v1"

    # ---------- Server ----------
    BACKEND_HOST: str = "0.0.0.0"
    BACKEND_PORT: int = 8000

    # ---------- Database ----------
    # Optional full-DSN override. When empty (the default) the DSN is built
    # from the POSTGRES_* fields below. Setting this to e.g.
    # ``sqlite:///./ai_platform.db`` enables a zero-install local preview —
    # Docker deployments and Phase 3 (pgvector) keep using PostgreSQL.
    DATABASE_URL: str = ""

    POSTGRES_USER: str = "aiplatform"
    POSTGRES_PASSWORD: str = "change_me_postgres"
    POSTGRES_DB: str = "aiplatform"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # ---------- Redis ----------
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # ---------- Celery (Phase 2 async workers) ----------
    # Separate Redis logical DBs so the task queue never collides with the cache.
    CELERY_BROKER_URL: str = ""  # defaults to redis db 1
    CELERY_RESULT_BACKEND: str = ""  # defaults to redis db 2
    CELERY_TASK_ALWAYS_EAGER: bool = False  # True = run tasks inline (no worker)

    # ---------- Uploads (Phase 2 document ingestion) ----------
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_BYTES: int = 25 * 1024 * 1024

    # ---------- Phase 3: Embedding ----------
    # "mock"   = deterministic local hash embedding (offline demo/tests)
    # "openai" = any OpenAI-compatible /v1/embeddings endpoint
    # "local"  = sentence-transformers, imported lazily only when installed
    EMBEDDING_PROVIDER: str = "mock"
    # Fixed vector width. The pgvector column is created with this dimension in
    # migration 0003; changing it requires a new migration, so it is validated
    # against what providers actually return at runtime.
    EMBEDDING_DIM: int = 1024
    EMBEDDING_BATCH_SIZE: int = 32
    # Required when EMBEDDING_PROVIDER=openai (e.g. SiliconFlow / Zhipu / OpenAI).
    EMBEDDING_API_BASE: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = ""

    # ---------- Phase 3: Reranker ----------
    # "none"    = keep the vector-search order
    # "lexical" = built-in char-n-gram overlap reranker (offline, no deps)
    # "api"     = Jina/Cohere-style /rerank endpoint (SiliconFlow, Jina, ...)
    RERANKER_PROVIDER: str = "lexical"
    RERANKER_API_BASE: str = ""
    RERANKER_API_KEY: str = ""
    RERANKER_MODEL: str = ""

    # ---------- Phase 3: LLM (minimal sync provider; Phase 4 adds SSE) ----------
    # "mock"   = extractive answer over the retrieved chunks (offline demo)
    # "openai" = any OpenAI-compatible /v1/chat/completions endpoint
    LLM_PROVIDER: str = "mock"
    LLM_API_BASE: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    LLM_TEMPERATURE: float = 0.2
    LLM_MAX_OUTPUT_TOKENS: int = 1024
    LLM_TIMEOUT_SECONDS: float = 60.0

    # ---------- Phase 3: RAG pipeline ----------
    RAG_VECTOR_TOP_K: int = 20  # candidates pulled from vector search
    RAG_FINAL_TOP_K: int = 5  # kept after reranking
    RAG_MAX_CONTEXT_CHARS: int = 6000  # prompt budget for retrieved chunks

    # ---------- Phase 6: MCP ----------
    # The self-hosted MCP server is mounted on this app at /api/mcp; the
    # client talks to it over loopback HTTP. Point MCP_SERVER_URL elsewhere
    # to consume a third-party server instead.
    MCP_ENABLED: bool = True
    MCP_SERVER_URL: str = ""

    # ---------- Security ----------
    SECRET_KEY: str = "please_change_me_to_a_long_random_string_at_least_32_chars"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    # ---------- CORS ----------
    # ``NoDecode`` stops pydantic-settings from JSON-decoding the raw env
    # value, so the before-validator below can accept a comma-separated
    # string (the form used by .env files and docker-compose).
    CORS_ORIGINS: Annotated[List[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # ---------------------------------------------------------------
    # Derived properties
    # ---------------------------------------------------------------
    @property
    def database_url(self) -> str:
        """SQLAlchemy DSN used by both the app and Alembic.

        ``DATABASE_URL`` wins when set; otherwise the DSN is assembled from
        the ``POSTGRES_*`` fields.
        """
        if self.DATABASE_URL.strip():
            return self.DATABASE_URL.strip()
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def is_sqlite(self) -> bool:
        """True when the effective DSN points at SQLite."""
        return self.database_url.startswith("sqlite")

    @property
    def redis_url(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    def _redis_db_url(self, db: int) -> str:
        """Redis URL for a specific logical DB, reusing host/port/auth."""
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{db}"

    @property
    def celery_broker_url(self) -> str:
        return self.CELERY_BROKER_URL.strip() or self._redis_db_url(1)

    @property
    def celery_result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND.strip() or self._redis_db_url(2)

    @property
    def rag_final_top_k(self) -> int:
        """Chunks kept after reranking — never more than the vector candidates."""
        return max(1, min(self.RAG_FINAL_TOP_K, self.RAG_VECTOR_TOP_K))

    @property
    def mcp_server_url(self) -> str:
        """Loopback URL of the mounted MCP server by default."""
        return (
            self.MCP_SERVER_URL.strip()
            or f"http://127.0.0.1:{self.BACKEND_PORT}/api/mcp"
        )

    @property
    def upload_dir(self) -> str:
        """Absolute path to the uploads root, created on first access."""
        import os

        path = os.path.abspath(self.UPLOAD_DIR)
        os.makedirs(path, exist_ok=True)
        return path

    # ---------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------
    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors(cls, v):
        """Normalise ``CORS_ORIGINS`` into a list of origins.

        Accepts either a JSON array (``["http://a","http://b"]``) or the
        comma-separated form used by ``.env`` files and docker-compose.
        """
        if isinstance(v, str):
            raw = v.strip()
            if not raw:
                return []
            if raw.startswith("["):
                try:
                    import json

                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except ValueError:
                    pass  # fall through to comma splitting
            return [item.strip() for item in raw.split(",") if item.strip()]
        return v

    @field_validator("EMBEDDING_PROVIDER", "RERANKER_PROVIDER", "LLM_PROVIDER")
    @classmethod
    def _lowercase_provider(cls, v: str) -> str:
        """Normalise provider selectors so ``Mock``/``MOCK`` both work."""
        return v.strip().lower()

    @field_validator("EMBEDDING_DIM")
    @classmethod
    def _check_dim(cls, v: int) -> int:
        # pgvector supports up to 2000 dims for indexed columns (60000 without
        # an index); the practical sweet spot for the models this project
        # targets is 256-4096.
        if not 64 <= v <= 4096:
            raise ValueError("EMBEDDING_DIM must be between 64 and 4096")
        return v

    @field_validator("RAG_VECTOR_TOP_K", "RAG_FINAL_TOP_K")
    @classmethod
    def _check_top_k(cls, v: int) -> int:
        if not 1 <= v <= 100:
            raise ValueError("RAG top-k values must be between 1 and 100")
        return v

    @field_validator("SECRET_KEY")
    @classmethod
    def _warn_short_secret(cls, v: str) -> str:
        if len(v) < 32:
            # Soft warning; the default already satisfies this in dev.
            # Real deployments MUST override.
            import logging

            logging.getLogger(__name__).warning(
                "SECRET_KEY is shorter than 32 characters; rotate it for production."
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()


# Convenience alias for direct imports.
settings = get_settings()