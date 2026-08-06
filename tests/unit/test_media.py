"""Tests for media analysis decisions."""

import json
import subprocess
from pathlib import Path

import pytest

from xbox_media_utils.media import analyze_recode_needs, probe_file
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
    assert info.audio_tracks[0].recode_reason == "mono track -> AAC stereo"


def test_analyze_recode_needs_marks_non_default_mono_commentary_for_audio_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        audio_tracks=[
            AudioTrack(index=1, codec="aac", channels=2, is_default=True),
            AudioTrack(index=2, codec="aac", channels=1, is_default=False, title="Commentary"),
        ],
    )

    analyze_recode_needs(info)

    assert info.needs_audio_recode is True
    assert info.audio_tracks[1].needs_recode is True
    assert info.audio_tracks[1].recode_reason == "mono track -> AAC stereo"


def test_analyze_recode_needs_marks_av1_video_for_video_recode():
    info = MediaInfo(path=Path("movie.mkv"), video_codec="av1")

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.video_recode_reason == "incompatible codec: av1"


def test_probe_file_records_video_compatibility_dimensions(monkeypatch):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "profile": "High",
                "pix_fmt": "yuv420p",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "24000/1001",
            }
        ],
        "format": {},
    }
    monkeypatch.setattr(
        "xbox_media_utils.media.run_cmd",
        lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr=""),
    )
    monkeypatch.setattr("xbox_media_utils.media.ffprobe_path", lambda: "ffprobe")

    info = probe_file(Path("movie.mkv"))

    assert info.video_profile == "high"
    assert info.video_pixel_format == "yuv420p"
    assert info.video_width == 3840
    assert info.video_height == 2160
    assert info.video_frame_rate == pytest.approx(23.976, abs=0.001)
    assert info.needs_video_recode is True


def test_analyze_recode_needs_marks_2160p_h264_for_hevc_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        video_profile="high",
        video_bit_depth=8,
        video_width=3840,
        video_height=2160,
        video_frame_rate=23.976,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.video_recode_reason == ("H.264 3840x2160 exceeds Xbox H.264 1080p60 support")


@pytest.mark.parametrize(
    ("width", "height"),
    [
        (1920, 1080),
        (1080, 1920),
        (1440, 1080),
    ],
)
def test_analyze_recode_needs_keeps_h264_within_oriented_1080p_limit(width, height):
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        video_profile="high",
        video_bit_depth=8,
        video_width=width,
        video_height=height,
        video_frame_rate=60.0,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is False


def test_analyze_recode_needs_keeps_2160p60_main10_hdr_hevc():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_profile="main 10",
        video_bit_depth=10,
        video_width=3840,
        video_height=2160,
        video_frame_rate=60.0,
        video_hdr=True,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is False


@pytest.mark.parametrize(
    ("width", "height", "frame_rate"),
    [
        (7680, 4320, 24.0),
        (3840, 2160, 120.0),
    ],
)
def test_analyze_recode_needs_caps_hevc_above_4k60(width, height, frame_rate):
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_profile="main",
        video_bit_depth=8,
        video_width=width,
        video_height=height,
        video_frame_rate=frame_rate,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.video_recode_reason == "HEVC exceeds Xbox Main/Main10 4K60 support"


def test_analyze_recode_needs_marks_dolby_vision_profile_5_for_video_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=5,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.video_recode_reason == "Dolby Vision Profile 5 is incompatible with Plex on Xbox"


def test_analyze_recode_needs_marks_unknown_dolby_vision_for_video_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.video_recode_reason == "Dolby Vision is incompatible with Plex on Xbox"


def test_analyze_recode_needs_marks_10bit_sdr_hevc_for_video_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_bit_depth=10,
        video_hdr=False,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.video_recode_reason == "10-bit SDR hevc crashes Plex on Xbox"


def test_analyze_recode_needs_leaves_10bit_hdr_hevc_alone():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_bit_depth=10,
        video_hdr=True,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is False


def test_analyze_recode_needs_leaves_8bit_sdr_hevc_alone():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_bit_depth=8,
        video_hdr=False,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is False


def test_analyze_recode_needs_blocks_dolby_vision_profile_5_as_incompatible():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=5,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.incompatible_reason is not None
    assert "Profile 5" in info.incompatible_reason
    assert "libdovi" in info.incompatible_reason


def test_analyze_recode_needs_blocks_dolby_vision_profile_7_as_incompatible():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=7,
    )

    analyze_recode_needs(info)

    assert info.incompatible_reason is not None
    assert "Profile 7" in info.incompatible_reason


def test_analyze_recode_needs_keeps_dolby_vision_profile_8_processable():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=8,
    )

    analyze_recode_needs(info)

    assert info.needs_video_recode is True
    assert info.incompatible_reason is None


def test_analyze_recode_needs_treats_dovi_profile_as_dolby_vision_when_hdr10_tagged():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="hdr10",
        dovi_profile=8,
    )

    analyze_recode_needs(info)

    assert info.video_hdr_type == "dolby vision"
    assert info.needs_video_recode is True
    assert info.incompatible_reason is None


def test_analyze_recode_needs_blocks_unknown_dolby_vision_as_incompatible():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
    )

    analyze_recode_needs(info)

    assert info.incompatible_reason is not None
