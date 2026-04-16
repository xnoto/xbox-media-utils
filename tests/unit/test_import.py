from pathlib import Path

from xbox_media_utils.cli import import_ as import_cli
from xbox_media_utils.models import MediaInfo


def test_import_file_sets_ownership_for_hdr10_copy(tmp_path: Path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_text("input")
    hdr10_path = tmp_path / "dest" / "movie.HDR10.mkv"
    dest_dir = tmp_path / "dest"

    info = MediaInfo(path=source, video_codec="hevc")
    info.has_dovi_profile_8 = True

    ownership_calls: list[tuple[Path, str, str]] = []

    def fake_set_ownership(path, user, group):
        ownership_calls.append((Path(path), user, group))
        return True, None

    def fake_copy2(src, dst):
        Path(dst).write_text(Path(src).read_text())

    def fake_create_hdr10_copy(info, dest_dir):
        hdr10_path.parent.mkdir(parents=True, exist_ok=True)
        hdr10_path.write_text("hdr10")
        return True, "HDR10 copy created", hdr10_path

    monkeypatch.setattr(import_cli, "set_ownership", fake_set_ownership)
    monkeypatch.setattr(import_cli.shutil, "copy2", fake_copy2)
    monkeypatch.setattr(import_cli, "create_hdr10_copy", fake_create_hdr10_copy)

    result = import_cli.import_file(info, dest_dir, tmp_path)

    assert result["status"] == "success"
    assert (dest_dir / "movie.mkv", "plex", "libstoragemgmt") in ownership_calls
    assert (hdr10_path, "plex", "libstoragemgmt") in ownership_calls
