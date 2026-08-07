"""Detect active Plex video transcodes before background recoding."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable


class PlexStatusError(RuntimeError):
    """Raised when Plex process activity cannot be inspected safely."""


def count_active_plex_transcodes(proc_root: Path = Path("/proc")) -> int:
    """Count processes whose executable is Plex Transcoder."""
    count = 0
    try:
        processes = proc_root.iterdir()
    except OSError as e:
        raise PlexStatusError(f"Could not inspect {proc_root}: {e}") from e

    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            command = (process / "cmdline").read_bytes().split(b"\0", 1)[0]
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if Path(command.decode(errors="replace")).name == "Plex Transcoder":
            count += 1
    return count


def wait_for_plex_transcodes(
    poll_seconds: int,
    logger: Callable[[str], None] = print,
    counter: Callable[[], int] = count_active_plex_transcodes,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Wait until Plex has no active transcoder processes."""
    while True:
        active = counter()
        if active == 0:
            return
        logger(f"Plex has {active} active transcode(s); retrying in {poll_seconds}s")
        sleeper(poll_seconds)
