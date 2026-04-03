"""Core utilities shared across xbox-media-utils."""

from xbox_media_utils.core.config import (
    DEFAULT_LIBRARY,
    DEFAULT_PLEX_ROOT,
    DEFAULT_PLEX_URL,
    DEFAULT_PREFS_PATH,
    ENV_LIBRARY,
    ENV_PLEX_ROOT,
    IMPORT_LOG_DIR,
    LOCK_FILE,
    LOG_DIR,
    PLEX_GROUP,
    PLEX_USER,
    get_config_value,
    get_plex_root,
)
from xbox_media_utils.core.locking import LockAcquisitionError, acquire_lock
from xbox_media_utils.core.logging import (
    get_log_file_path,
    read_log_entries,
    write_log_entry,
)

__all__ = [
    # Locking
    "acquire_lock",
    "LockAcquisitionError",
    # Logging
    "write_log_entry",
    "get_log_file_path",
    "read_log_entries",
    # Config
    "get_config_value",
    "get_plex_root",
    "PLEX_USER",
    "PLEX_GROUP",
    "DEFAULT_PLEX_ROOT",
    "DEFAULT_LIBRARY",
    "LOG_DIR",
    "IMPORT_LOG_DIR",
    "LOCK_FILE",
    "DEFAULT_PLEX_URL",
    "DEFAULT_PREFS_PATH",
    "ENV_PLEX_ROOT",
    "ENV_LIBRARY",
]
