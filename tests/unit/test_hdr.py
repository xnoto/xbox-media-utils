from pathlib import Path
from types import SimpleNamespace

from xbox_media_utils import hdr
from xbox_media_utils.hdr import (
    create_hdr10_copy,
    get_dovi_archive_path,
    promote_hdr10_copy,
)
from xbox_media_utils.models import MediaInfo


def test_promote_hdr10_copy_swaps_primary_and_archives_dovi(tmp_path: Path):
    primary = tmp_path / "movie.mkv"
    hdr10 = tmp_path / "movie.HDR10.mkv"
    primary.write_text("dovi")
    hdr10.write_text("hdr10")

    info = MediaInfo(path=primary)

    success, message, dv_path = promote_hdr10_copy(info, hdr10)

    assert success is True
    assert message == "HDR10 copy promoted to primary"
    assert dv_path == tmp_path / "movie.DV.mkv"
    assert primary.read_text() == "hdr10"
    assert dv_path.read_text() == "dovi"
    assert not hdr10.exists()


def test_promote_hdr10_copy_replaces_primary_if_archive_already_exists(tmp_path: Path):
    primary = tmp_path / "movie.mkv"
    hdr10 = tmp_path / "movie.HDR10.mkv"
    dv_path = tmp_path / "movie.DV.mkv"
    primary.write_text("dovi")
    hdr10.write_text("hdr10")
    dv_path.write_text("existing")

    info = MediaInfo(path=primary)

    success, message, archived = promote_hdr10_copy(info, hdr10)

    assert success is True
    assert message == "HDR10 copy promoted to primary"
    assert archived == dv_path
    assert primary.read_text() == "hdr10"
    assert not hdr10.exists()
    assert dv_path.read_text() == "existing"


def test_get_dovi_archive_path_uses_backup_root_and_preserves_relative_path(tmp_path: Path):
    plex_root = tmp_path / "plex"
    primary = plex_root / "movies" / "Movie" / "movie.mkv"

    archive_path = get_dovi_archive_path(primary, plex_root / "backup", plex_root)

    assert archive_path == plex_root / "backup" / "movies" / "Movie" / "movie.DV.mkv"


def test_create_hdr10_copy_strips_dovi_rpu_with_dovi_bsf(tmp_path: Path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_bytes(b"x" * 100)
    info = MediaInfo(path=source, has_dovi_profile_8=True)
    captured_cmd = []

    def fake_run_cmd(cmd):
        captured_cmd.extend(cmd)
        Path(cmd[-1]).write_bytes(b"y" * 95)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(hdr, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr(hdr, "run_cmd", fake_run_cmd)

    success, message, output_path = create_hdr10_copy(info, tmp_path, logger=lambda _msg: None)

    assert success is True
    assert message == "HDR10 copy created"
    assert output_path == tmp_path / "movie.HDR10.mkv"
    assert "-bsf:v" in captured_cmd
    assert "dovi_rpu=strip=1" in captured_cmd
