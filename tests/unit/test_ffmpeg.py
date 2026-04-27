"""Tests for ffmpeg command generation."""

from pathlib import Path

from xbox_media_utils.ffmpeg import build_ffmpeg_cmd
from xbox_media_utils.media import can_use_vaapi
from xbox_media_utils.models import AudioTrack, MediaInfo


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
