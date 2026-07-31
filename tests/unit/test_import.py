import sys
from pathlib import Path

import pytest

from xbox_media_utils.api import PlexError
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


@pytest.mark.parametrize(("scanner_success", "expected"), [(True, True), (False, False)])
def test_trigger_plex_scan_returns_scanner_result(
    tmp_path: Path,
    monkeypatch,
    capsys,
    scanner_success,
    expected,
):
    target = tmp_path / "movies" / "Movie"

    class FakeScanner:
        def scan_path(self, path):
            assert path == target
            return {"success": scanner_success, "message": "scan requested"}

    monkeypatch.setattr(import_cli, "PlexScanner", FakeScanner)

    assert import_cli.trigger_plex_scan(target) is expected
    assert "[plex_scan] scan requested" in capsys.readouterr().out


def test_trigger_plex_scan_handles_plex_errors(tmp_path: Path, monkeypatch, capsys):
    class FakeScanner:
        def scan_path(self, path):
            raise PlexError("Plex unavailable")

    monkeypatch.setattr(import_cli, "PlexScanner", FakeScanner)

    assert import_cli.trigger_plex_scan(tmp_path) is False
    assert "[plex_scan] Plex unavailable" in capsys.readouterr().err


def test_main_scans_imported_directory_after_success(tmp_path: Path, monkeypatch):
    source = tmp_path / "Movie.2024"
    source.mkdir()
    media_files = [source / "movie-part-1.mkv", source / "movie-part-2.mkv"]
    for media_file in media_files:
        media_file.write_text("input")

    plex_root = tmp_path / "plex"
    library_path = plex_root / "movies"
    library_path.mkdir(parents=True)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xbox-import",
            str(source),
            "--plex",
            str(plex_root),
            "--library",
            "movies",
        ],
    )
    monkeypatch.setattr(import_cli, "probe_file", lambda path: MediaInfo(path=path))
    import_calls = []

    def fake_import_file(info, dest_dir, *args, **kwargs):
        import_calls.append(info.path)
        return {
            "status": "success",
            "action": "copy",
            "destination": str(dest_dir / info.path.name),
        }

    monkeypatch.setattr(import_cli, "import_file", fake_import_file)
    monkeypatch.setattr(import_cli, "write_log_entry", lambda *args, **kwargs: None)

    scan_calls = []

    def fake_trigger_plex_scan(target):
        scan_calls.append(target)
        return True

    monkeypatch.setattr(import_cli, "trigger_plex_scan", fake_trigger_plex_scan)

    with pytest.raises(SystemExit) as exc_info:
        import_cli.main()

    assert exc_info.value.code == 0
    assert import_calls == media_files
    assert scan_calls == [library_path / source.name]


def test_main_scans_single_imported_file_and_propagates_failure(tmp_path: Path, monkeypatch):
    source = tmp_path / "movie.mkv"
    source.write_text("input")
    plex_root = tmp_path / "plex"
    library_path = plex_root / "movies"
    library_path.mkdir(parents=True)
    destination = library_path / source.name

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xbox-import",
            str(source),
            "--plex",
            str(plex_root),
            "--library",
            "movies",
        ],
    )
    monkeypatch.setattr(import_cli, "probe_file", lambda path: MediaInfo(path=path))
    monkeypatch.setattr(
        import_cli,
        "import_file",
        lambda *args, **kwargs: {
            "status": "success",
            "action": "copy",
            "destination": str(destination),
        },
    )
    monkeypatch.setattr(import_cli, "write_log_entry", lambda *args, **kwargs: None)

    scan_calls = []

    def fake_trigger_plex_scan(target):
        scan_calls.append(target)
        return False

    monkeypatch.setattr(import_cli, "trigger_plex_scan", fake_trigger_plex_scan)

    with pytest.raises(SystemExit) as exc_info:
        import_cli.main()

    assert exc_info.value.code == 1
    assert scan_calls == [destination]


def test_main_does_not_scan_after_partial_failure(tmp_path: Path, monkeypatch):
    source = tmp_path / "Movie.2024"
    source.mkdir()
    successful_file = source / "part-1.mkv"
    failed_file = source / "part-2.mkv"
    successful_file.write_text("input")
    failed_file.write_text("input")

    plex_root = tmp_path / "plex"
    library_path = plex_root / "movies"
    library_path.mkdir(parents=True)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xbox-import",
            str(source),
            "--plex",
            str(plex_root),
            "--library",
            "movies",
        ],
    )
    monkeypatch.setattr(import_cli, "probe_file", lambda path: MediaInfo(path=path))

    def fake_import_file(info, dest_dir, *args, **kwargs):
        succeeded = info.path == successful_file
        return {
            "status": "success" if succeeded else "failed",
            "action": "copy",
            "destination": str(dest_dir / info.path.name),
            "error": None if succeeded else "import failed",
        }

    monkeypatch.setattr(import_cli, "import_file", fake_import_file)
    monkeypatch.setattr(import_cli, "write_log_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        import_cli,
        "trigger_plex_scan",
        lambda target: pytest.fail(f"unexpected Plex scan for {target}"),
    )

    with pytest.raises(SystemExit) as exc_info:
        import_cli.main()

    assert exc_info.value.code == 1


@pytest.mark.parametrize(
    ("status", "extra_args", "expected_exit"),
    [
        ("failed", [], 1),
        ("would_import", ["--dry-run"], 0),
        ("success", ["--no-plex-scan"], 0),
    ],
)
def test_main_skips_scan_when_import_not_eligible(
    tmp_path: Path,
    monkeypatch,
    status,
    extra_args,
    expected_exit,
):
    source = tmp_path / "movie.mkv"
    source.write_text("input")
    plex_root = tmp_path / "plex"
    library_path = plex_root / "movies"
    library_path.mkdir(parents=True)
    destination = library_path / source.name

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "xbox-import",
            str(source),
            "--plex",
            str(plex_root),
            "--library",
            "movies",
            *extra_args,
        ],
    )
    monkeypatch.setattr(import_cli, "probe_file", lambda path: MediaInfo(path=path))
    monkeypatch.setattr(
        import_cli,
        "import_file",
        lambda *args, **kwargs: {
            "status": status,
            "action": "copy",
            "destination": str(destination),
            "error": "import failed" if status == "failed" else None,
        },
    )
    monkeypatch.setattr(import_cli, "write_log_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        import_cli,
        "trigger_plex_scan",
        lambda target: pytest.fail(f"unexpected Plex scan for {target}"),
    )

    with pytest.raises(SystemExit) as exc_info:
        import_cli.main()

    assert exc_info.value.code == expected_exit
