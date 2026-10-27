"""RAR import orchestration without external extraction or media tools."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from xbox_media_utils.cli import import_ as cli
from xbox_media_utils.models import MediaInfo


@pytest.fixture
def rar_import(tmp_path, monkeypatch):
    source = tmp_path / "Source Movie"
    source.mkdir()
    plex = tmp_path / "plex"
    (plex / "movies").mkdir(parents=True)
    calls = SimpleNamespace(extract=[], imports=[], scans=[], stages=[])
    monkeypatch.setattr(sys, "argv", ["xbox-import", str(source), "--plex", str(plex)])
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/usr/bin/unrar")
    monkeypatch.setattr(cli, "probe_file", lambda path: MediaInfo(path=path))
    monkeypatch.setattr(cli, "write_log_entry", lambda *args, **kwargs: None)

    def import_file(info, dest_dir, *args, **kwargs):
        calls.imports.append((info.path, dest_dir))
        assert info.path.exists()
        if kwargs.get("dry_run"):
            return {"status": "would_import", "action": "copy", "destination": str(dest_dir)}
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / info.path.name
        target.write_bytes(info.path.read_bytes())
        return {"status": "success", "action": "copy", "destination": str(target)}

    def scan(target):
        calls.scans.append(target)
        return True

    monkeypatch.setattr(cli, "import_file", import_file)
    monkeypatch.setattr(cli, "trigger_plex_scan", scan)
    return source, plex, calls


def extractor(monkeypatch, calls, contents):
    def run(argv, **kwargs):
        archive = Path(argv[-2])
        stage = Path(argv[-1])
        calls.extract.append(archive)
        calls.stages.append(stage.parent)
        assert argv[:9] == [
            "/usr/bin/unrar",
            "x",
            "-cfg-",
            "-ol-",
            "-o-",
            "-p-",
            "-idq",
            "--",
            str(archive),
        ]
        assert archive.is_absolute()
        assert argv[-1].endswith(os.sep)
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["cwd"] == stage
        assert kwargs["capture_output"] and kwargs["text"]
        for relative, data in contents[archive.name].items():
            output = stage / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", run)


def run_main(expected=0):
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == expected


def test_nested_archive_import_preserves_source_and_cleans_stage(rar_import, monkeypatch):
    source, plex, calls = rar_import
    nested = source / "Season 01"
    nested.mkdir()
    archive = nested / "episode.RaR"
    archive.write_bytes(b"original archive")
    loose = source / "loose.mkv"
    loose.write_bytes(b"original media")
    extractor(
        monkeypatch,
        calls,
        {archive.name: {"Episode/episode.mkv": b"extracted", "note.txt": b"note"}},
    )
    run_main()
    dest = plex / "movies" / source.name
    assert (dest / "Season 01/Episode/episode.mkv").read_bytes() == b"extracted"
    assert (dest / "loose.mkv").read_bytes() == b"original media"
    assert archive.read_bytes() == b"original archive"
    assert loose.read_bytes() == b"original media"
    assert sorted(path.relative_to(source) for path in source.rglob("*")) == [
        Path("Season 01"),
        Path("Season 01/episode.RaR"),
        Path("loose.mkv"),
    ]
    assert calls.scans == [dest]
    assert all(not stage.exists() for stage in calls.stages)
    assert all(stage.parent == source.parent for stage in calls.stages)


def test_extracts_all_sets_before_import_and_skips_continuation_volumes(rar_import, monkeypatch):
    source, _, calls = rar_import
    for name in ["a.part01.rar", "a.part02.rar", "b.RAR", "b.r00"]:
        (source / name).write_bytes(b"archive")
    extractor(monkeypatch, calls, {"a.part01.rar": {"a.mkv": b"a"}, "b.RAR": {"b.mkv": b"b"}})
    normal_import = cli.import_file

    def import_after_extraction(*args, **kwargs):
        assert [path.name for path in calls.extract] == ["a.part01.rar", "b.RAR"]
        return normal_import(*args, **kwargs)

    monkeypatch.setattr(cli, "import_file", import_after_extraction)
    run_main()
    assert len(calls.imports) == 2


@pytest.mark.parametrize("failure", ["failed", "incompatible", "probe", "skipped"])
def test_failed_staged_media_remains_but_successful_media_is_removed(
    rar_import, monkeypatch, capsys, failure
):
    source, _, calls = rar_import
    (source / "movie.rar").write_bytes(b"archive")
    failed_name = "z.HDR10.mkv" if failure == "skipped" else "z.mkv"
    extractor(monkeypatch, calls, {"movie.rar": {"a.mkv": b"good", failed_name: b"bad"}})
    normal_import = cli.import_file

    def import_with_failure(info, *args, **kwargs):
        if info.path.name == failed_name:
            return {"status": failure, "error": "failure"}
        return normal_import(info, *args, **kwargs)

    monkeypatch.setattr(cli, "import_file", import_with_failure)
    if failure == "probe":
        monkeypatch.setattr(
            cli,
            "probe_file",
            lambda path: MediaInfo(
                path=path, probe_error="bad" if path.name == failed_name else None
            ),
        )
    run_main(1)
    stage = calls.stages[0]
    assert not (stage / "0/a.mkv").exists()
    assert (stage / "0" / failed_name).read_bytes() == b"bad"
    assert "Staged files retained at:" in capsys.readouterr().out
    assert calls.scans == []


def test_later_extraction_failure_blocks_all_imports(rar_import, monkeypatch, capsys):
    source, _, calls = rar_import
    for name in ["a.rar", "b.rar"]:
        (source / name).write_bytes(b"archive")
    extractor(monkeypatch, calls, {"a.rar": {"a.mkv": b"media"}, "b.rar": {}})
    normal_run = cli.subprocess.run

    def failing_run(argv, **kwargs):
        result = normal_run(argv, **kwargs)
        if Path(argv[-2]).name == "b.rar":
            result.returncode = 3
            result.stderr = "CRC failure"
        return result

    monkeypatch.setattr(cli.subprocess, "run", failing_run)
    run_main(1)
    assert not calls.imports and not calls.scans
    assert calls.stages[0].exists()
    assert "CRC failure" in capsys.readouterr().err


def test_missing_unrar_fails_only_archive_import(rar_import, monkeypatch, capsys):
    source, _, calls = rar_import
    (source / "movie.rar").write_bytes(b"archive")
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    run_main(1)
    assert "require host-provided unrar" in capsys.readouterr().err
    assert not calls.imports and not calls.scans
    assert not list(source.parent.glob(".xbox-import-*"))


@pytest.mark.parametrize("with_loose_media", [False, True])
def test_dry_run_reports_archives_without_extraction_or_staging(
    rar_import, monkeypatch, capsys, with_loose_media
):
    source, _, calls = rar_import
    (source / "movie.rar").write_bytes(b"archive")
    if with_loose_media:
        (source / "loose.mkv").write_bytes(b"media")
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: pytest.fail("extracted during dry run")
    )
    monkeypatch.setattr(
        cli.tempfile, "mkdtemp", lambda *a, **k: pytest.fail("staged during dry run")
    )
    monkeypatch.setattr(
        cli, "write_log_entry", lambda *a, **k: pytest.fail("logged during dry run")
    )
    run_main()
    assert "Would extract RAR" in capsys.readouterr().out
    assert len(calls.imports) == int(with_loose_media)
    assert not calls.scans


@pytest.mark.parametrize("collision", ["loose", "archive", "existing", "extension"])
def test_collisions_block_imports(rar_import, monkeypatch, collision):
    source, plex, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    contents = {"a.rar": {"movie.mkv": b"a"}}
    if collision in {"loose", "extension"}:
        name = "movie.mp4" if collision == "extension" else "movie.mkv"
        (source / name).write_bytes(b"original")
    elif collision == "archive":
        (source / "b.rar").write_bytes(b"archive")
        contents["b.rar"] = {"movie.mkv": b"b"}
    else:
        destination = plex / "movies" / source.name
        destination.mkdir()
        (destination / "movie.mkv").write_bytes(b"existing")
    extractor(monkeypatch, calls, contents)
    run_main(1)
    assert not calls.imports and not calls.scans
    assert calls.stages[0].exists()


@pytest.mark.parametrize("special", ["symlink", "fifo"])
def test_unsafe_extracted_outputs_rejected_before_import(rar_import, monkeypatch, special):
    source, _, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")

    def unsafe_run(argv, **kwargs):
        stage = Path(argv[-1])
        output = stage / "unsafe.mkv"
        if special == "symlink":
            output.symlink_to(source / "a.rar")
        else:
            os.mkfifo(output)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", unsafe_run)
    run_main(1)
    assert not calls.imports and not calls.scans


def test_scan_failure_still_cleans_successfully_imported_stage(rar_import, monkeypatch):
    source, _, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    extractor(monkeypatch, calls, {"a.rar": {"a.mkv": b"a"}})
    monkeypatch.setattr(cli, "trigger_plex_scan", lambda target: False)
    run_main(1)
    assert all(not stage.exists() for stage in calls.stages)


def test_standalone_media_does_not_inspect_neighbor_archives(rar_import, monkeypatch):
    source, plex, calls = rar_import
    media = source / "movie.mkv"
    media.write_bytes(b"media")
    (source / "movie.rar").write_bytes(b"archive")
    monkeypatch.setattr(
        sys, "argv", ["xbox-import", str(media), "--plex", str(plex), "--no-plex-scan"]
    )
    monkeypatch.setattr(
        cli, "discover_rar_archives", lambda path: pytest.fail("inspected neighbors")
    )
    run_main()
    assert len(calls.imports) == 1
    assert media.exists() and not calls.scans


def test_archive_source_destination_overlap_rejected(rar_import, monkeypatch):
    source, _, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    monkeypatch.setattr(
        sys,
        "argv",
        ["xbox-import", str(source), "--plex", str(source), "--library", "movies"],
    )
    run_main(1)
    assert not calls.imports and not calls.scans


def test_orphan_continuation_volume_is_not_silently_ignored(rar_import):
    source, _, calls = rar_import
    (source / "a.part02.rar").write_bytes(b"archive")
    (source / "loose.mkv").write_bytes(b"media")
    run_main(1)
    assert not calls.imports and not calls.scans


@pytest.mark.parametrize("operation", ["unlink", "rmtree"])
def test_cleanup_errors_fail_and_report_retained_stage(rar_import, monkeypatch, capsys, operation):
    source, _, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    extractor(monkeypatch, calls, {"a.rar": {"a.mkv": b"a"}})
    if operation == "unlink":
        normal_unlink = Path.unlink

        def failed_unlink(path, *args, **kwargs):
            if path.name == "a.mkv" and ".xbox-import-" in str(path):
                raise OSError("unlink denied")
            return normal_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", failed_unlink)
    else:

        def failed_rmtree(path, *args, **kwargs):
            raise OSError("rmtree denied")

        monkeypatch.setattr(cli.shutil, "rmtree", failed_rmtree)
    run_main(1)
    assert calls.stages[0].exists()
    output = capsys.readouterr()
    assert "denied" in output.out + output.err
    assert not calls.scans


def test_extractor_start_error_retains_stage_and_blocks_imports(rar_import, monkeypatch, capsys):
    source, _, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")

    def failed_run(*args, **kwargs):
        raise OSError("cannot start unrar")

    monkeypatch.setattr(cli.subprocess, "run", failed_run)
    run_main(1)
    assert "Staged files retained at:" in capsys.readouterr().err
    assert list(source.parent.glob(".xbox-import-*"))
    assert not calls.imports and not calls.scans


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_unsafe_destination_ancestor_blocks_imports(rar_import, monkeypatch, kind):
    source, plex, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    dest = plex / "movies" / source.name
    dest.mkdir()
    if kind == "symlink":
        (dest / "nested").symlink_to(source, target_is_directory=True)
    else:
        (dest / "nested").write_bytes(b"existing")
    extractor(monkeypatch, calls, {"a.rar": {"nested/a.mkv": b"a"}})
    run_main(1)
    assert not calls.imports and not calls.scans


def test_nested_file_directory_collision_blocks_all_imports(rar_import, monkeypatch):
    source, _, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    (source / "b.rar").write_bytes(b"archive")
    extractor(
        monkeypatch,
        calls,
        {"a.rar": {"movie.mkv": b"a"}, "b.rar": {"movie.mkv/episode.mkv": b"b"}},
    )
    run_main(1)
    assert not calls.imports and not calls.scans


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_invalid_rar_source_blocks_extraction(rar_import, monkeypatch, kind):
    source, _, calls = rar_import
    archive = source / "a.rar"
    if kind == "symlink":
        target = source / "original.bin"
        target.write_bytes(b"archive")
        archive.symlink_to(target)
    else:
        archive.mkdir()
    monkeypatch.setattr(
        cli.subprocess, "run", lambda *a, **k: pytest.fail("extracted unsafe source")
    )
    run_main(1)
    assert not calls.imports and not calls.scans


def test_archive_without_media_fails_and_retains_outputs(rar_import, monkeypatch, capsys):
    source, _, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    extractor(monkeypatch, calls, {"a.rar": {"note.txt": b"note"}})
    run_main(1)
    assert (calls.stages[0] / "0/note.txt").exists()
    assert "No media files found" in capsys.readouterr().out
    assert not calls.imports and not calls.scans


@pytest.mark.parametrize("first", ["part1.RAR", "PART01.rar"])
def test_multipart_first_volume_naming_variants(rar_import, monkeypatch, first):
    source, _, calls = rar_import
    (source / f"movie.{first}").write_bytes(b"archive")
    (source / "movie.PART02.RaR").write_bytes(b"archive")
    extractor(monkeypatch, calls, {f"movie.{first}": {"movie.mkv": b"media"}})
    run_main()
    assert [path.name for path in calls.extract] == [f"movie.{first}"]


@pytest.mark.parametrize("suffix", [".importing.mkv", ".HDR10.mkv", ".HDR10.tmp.mkv"])
def test_generated_intermediate_names_are_reserved_before_import(rar_import, monkeypatch, suffix):
    source, _, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    extractor(
        monkeypatch,
        calls,
        {"a.rar": {"Movie.mkv": b"main", "Movie" + suffix: b"must not overwrite"}},
    )
    run_main(1)
    assert not calls.imports and not calls.scans
    stage = calls.stages[0] / "0"
    assert (stage / "Movie.mkv").read_bytes() == b"main"
    assert (stage / ("Movie" + suffix)).read_bytes() == b"must not overwrite"


@pytest.mark.parametrize("name", ["Movie.en.srt", "Movie.en.2.forced.ass", "Movie.en.sdh.sup"])
def test_existing_subtitle_sidecars_are_preserved_before_any_import(rar_import, monkeypatch, name):
    source, plex, calls = rar_import
    (source / "a.rar").write_bytes(b"archive")
    destination = plex / "movies" / source.name
    destination.mkdir()
    sidecar = destination / name
    sidecar.write_bytes(b"existing subtitle")
    extractor(monkeypatch, calls, {"a.rar": {"Movie.mkv": b"media"}})
    run_main(1)
    assert not calls.imports and not calls.scans
    assert sidecar.read_bytes() == b"existing subtitle"
    assert (calls.stages[0] / "0/Movie.mkv").read_bytes() == b"media"
