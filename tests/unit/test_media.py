"""Tests for media analysis decisions."""

from pathlib import Path

from xbox_media_utils.media import analyze_recode_needs
from xbox_media_utils.models import AudioTrack, MediaInfo


def test_analyze_recode_needs_marks_opus_stereo_for_audio_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        audio_tracks=[AudioTrack(index=1, codec="opus", channels=2, is_default=True)],
    )

    analyze_recode_needs(info)

    assert info.needs_audio_recode is True
    assert info.audio_tracks[0].recode_reason == "incompatible codec: opus -> AAC stereo"


def test_analyze_recode_needs_marks_default_mono_track_for_audio_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        audio_tracks=[AudioTrack(index=1, codec="aac", channels=1, is_default=True)],
    )

    analyze_recode_needs(info)

    assert info.needs_audio_recode is True
    assert info.audio_tracks[0].recode_reason == "mono primary track -> AAC stereo"


def test_analyze_recode_needs_skips_non_default_mono_commentary_when_main_track_exists():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        audio_tracks=[
            AudioTrack(index=1, codec="aac", channels=2, is_default=True),
            AudioTrack(index=2, codec="aac", channels=1, is_default=False, title="Commentary"),
        ],
    )

    analyze_recode_needs(info)

    assert info.needs_audio_recode is False
    assert info.audio_tracks[1].needs_recode is False


def test_analyze_recode_needs_marks_av1_video_for_video_recode():
    info = MediaInfo(path=Path("movie.mkv"), video_codec="av1")

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.video_recode_reason == "incompatible codec: av1"
