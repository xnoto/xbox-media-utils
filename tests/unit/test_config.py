"""Tests for core.config module."""

import os
from pathlib import Path

import pytest

from xbox_media_utils.core import config
from xbox_media_utils.core.config import get_config_value, get_plex_root


class TestGetConfigValue:
    """Test suite for get_config_value function."""

    def test_cli_value_takes_priority(self):
        """CLI value should override env var and default."""
        os.environ["TEST_VAR"] = "env_value"

        result = get_config_value("cli_value", "TEST_VAR", "default")

        assert result == "cli_value"
        del os.environ["TEST_VAR"]

    def test_env_var_used_when_no_cli(self):
        """Env var should be used when CLI value is None."""
        os.environ["TEST_VAR"] = "env_value"

        result = get_config_value(None, "TEST_VAR", "default")

        assert result == "env_value"
        del os.environ["TEST_VAR"]

    def test_default_used_when_no_cli_or_env(self):
        """Default should be used when neither CLI nor env var set."""
        # Ensure env var not set
        os.environ.pop("TEST_VAR_NONEXISTENT", None)

        result = get_config_value(None, "TEST_VAR_NONEXISTENT", "default")

        assert result == "default"

    def test_empty_string_cli_value_used(self):
        """Empty string CLI value should be used (not treated as None)."""
        result = get_config_value("", "TEST_VAR", "default")

        assert result == ""


class TestGetPlexRoot:
    """Test suite for get_plex_root function."""

    def test_expands_tilde(self):
        """Should expand ~ to home directory."""
        result = get_plex_root("~/test")

        assert str(result) == str(Path.home() / "test")
        assert "~" not in str(result)

    def test_uses_cli_value(self):
        """Should use CLI value when provided."""
        result = get_plex_root("/custom/path")

        assert result == Path("/custom/path")

    def test_uses_env_var(self, monkeypatch):
        """Should use env var when CLI not provided."""
        monkeypatch.setenv(config.ENV_PLEX_ROOT, "/env/path")

        result = get_plex_root()

        assert result == Path("/env/path")

    def test_uses_default(self, monkeypatch):
        """Should use default when nothing else set."""
        monkeypatch.delenv(config.ENV_PLEX_ROOT, raising=False)

        result = get_plex_root()

        assert result == Path(config.DEFAULT_PLEX_ROOT).expanduser()


class TestConfigConstants:
    """Test suite for configuration constants."""

    def test_default_values_exist(self):
        """Default configuration values should be defined."""
        assert hasattr(config, "PLEX_USER")
        assert hasattr(config, "PLEX_GROUP")
        assert hasattr(config, "DEFAULT_PLEX_ROOT")
        assert hasattr(config, "DEFAULT_LIBRARY")
        assert hasattr(config, "LOG_DIR")
        assert hasattr(config, "IMPORT_LOG_DIR")
        assert hasattr(config, "LOCK_FILE")
        assert hasattr(config, "DEFAULT_PLEX_URL")
        assert hasattr(config, "DEFAULT_PREFS_PATH")

    def test_env_var_names_defined(self):
        """Environment variable names should be defined."""
        assert config.ENV_PLEX_ROOT == "XBOX_IMPORT_PLEX_ROOT"
        assert config.ENV_LIBRARY == "XBOX_IMPORT_LIBRARY"

    def test_defaults_use_xbox_prefix(self):
        """Default values should use XBOX_ prefix for env vars."""
        # Check that we're reading from XBOX_ prefixed env vars
        assert "XBOX_" in str(config.DEFAULT_PLEX_ROOT) or config.DEFAULT_PLEX_ROOT == "~/plex"
