"""Centralized configuration with environment variable fallbacks."""

from __future__ import annotations

import os
from pathlib import Path

# Plex settings
PLEX_USER = os.environ.get("XBOX_PLEX_USER", "plex")
PLEX_GROUP = os.environ.get("XBOX_PLEX_GROUP", "libstoragemgmt")

# Paths
DEFAULT_PLEX_ROOT = os.environ.get("XBOX_PLEX_ROOT", "~/plex")
DEFAULT_LIBRARY = os.environ.get("XBOX_DEFAULT_LIBRARY", "movies")

# Logging
LOG_DIR = os.environ.get("XBOX_RECODE_LOG_DIR", "/var/log/xbox-recode")
IMPORT_LOG_DIR = os.environ.get("XBOX_IMPORT_LOG_DIR", "/var/log/xbox-import")

# Locking
LOCK_FILE = os.environ.get("XBOX_RECODE_LOCK_FILE", "/var/run/xbox-recode.lock")

# Plex API
DEFAULT_PLEX_URL = os.environ.get("XBOX_PLEX_URL", "http://localhost:32400")
DEFAULT_PREFS_PATH = os.environ.get(
    "XBOX_PLEX_PREFS_PATH",
    "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml",
)

# Environment variable names for CLI override
ENV_PLEX_ROOT = "XBOX_IMPORT_PLEX_ROOT"
ENV_LIBRARY = "XBOX_IMPORT_LIBRARY"


def get_config_value(cli_value: str | None, env_name: str, default: str) -> str:
    """Get configuration value with priority: CLI > env var > default.

    Args:
        cli_value: Value from CLI argument (highest priority).
        env_name: Environment variable name.
        default: Default value if neither CLI nor env var set.

    Returns:
        Resolved configuration value.

    Example:
        >>> get_config_value(None, "XBOX_PLEX_USER", "plex")
        'plex'
        >>> get_config_value("custom", "XBOX_PLEX_USER", "plex")
        'custom'
    """
    if cli_value is not None:
        return cli_value
    return os.environ.get(env_name, default)


def get_plex_root(cli_value: str | None = None) -> Path:
    """Get Plex root path, expanding user if needed.

    Args:
        cli_value: Optional CLI override.

    Returns:
        Resolved Plex root path.
    """
    value = get_config_value(cli_value, ENV_PLEX_ROOT, DEFAULT_PLEX_ROOT)
    return Path(value).expanduser()
