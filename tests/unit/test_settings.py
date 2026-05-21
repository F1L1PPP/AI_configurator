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
    # clear=True wipes all env vars first so the test isn't polluted by the
    # developer's local shell. _env_file=None skips .env so we don't pick up
    # whatever happens to be in the project root.
    with patch.dict(os.environ, env, clear=True):
        s = Settings(_env_file=None)

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


def test_get_settings_is_cached(monkeypatch):
    # Force get_settings() through a hermetic environment: clear shell vars,
    # skip .env, and stub Settings to a known instance so we're only testing
    # the @lru_cache behavior of get_settings itself.
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "key-a"}, clear=True):
        monkeypatch.setattr(
            "backend.core.settings.Settings",
            lambda **kw: Settings(_env_file=None, **kw),
        )
        s1 = get_settings()
        s2 = get_settings()
    assert s1 is s2


# ---------------------------------------------------------------------------
# validate_required_credentials — boot guard (review fix #3)
# ---------------------------------------------------------------------------

_ALL_REQUIRED_ENV = {
    "ANTHROPIC_API_KEY": "sk-test",
    "ROUTER_HOST": "10.0.0.1",
    "ROUTER_SSH_USER": "admin",
    "ROUTER_SSH_PASSWORD": "secret",
    "ROUTER_WEBUI_USER": "webadmin",
    "ROUTER_WEBUI_PASSWORD": "webpass",
    "ROUTER_WEBUI_BASE_URL": "https://10.0.0.1",
}


def _make_settings(**env_overrides) -> Settings:
    """Build a Settings instance with all required creds set, applying env overrides.

    Uses patch.dict with clear=True for full isolation — Settings resolves
    fields via env-var aliases, so we supply the alias names (UPPER_CASE).
    _env_file=None skips .env on disk.
    """
    env = {**_ALL_REQUIRED_ENV, **env_overrides}
    with patch.dict(os.environ, env, clear=True):
        return Settings(_env_file=None)


def test_validate_required_credentials_passes_when_all_set():
    s = _make_settings()
    # Should not raise
    s.validate_required_credentials()


def test_validate_required_credentials_raises_on_empty_anthropic_key():
    s = _make_settings(anthropic_api_key="")
    with pytest.raises(ValueError) as exc_info:
        s.validate_required_credentials()
    assert "ANTHROPIC_API_KEY" in str(exc_info.value)


def test_validate_required_credentials_lists_all_missing():
    # All required env vars absent → all 7 env-var names appear in the error message
    with patch.dict(os.environ, {}, clear=True):
        s = Settings(_env_file=None)
    with pytest.raises(ValueError) as exc_info:
        s.validate_required_credentials()
    msg = str(exc_info.value)
    for env_name in [
        "ANTHROPIC_API_KEY",
        "ROUTER_HOST",
        "ROUTER_SSH_USER",
        "ROUTER_SSH_PASSWORD",
        "ROUTER_WEBUI_USER",
        "ROUTER_WEBUI_PASSWORD",
        "ROUTER_WEBUI_BASE_URL",
    ]:
        assert env_name in msg, f"Expected {env_name!r} in error message"


def test_validate_required_credentials_ignores_non_required_fields():
    # LOG_LEVEL is not in the required list; empty value must not cause a raise.
    s = _make_settings(LOG_LEVEL="")
    # Should not raise
    s.validate_required_credentials()
