"""Tests for Plex transcoder activity gating."""

from xbox_media_utils.core.plex_activity import (
    count_active_plex_transcodes,
    wait_for_plex_transcodes,
)


def test_count_active_plex_transcodes_matches_executable_only(tmp_path):
    for pid, command in (
        ("100", b"/usr/lib/plexmediaserver/Plex Transcoder\0--session\0"),
        ("101", b"python3\0script mentioning Plex Transcoder\0"),
        ("self", b"/usr/lib/plexmediaserver/Plex Transcoder\0"),
    ):
        process = tmp_path / pid
        process.mkdir()
        (process / "cmdline").write_bytes(command)

    assert count_active_plex_transcodes(tmp_path) == 1


def test_wait_for_plex_transcodes_retries_until_idle():
    readings = iter([2, 1, 0])
    messages = []
    sleeps = []

    wait_for_plex_transcodes(
        poll_seconds=15,
        logger=messages.append,
        counter=lambda: next(readings),
        sleeper=sleeps.append,
    )

    assert len(messages) == 2
    assert sleeps == [15, 15]
