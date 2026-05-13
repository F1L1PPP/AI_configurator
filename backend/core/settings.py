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
    logs_dir: Path = Field(default=Path("logs"), alias="LOGS_DIR")

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
