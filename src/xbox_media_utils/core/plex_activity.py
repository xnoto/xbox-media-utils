"""Detect active Plex video playback before background recoding."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable


class PlexStatusError(RuntimeError):
    """Raised when Plex playback activity cannot be inspected safely."""


ACTIVE_PLAYBACK_STATES = {"buffering", "playing"}
VIDEO_MEDIA_TYPES = {"clip", "episode", "movie"}


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


def _first(value: Any) -> dict:
    """Normalize Plex objects that may be dictionaries or one-item lists."""
    if isinstance(value, list):
        value = value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _get_plex_sessions() -> list[dict]:
    """Fetch Plex sessions without exposing authentication details."""
    from xbox_media_utils.api import PlexError, PlexScanner

    try:
        return PlexScanner().get_sessions()
    except PlexError as e:
        raise PlexStatusError(f"Could not inspect Plex sessions: {e}") from e


def count_active_plex_playbacks(
    session_provider: Callable[[], list[dict]] | None = None,
    transcoder_counter: Callable[[], int] | None = None,
) -> int:
    """Count active video playback, retaining process-level transcode detection."""
    sessions = (session_provider or _get_plex_sessions)()
    active_sessions = sum(
        1
        for session in sessions
        if isinstance(session, dict)
        and session.get("type") in VIDEO_MEDIA_TYPES
        and _first(session.get("Player")).get("state") in ACTIVE_PLAYBACK_STATES
    )
    active_transcoders = (transcoder_counter or count_active_plex_transcodes)()
    return max(active_sessions, active_transcoders)


def wait_for_plex_playback_idle(
    poll_seconds: int,
    logger: Callable[[str], None] = print,
    counter: Callable[[], int] = count_active_plex_playbacks,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Wait until Plex has no active video playback or transcoder processes."""
    while True:
        try:
            active = counter()
        except PlexStatusError as e:
            logger(f"Could not inspect Plex activity; retrying in {poll_seconds}s: {e}")
            sleeper(poll_seconds)
            continue
        if active == 0:
            return
        logger(f"Plex has {active} active playback(s); retrying in {poll_seconds}s")
        sleeper(poll_seconds)
