"""Structured logging utilities for JSONL output."""

from __future__ import annotations

import json
import os
from collections import Counter
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
        f.flush()
        os.fsync(f.fileno())

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


def summarize_recode_progress(log_dir: str | Path) -> dict[str, Any]:
    """Summarize durable recode lifecycle entries for operators and agents."""
    started: dict[str, dict[str, Any]] = {}
    finished: dict[str, dict[str, Any]] = {}
    legacy_finished = []
    corrupt_lines = 0
    recovery_events = 0

    for log_file in sorted(Path(log_dir).glob("recode-*.jsonl")):
        with open(log_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    corrupt_lines += 1
                    continue

                event = entry.get("event")
                operation_id = entry.get("operation_id")
                if event == "started" and operation_id:
                    started[operation_id] = entry
                elif event == "finished" and operation_id:
                    finished[operation_id] = entry
                elif event == "recovery":
                    recovery_events += 1
                elif entry.get("status"):
                    legacy_finished.append(entry)

    finished_entries = [*legacy_finished, *finished.values()]
    statuses = Counter(entry.get("status", "unknown") for entry in finished_entries)
    unfinished = [started[key] for key in started.keys() - finished.keys()]
    failures = [
        entry for entry in finished_entries if entry.get("status") in {"failed", "incompatible"}
    ]

    return {
        "finished": len(finished_entries),
        "succeeded": statuses["success"],
        "failed": statuses["failed"],
        "incompatible": statuses["incompatible"],
        "would_process": statuses["would_process"],
        "unfinished": len(unfinished),
        "space_saved_bytes": sum(entry.get("space_saved_bytes") or 0 for entry in finished_entries),
        "recovery_events": recovery_events,
        "corrupt_log_lines": corrupt_lines,
        "unfinished_operations": [
            {
                "operation_id": entry.get("operation_id"),
                "path": entry.get("path"),
                "started_at": entry.get("timestamp"),
            }
            for entry in sorted(unfinished, key=lambda item: item.get("timestamp", ""))
        ],
        "recent_failures": [
            {
                "path": entry.get("path"),
                "status": entry.get("status"),
                "error": entry.get("error"),
                "finished_at": entry.get("finished_at") or entry.get("timestamp"),
            }
            for entry in failures[-10:]
        ],
    }
