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
