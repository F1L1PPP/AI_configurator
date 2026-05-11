import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.core.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_types_from_env():
    env = {
        "ANTHROPIC_API_KEY": "sk-test-key",
        "ROUTER_HOST": "10.0.0.1",
        "ROUTER_SSH_USER": "admin",
        "ROUTER_SSH_PASSWORD": "secret",
        "ROUTER_WEBUI_USER": "webadmin",
        "ROUTER_WEBUI_PASSWORD": "webpass",
        "ROUTER_WEBUI_BASE_URL": "https://10.0.0.1",
        "LOG_LEVEL": "DEBUG",
        "ARTIFACTS_DIR": "artifacts",
        "LOGS_DIR": "logs",
    }
    with patch.dict(os.environ, env):
        s = Settings()

    assert isinstance(s.anthropic_api_key, str)
    assert s.anthropic_api_key == "sk-test-key"
    assert isinstance(s.router_host, str)
    assert s.router_host == "10.0.0.1"
    assert isinstance(s.log_level, str)
    assert s.log_level == "DEBUG"
    assert isinstance(s.artifacts_dir, Path)
    assert isinstance(s.logs_dir, Path)


def test_settings_defaults():
    with patch.dict(os.environ, {}, clear=True):
        s = Settings(_env_file=None)

    assert s.log_level == "INFO"
    assert s.artifacts_dir == Path("artifacts")
    assert s.logs_dir == Path("logs")


def test_get_settings_is_cached():
    env = {"ANTHROPIC_API_KEY": "key-a"}
    with patch.dict(os.environ, env):
        s1 = get_settings()
        s2 = get_settings()
    assert s1 is s2
