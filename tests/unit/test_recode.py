from pathlib import Path
from types import SimpleNamespace

from xbox_media_utils.cli import recode
from xbox_media_utils.models import MediaInfo, SubtitleTrack


def test_process_file_sets_ownership_for_extracted_subtitles(tmp_path: Path, monkeypatch):
    media_path = tmp_path / "movie.mkv"
    media_path.write_text("input")
    subtitle_path = tmp_path / "movie.en.srt"
    output_path = tmp_path / "movie.xbox.mkv"

    info = MediaInfo(
        path=media_path,
        video_codec="h264",
        subtitle_tracks=[SubtitleTrack(index=2, codec="subrip", language="eng", is_text=True)],
    )

    ownership_calls: list[tuple[Path, str, str]] = []

    def fake_extract_subtitles(*args, **kwargs):
        subtitle_path.write_text("subtitle")
        return [{"success": True, "output": str(subtitle_path)}]

    def fake_run(cmd, capture_output, text):
        output_path.write_text("remuxed")
        return SimpleNamespace(returncode=0, stderr="")

    def fake_set_ownership(path, user, group):
        ownership_calls.append((Path(path), user, group))
        return True, None

    monkeypatch.setattr(recode, "extract_subtitles", fake_extract_subtitles)
    monkeypatch.setattr(recode.subprocess, "run", fake_run)
    monkeypatch.setattr(recode, "validate_output", lambda info, path: (True, "OK"))
    monkeypatch.setattr(recode, "set_ownership", fake_set_ownership)

    result = recode.process_file(info)

    assert result["status"] == "success"
    assert (subtitle_path, "plex", "libstoragemgmt") in ownership_calls
    assert (media_path, "plex", "libstoragemgmt") in ownership_calls


def test_process_file_refuses_incompatible_format(tmp_path: Path):
    media_path = tmp_path / "movie.mkv"
    media_path.write_text("input")

    info = MediaInfo(
        path=media_path,
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=5,
        incompatible_reason="Dolby Vision Profile 5 cannot be tonemapped (libdovi required)",
    )

    result = recode.process_file(info)

    assert result["status"] == "incompatible"
    assert result["video_action"] == "skip"
    assert "Profile 5" in result["error"]


def test_write_incompatible_report_lists_only_blocked_files(tmp_path: Path):
    blocked = MediaInfo(
        path=Path("/lib/Show.S01E01.mkv"),
        video_codec="hevc",
        video_bit_depth=10,
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=5,
        incompatible_reason="Dolby Vision Profile 5 cannot be tonemapped",
    )
    fine = MediaInfo(path=Path("/lib/Show.S01E02.mkv"), video_codec="hevc")
    output = tmp_path / "incompat.txt"

    count = recode.write_incompatible_report([blocked, fine], output)

    assert count == 1
    contents = output.read_text(encoding="utf-8")
    assert "/lib/Show.S01E01.mkv" in contents
    assert "/lib/Show.S01E02.mkv" not in contents
    assert "DV Profile 5" in contents
    assert "10-bit" in contents
    # Tab-separated: path<tab>details<tab>reason
    line = contents.strip().split("\n")[0]
    assert line.count("\t") == 2
