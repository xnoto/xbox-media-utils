"""Tests for ffmpeg command generation."""

from pathlib import Path

from xbox_media_utils.ffmpeg import build_ffmpeg_cmd
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
