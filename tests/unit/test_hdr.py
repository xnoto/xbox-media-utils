from pathlib import Path

from xbox_media_utils.hdr import promote_hdr10_copy
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


def test_promote_hdr10_copy_fails_if_archive_already_exists(tmp_path: Path):
    primary = tmp_path / "movie.mkv"
    hdr10 = tmp_path / "movie.HDR10.mkv"
    dv_path = tmp_path / "movie.DV.mkv"
    primary.write_text("dovi")
    hdr10.write_text("hdr10")
    dv_path.write_text("existing")

    info = MediaInfo(path=primary)

    success, message, archived = promote_hdr10_copy(info, hdr10)

    assert success is False
    assert message == f"Archive path already exists: {dv_path.name}"
    assert archived is None
    assert primary.read_text() == "dovi"
    assert hdr10.read_text() == "hdr10"
    assert dv_path.read_text() == "existing"
