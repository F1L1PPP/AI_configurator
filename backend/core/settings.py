from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Anthropic
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")

    # Cisco C1111 — SSH
    router_host: str = Field(default="", alias="ROUTER_HOST")
    router_ssh_user: str = Field(default="", alias="ROUTER_SSH_USER")
    router_ssh_password: str = Field(default="", alias="ROUTER_SSH_PASSWORD")

    # Cisco C1111 — WebUI
    router_webui_user: str = Field(default="", alias="ROUTER_WEBUI_USER")
    router_webui_password: str = Field(default="", alias="ROUTER_WEBUI_PASSWORD")
    router_webui_base_url: str = Field(default="", alias="ROUTER_WEBUI_BASE_URL")

    # App
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    artifacts_dir: Path = Field(default=Path("artifacts"), alias="ARTIFACTS_DIR")
    selector_cache_path: Path = Field(
        default=Path("artifacts/selector_cache.json"),
        alias="SELECTOR_CACHE_PATH",
    )
    plan_validation_cache_path: Path = Field(
        default=Path("artifacts/plan_validation_cache.json"),
        alias="PLAN_VALIDATION_CACHE_PATH",
    )
    plan_vision_enabled: bool = Field(default=True, alias="PLAN_VISION_ENABLED")
    logs_dir: Path = Field(default=Path("logs"), alias="LOGS_DIR")

    # CORS + WebSocket origin allowlist. WebSockets bypass the browser CORS
    # policy, so /ws/agent has to enforce the same origin check itself —
    # share the same list to avoid drift.
    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://localhost:8000",
            # 127.0.0.1 is the same machine as `localhost` to the OS but a
            # DIFFERENT origin to the browser. Filip's bookmarks use
            # 127.0.0.1:8000, so without this entry the WS handshake at
            # /ws/agent gets a 1008 policy-violation close and the live
            # event stream silently shows "Waiting for agent activity."
            "http://127.0.0.1:8000",
        ],
        alias="ALLOWED_ORIGINS",
    )

    # Day 6 — RAG (knowledge_agent)
    # Chunk sizes are tuned to fit the MiniLM-L6 model's 256-token input window.
    knowledge_base_dir: Path = Field(default=Path("knowledge_base"), alias="KNOWLEDGE_BASE_DIR")
    chroma_persist_dir: Path = Field(
        default=Path("knowledge_base/vectorstore"), alias="CHROMA_PERSIST_DIR"
    )
    chroma_collection: str = Field(default="cisco_docs", alias="CHROMA_COLLECTION")
    embedding_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2", alias="EMBEDDING_MODEL"
    )
    rag_top_k: int = Field(default=5, alias="RAG_TOP_K")
    rag_chunk_tokens: int = Field(default=250, alias="RAG_CHUNK_TOKENS")
    rag_chunk_overlap: int = Field(default=30, alias="RAG_CHUNK_OVERLAP")

    # WebUI Atlas
    atlas_dir: Path = Field(default=Path("webui_atlas"), alias="ATLAS_DIR")
    atlas_self_verify: bool = Field(default=True, alias="ATLAS_SELF_VERIFY")
    webui_vision_enabled: bool = Field(default=False, alias="WEBUI_VISION_ENABLED")

    # WebUI Playwright timeout — promote from hardcoded 20 s so slow routers
    # or flaky networks can be tuned via env without a code change.
    webui_goto_timeout_ms: int = Field(default=20_000, alias="WEBUI_GOTO_TIMEOUT_MS")

    # WebSocket strict-origin mode (review fix #14).
    # False (default): missing-origin connections are allowed (covers TestClient,
    # curl, and other non-browser clients used during local development).
    # True: missing OR foreign origin → rejected with 1008 Policy Violation.
    ws_strict_origin: bool = Field(default=False, alias="WS_STRICT_ORIGIN")

    def validate_required_credentials(self) -> None:
        """Raise ValueError listing every required credential that is missing.

        Called from main.py lifespan at startup. Failing fast at boot with
        a clear list beats failing at first router/Anthropic call with a
        cryptic auth error.
        """
        required = [
            ("anthropic_api_key", "ANTHROPIC_API_KEY"),
            ("router_host", "ROUTER_HOST"),
            ("router_ssh_user", "ROUTER_SSH_USER"),
            ("router_ssh_password", "ROUTER_SSH_PASSWORD"),
            ("router_webui_user", "ROUTER_WEBUI_USER"),
            ("router_webui_password", "ROUTER_WEBUI_PASSWORD"),
            ("router_webui_base_url", "ROUTER_WEBUI_BASE_URL"),
        ]
        missing = [env_name for attr, env_name in required if not getattr(self, attr)]
        if missing:
            raise ValueError(
                "Required credentials missing from .env (or shadowed by an empty "
                f"shell export): {', '.join(missing)}. Check .env.example for the "
                "required keys."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
