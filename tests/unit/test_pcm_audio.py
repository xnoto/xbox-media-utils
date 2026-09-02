"""Regression tests for PCM audio normalization."""

from pathlib import Path

import pytest

from xbox_media_utils.media import analyze_recode_needs
from xbox_media_utils.models import AudioTrack, MediaInfo


@pytest.mark.parametrize("codec", ["pcm_s16le", "pcm_s24le", "pcm_s32le"])
def test_analyze_recode_needs_marks_pcm_stereo_for_audio_recode(codec):
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        audio_tracks=[AudioTrack(index=1, codec=codec, channels=2, is_default=True)],
    )

    analyze_recode_needs(info)

    assert info.needs_audio_recode is True
    assert info.audio_tracks[0].recode_reason == f"incompatible codec: {codec} -> AAC stereo"
