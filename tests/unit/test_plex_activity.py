"""Tests for Plex playback activity gating."""

from xbox_media_utils.core.plex_activity import (
    PlexStatusError,
    count_active_plex_playbacks,
    count_active_plex_transcodes,
    wait_for_plex_playback_idle,
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


def test_count_active_plex_playbacks_includes_all_video_playback_modes():
    sessions = [
        {
            "type": "movie",
            "Player": {"state": "playing"},
            "TranscodeSession": {"protocol": "http"},
        },
        {
            "type": "episode",
            "Player": [{"state": "buffering"}],
            "TranscodeSession": {"videoDecision": "copy", "audioDecision": "transcode"},
        },
        {
            "type": "movie",
            "Player": {"state": "playing"},
            "TranscodeSession": {"videoDecision": "transcode", "audioDecision": "transcode"},
        },
        {"type": "clip", "Player": {"state": "paused"}},
        {"type": "track", "Player": {"state": "playing"}},
    ]

    assert (
        count_active_plex_playbacks(
            session_provider=lambda: sessions,
            transcoder_counter=lambda: 0,
        )
        == 3
    )


def test_count_active_plex_playbacks_keeps_process_transcode_detection():
    assert (
        count_active_plex_playbacks(
            session_provider=lambda: [],
            transcoder_counter=lambda: 1,
        )
        == 1
    )


def test_wait_for_plex_playback_retries_until_idle():
    readings = iter([2, 1, 0])
    messages = []
    sleeps = []

    wait_for_plex_playback_idle(
        poll_seconds=15,
        logger=messages.append,
        counter=lambda: next(readings),
        sleeper=sleeps.append,
    )

    assert len(messages) == 2
    assert all("active playback" in message for message in messages)
    assert sleeps == [15, 15]


def test_wait_for_plex_playback_retries_when_status_is_unavailable():
    readings = iter([PlexStatusError("unavailable"), 0])
    messages = []
    sleeps = []

    def counter():
        reading = next(readings)
        if isinstance(reading, Exception):
            raise reading
        return reading

    wait_for_plex_playback_idle(
        poll_seconds=5,
        logger=messages.append,
        counter=counter,
        sleeper=sleeps.append,
    )

    assert messages == ["Could not inspect Plex activity; retrying in 5s: unavailable"]
    assert sleeps == [5]
