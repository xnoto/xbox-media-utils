"""Tests for ffmpeg command generation."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from xbox_media_utils.ffmpeg import (
    _is_vaapi_error,
    build_ffmpeg_cmd,
    run_ffmpeg_command,
    run_ffmpeg_with_fallback,
    validate_output,
)
from xbox_media_utils.media import can_use_vaapi
from xbox_media_utils.models import AudioTrack, MediaInfo


@pytest.fixture(autouse=True)
def stub_static_ffmpeg(monkeypatch):
    monkeypatch.setattr("xbox_media_utils.ffmpeg.ffmpeg_path", lambda: "ffmpeg")


def test_build_ffmpeg_cmd_uses_mono_duplication_filter_for_mono_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        audio_tracks=[AudioTrack(index=1, codec="aac", channels=1, needs_recode=True)],
    )

    cmd = build_ffmpeg_cmd(info, Path("movie.xbox.mkv"), use_vaapi=False)

    assert "-c:a:0" in cmd
    assert "aac" in cmd
    assert "-filter:a:0" in cmd
    assert "pan=stereo|c0=c0|c1=c0" in cmd


def test_build_ffmpeg_cmd_uses_downmix_filter_for_multichannel_audio():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        audio_tracks=[AudioTrack(index=1, codec="dts", channels=6, needs_recode=True)],
    )

    cmd = build_ffmpeg_cmd(info, Path("movie.xbox.mkv"), use_vaapi=False)

    assert "-filter:a:0" in cmd
    assert any("pan=stereo|FL=" in part for part in cmd)


def test_build_ffmpeg_cmd_recodes_incompatible_stereo_audio_without_pan_filter():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        audio_tracks=[AudioTrack(index=1, codec="opus", channels=2, needs_recode=True)],
    )

    cmd = build_ffmpeg_cmd(info, Path("movie.xbox.mkv"), use_vaapi=False)

    assert "-c:a:0" in cmd
    assert "aac" in cmd
    assert "-filter:a:0" not in cmd


def test_build_ffmpeg_cmd_tonemaps_dolby_vision_to_sdr_bt709():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        needs_video_recode=True,
        dovi_profile=5,
    )

    cmd = build_ffmpeg_cmd(info, Path("movie.xbox.mkv"), use_vaapi=True)

    assert "-hwaccel" not in cmd
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "tonemap=hable" in vf
    assert "zscale=transfer=bt709:primaries=bt709:matrix=bt709" in vf
    assert "format=yuv420p" in vf
    assert "libx265" in cmd
    assert "-pix_fmt" in cmd
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[cmd.index("-color_primaries") + 1] == "bt709"
    assert cmd[cmd.index("-color_trc") + 1] == "bt709"
    assert cmd[cmd.index("-colorspace") + 1] == "bt709"


def test_build_ffmpeg_cmd_recodes_10bit_sdr_hevc_to_8bit_main():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_bit_depth=10,
        video_hdr=False,
        needs_video_recode=True,
    )

    cmd = build_ffmpeg_cmd(info, Path("movie.xbox.mkv"), use_vaapi=False)

    assert "libx265" in cmd
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    # Must not opt into Main 10 for SDR.
    if "-x265-params" in cmd:
        assert "profile=main10" not in cmd[cmd.index("-x265-params") + 1]


def test_build_ffmpeg_cmd_keeps_10bit_for_hdr_hevc_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_bit_depth=10,
        video_hdr=True,
        needs_video_recode=True,
    )

    cmd = build_ffmpeg_cmd(info, Path("movie.xbox.mkv"), use_vaapi=False)

    assert "libx265" in cmd
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p10le"
    assert "profile=main10" in cmd[cmd.index("-x265-params") + 1]


def test_build_ffmpeg_cmd_preserves_2160p24_h264_geometry_on_vaapi_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="h264",
        video_profile="high",
        video_bit_depth=8,
        video_width=3840,
        video_height=2160,
        video_frame_rate=23.976,
        needs_video_recode=True,
    )

    cmd = build_ffmpeg_cmd(info, Path("movie.xbox.mkv"), use_vaapi=True)

    assert "hevc_vaapi" in cmd
    assert "-vf" not in cmd


def test_build_ffmpeg_cmd_downscales_and_caps_frame_rate_only_above_4k60():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_profile="main",
        video_bit_depth=8,
        video_width=7680,
        video_height=4320,
        video_frame_rate=120.0,
        needs_video_recode=True,
    )

    cmd = build_ffmpeg_cmd(info, Path("movie.xbox.mkv"), use_vaapi=False)

    vf = cmd[cmd.index("-vf") + 1]
    assert "scale=3840:2160" in vf
    assert "force_original_aspect_ratio=decrease" in vf
    assert "fps=60" in vf


def test_can_use_vaapi_returns_false_when_video_needs_dimension_filter():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_bit_depth=8,
        video_width=7680,
        video_height=4320,
        video_frame_rate=24.0,
        needs_video_recode=True,
    )

    assert can_use_vaapi(info) is False


def test_validate_output_preserves_2160p_h264_geometry_when_converting_to_hevc(
    tmp_path, monkeypatch
):
    source = tmp_path / "movie.mkv"
    output = tmp_path / "movie.xbox.mkv"
    source.write_bytes(b"x" * 100)
    output.write_bytes(b"x" * 50)
    info = MediaInfo(
        path=source,
        video_codec="h264",
        video_width=3840,
        video_height=2160,
        video_frame_rate=23.976,
        needs_video_recode=True,
    )
    stream_data = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 3840,
                "height": 2160,
                "avg_frame_rate": "24000/1001",
                "pix_fmt": "yuv420p",
            }
        ]
    }
    monkeypatch.setattr("xbox_media_utils.ffmpeg.get_best_duration", lambda path: 60.0)
    monkeypatch.setattr("xbox_media_utils.ffmpeg.ffprobe_path", lambda: "ffprobe")
    monkeypatch.setattr(
        "xbox_media_utils.ffmpeg.run_cmd",
        lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(stream_data), stderr=""),
    )

    assert validate_output(info, output) == (True, "OK")


def test_validate_output_rejects_unrequested_resolution_loss(tmp_path, monkeypatch):
    source = tmp_path / "movie.mkv"
    output = tmp_path / "movie.xbox.mkv"
    source.write_bytes(b"x" * 100)
    output.write_bytes(b"x" * 50)
    info = MediaInfo(
        path=source,
        video_codec="h264",
        video_width=3840,
        video_height=2160,
        video_frame_rate=24.0,
        needs_video_recode=True,
    )
    stream_data = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1920,
                "height": 1080,
                "avg_frame_rate": "24/1",
                "pix_fmt": "yuv420p",
            }
        ]
    }
    monkeypatch.setattr("xbox_media_utils.ffmpeg.get_best_duration", lambda path: 60.0)
    monkeypatch.setattr("xbox_media_utils.ffmpeg.ffprobe_path", lambda: "ffprobe")
    monkeypatch.setattr(
        "xbox_media_utils.ffmpeg.run_cmd",
        lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(stream_data), stderr=""),
    )

    valid, message = validate_output(info, output)

    assert valid is False
    assert message == "Resolution changed: 3840x2160 -> 1920x1080"


def test_can_use_vaapi_returns_false_for_dolby_vision_recode():
    info = MediaInfo(
        path=Path("movie.mkv"),
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        needs_video_recode=True,
        dovi_profile=5,
    )

    assert can_use_vaapi(info) is False


def test_vaapi_device_initialization_error_is_fallback_eligible():
    stderr = """
    [VAAPI @ 0x562663f611c0] Failed to initialise VAAPI connection: -1 (unknown libva error).
    Device creation failed: -5.
    Failed to set value '/dev/dri/renderD128' for option 'vaapi_device': Input/output error
    Error parsing global options: Input/output error
    """

    assert _is_vaapi_error(stderr) is True


def test_run_ffmpeg_with_fallback_retries_vaapi_device_init_failure(monkeypatch, tmp_path):
    info = MediaInfo(
        path=Path("episode.mkv"),
        video_codec="vc1",
        video_bit_depth=8,
        needs_video_recode=True,
        audio_tracks=[AudioTrack(index=1, codec="ac3", channels=6, needs_recode=True)],
    )
    output = tmp_path / "episode.mkv"
    calls = []

    def fake_run(cmd, capture_output, text):
        calls.append(cmd)
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                cmd,
                1,
                stderr="Failed to set value '/dev/dri/renderD128' for option 'vaapi_device'",
            )
        return subprocess.CompletedProcess(cmd, 0, stderr="")

    monkeypatch.setattr("xbox_media_utils.ffmpeg.subprocess.run", fake_run)

    success, error = run_ffmpeg_with_fallback(info, output, use_hardware=True)

    assert success is True
    assert error == ""
    assert len(calls) == 2
    assert "hevc_vaapi" in calls[0]
    assert "libx265" in calls[1]


def test_run_ffmpeg_command_pauses_and_resumes_for_plex_transcode(monkeypatch):
    readings = iter([1, 0, 0])
    monkeypatch.setattr(
        "xbox_media_utils.ffmpeg.count_active_plex_transcodes",
        lambda: next(readings, 0),
    )
    messages = []

    result = run_ffmpeg_command(
        [sys.executable, "-c", "import time; time.sleep(0.2); print('done')"],
        pause_for_plex=True,
        plex_poll_seconds=0.01,
        logger=messages.append,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "done"
    assert any("Paused recode" in message for message in messages)
    assert any("Resumed recode" in message for message in messages)
