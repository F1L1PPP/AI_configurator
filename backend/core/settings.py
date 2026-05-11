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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
