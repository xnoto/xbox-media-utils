"""Structured logging utilities for JSONL output."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def write_log_entry(
    entry: dict[str, Any],
    log_dir: str | Path,
    prefix: str = "log",
    timestamp: datetime | None = None,
) -> Path:
    """Append log entry to daily JSONL file.

    Args:
        entry: Dictionary to serialize and append.
        log_dir: Directory for log files.
        prefix: Filename prefix (default: "log").
        timestamp: Optional timestamp for filename (default: now).

    Returns:
        Path to the log file written.

    Example:
        >>> write_log_entry(
        ...     {"status": "success", "file": "movie.mkv"},
        ...     "/var/log/myapp",
        ...     prefix="process"
        ... )
        PosixPath('/var/log/myapp/process-2024-01-15.jsonl')
    """
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    date_str = (timestamp or datetime.now()).strftime("%Y-%m-%d")
    log_file = log_path / f"{prefix}-{date_str}.jsonl"

    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")

    return log_file


def get_log_file_path(
    log_dir: str | Path,
    prefix: str = "log",
    date: datetime | None = None,
) -> Path:
    """Get the path to a log file without writing to it.

    Args:
        log_dir: Directory for log files.
        prefix: Filename prefix.
        date: Optional date for filename (default: today).

    Returns:
        Path to the log file.
    """
    date_str = (date or datetime.now()).strftime("%Y-%m-%d")
    return Path(log_dir) / f"{prefix}-{date_str}.jsonl"


def read_log_entries(log_file: str | Path) -> list[dict[str, Any]]:
    """Read all entries from a JSONL log file.

    Args:
        log_file: Path to the JSONL file.

    Returns:
        List of deserialized entries.

    Raises:
        FileNotFoundError: If log file doesn't exist.
    """
    entries = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries
