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


def test_import_file_archives_dovi_original_and_imports_hdr10_primary(tmp_path: Path, monkeypatch):
    plex_root = tmp_path / "plex"
    dest_dir = plex_root / "movies" / "Movie"
    backup_root = plex_root / "backup"
    source = tmp_path / "source" / "movie.mkv"
    source.parent.mkdir()
    source.write_text("dovi")
    hdr10_path = dest_dir / "movie.HDR10.mkv"

    info = MediaInfo(
        path=source,
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="hdr10",
        dovi_profile=8,
        has_dovi_profile_8=True,
    )

    def fake_set_ownership(path, user, group):
        return True, None

    def fake_create_hdr10_copy(info_arg, dest_dir_arg):
        hdr10_path.parent.mkdir(parents=True, exist_ok=True)
        hdr10_path.write_text("hdr10")
        return True, "HDR10 copy created", hdr10_path

    monkeypatch.setattr(import_cli, "set_ownership", fake_set_ownership)
    monkeypatch.setattr(import_cli, "create_hdr10_copy", fake_create_hdr10_copy)

    result = import_cli.import_file(
        info,
        dest_dir,
        plex_root,
        dovi_backup_root=backup_root,
    )

    archived_path = backup_root / "movies" / "Movie" / "movie.DV.mkv"
    primary_path = dest_dir / "movie.mkv"
    assert result["status"] == "success"
    assert result["action"] == "hdr10-copy"
    assert result["destination"] == str(primary_path)
    assert result["archived_dovi_path"] == str(archived_path)
    assert primary_path.read_text() == "hdr10"
    assert archived_path.read_text() == "dovi"
    assert source.read_text() == "dovi"
    assert not hdr10_path.exists()


def test_import_file_refuses_incompatible_format(tmp_path: Path):
    source = tmp_path / "movie.mkv"
    source.write_text("input")
    dest_dir = tmp_path / "dest"

    info = MediaInfo(
        path=source,
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=5,
        incompatible_reason="Dolby Vision Profile 5 cannot be tonemapped (libdovi required)",
    )

    result = import_cli.import_file(info, dest_dir, tmp_path)

    assert result["status"] == "incompatible"
    assert result["action"] == "skip"
    assert "Profile 5" in result["error"]
    # No file should be written.
    assert not (dest_dir / "movie.mkv").exists()
