"""Shared test fixtures and configuration."""

import pytest


@pytest.fixture
def mock_media_info():
    """Create a mock MediaInfo object for testing."""
    from unittest.mock import MagicMock

    info = MagicMock()
    info.path = MagicMock()
    info.path.name = "test_movie.mkv"
    info.path.stem = "test_movie"
    info.path.suffix = ".mkv"
    info.path.with_suffix = MagicMock(return_value=info.path)
    info.probe_error = None
    info.needs_video_recode = False
    info.needs_audio_recode = False
    info.video_recode_reason = None
    info.audio_recode_reason = None
    info.subtitle_tracks = []
    info.has_dovi_profile_8 = False
    info.dovi_profile = None
    return info
