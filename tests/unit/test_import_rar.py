"""Archive retry contracts; all extraction/probing/Plex operations are mocked."""

import json
import os
import subprocess
import sys
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from xbox_media_utils.cli import archive_import as archive
from xbox_media_utils.cli import import_ as cli
from xbox_media_utils.models import MediaInfo


@pytest.fixture
def rar_import(tmp_path, monkeypatch):
    source = tmp_path / "Source Movie"
    source.mkdir()
    plex = tmp_path / "plex"
    (plex / "movies").mkdir(parents=True)
    (source / "a.rar").write_bytes(b"archive")
    calls = SimpleNamespace(
        extract=[],
        imports=[],
        scans=[],
        contents={"a.rar": {"Movie.mkv": b"media"}},
        banner="UNRAR 7.10 freeware Copyright (c) 1993-2025 Alexander Roshal",
        infos=None,
        volumes=None,
        failure=None,
        subtitle=False,
        dovi=False,
    )
    monkeypatch.setattr(sys, "argv", ["xbox-import", str(source), "--plex", str(plex)])
    monkeypatch.setattr(archive.shutil, "which", lambda name: "/usr/bin/unrar")
    monkeypatch.setattr(archive, "set_ownership", lambda *args: (True, None))
    monkeypatch.setattr(archive, "validate_output", lambda *args: (True, "OK"))
    monkeypatch.setattr(
        cli, "probe_file", lambda path: MediaInfo(path=path, has_dovi_profile_8=calls.dovi)
    )
    monkeypatch.setattr(cli, "write_log_entry", lambda *args, **kwargs: None)

    def info(name, data, path):
        return SimpleNamespace(
            filename=name,
            file_size=len(data),
            CRC=zlib.crc32(data),
            host_os=archive.rarfile.RAR_OS_UNIX,
            mode=0o100644,
            flags=0,
            file_redir=None,
            type=archive.rarfile.RAR_BLOCK_FILE,
            data_offset=0,
            add_size=0,
            volume_file=str(path),
            is_dir=lambda: False,
            is_file=lambda: True,
            is_symlink=lambda: False,
            needs_password=lambda: False,
        )

    def metadata(path, *, errors, info_callback):
        assert errors == "strict"
        path = Path(path)
        infos = (
            calls.infos
            if calls.infos is not None
            else [info(name, data, path) for name, data in calls.contents[path.name].items()]
        )
        for item in infos:
            info_callback(item)
        return SimpleNamespace(
            infos=infos,
        )

    class Metadata:
        def __init__(self, path, **kwargs):
            self.path = path
            self.infos = metadata(path, **kwargs).infos

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def infolist(self):
            return self.infos

        def volumelist(self):
            return calls.volumes or [self.path]

        def needs_password(self):
            return False

        def strerror(self):
            return None

    monkeypatch.setattr(archive.rarfile, "RarFile", Metadata)
    calls.make_info = info

    def run(argv, **kwargs):
        assert kwargs["stdin"] == subprocess.DEVNULL
        if "x" not in argv:
            return SimpleNamespace(returncode=0, stdout=calls.banner, stderr="")
        assert argv[:8] == ["/usr/bin/unrar", "x", "-cfg-", "-ol-", "-o-", "-p-", "-idq", "--"]
        path, stage = Path(argv[-2]), Path(argv[-1])
        calls.extract.append(stage)
        assert kwargs["cwd"] == stage
        for name, data in calls.contents[path.name].items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if calls.failure:
            calls.failure(stage)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(archive.subprocess, "run", run)

    def import_file(info, dest_dir, plex_root, **kwargs):
        calls.imports.append(info.path)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / info.path.name
        target.write_bytes(info.path.read_bytes())
        result = {"status": "success", "action": "copy", "destination": str(target)}
        if calls.subtitle:
            subtitle = target.with_suffix(".en.srt")
            subtitle.write_bytes(b"subtitle")
            result["subtitles_extracted"] = [{"success": True, "output": str(subtitle)}]
        if calls.dovi:
            original = kwargs["dovi_backup_root"] / target.relative_to(plex_root).with_suffix(
                ".DV.mkv"
            )
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(info.path.read_bytes())
            target.write_bytes(b"HDR10 output")
            result["action"] = "hdr10-copy"
            result["archived_dovi_path"] = str(original)
        return result

    def scan(path):
        calls.scans.append(path)
        return True

    monkeypatch.setattr(cli, "import_file", import_file)
    monkeypatch.setattr(cli, "trigger_plex_scan", scan)
    return source, plex, calls


def run_main(expected=0):
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == expected


def receipt_path(plex):
    return next((plex / "movies/.xbox-import-state").glob("*/receipt.json"))


def destination(source, plex):
    return plex / "movies" / source.name


def test_verified_manual_extraction_imports_normally_then_skips_completed(rar_import):
    source, plex, calls = rar_import
    loose = source / "Movie.mkv"
    loose.write_bytes(b"media")
    run_main()
    assert not calls.extract
    assert calls.imports == [loose]
    assert (destination(source, plex) / "Movie.mkv").read_bytes() == b"media"
    run_main()
    assert calls.imports == [loose] and len(calls.scans) == 1
    assert not calls.extract and loose.read_bytes() == b"media"
    state = receipt_path(plex)
    assert state.stat().st_mode & 0o077 == 0
    assert state.parent.stat().st_mode & 0o077 == 0
    assert (state.parent / "lock").exists()
    assert not list(state.parent.glob("work-*"))


@pytest.mark.parametrize("data", [None, b"me", b"wrong"])
def test_missing_truncated_and_same_size_corrupt_loose_use_staging(rar_import, data):
    source, plex, calls = rar_import
    loose = source / "Movie.mkv"
    if data is not None:
        loose.write_bytes(data)
    run_main()
    assert len(calls.extract) == len(calls.imports) == 1
    assert calls.imports[0] != loose
    assert (destination(source, plex) / "Movie.mkv").read_bytes() == b"media"
    if data is None:
        assert not loose.exists()
    else:
        assert loose.read_bytes() == data
    run_main()
    assert len(calls.extract) == len(calls.imports) == 1


def test_legacy_partial_with_verified_source_is_preserved_without_tools(rar_import, capsys):
    source, plex, calls = rar_import
    (source / "Movie.mkv").write_bytes(b"media")
    target = destination(source, plex)
    target.mkdir()
    (target / "Movie.importing.mkv").write_bytes(b"legacy partial")
    (target / "Movie.en.srt").write_bytes(b"legacy subtitle")
    run_main(1)
    assert not calls.extract and not calls.imports
    assert (target / "Movie.importing.mkv").read_bytes() == b"legacy partial"
    assert (target / "Movie.en.srt").read_bytes() == b"legacy subtitle"
    assert "relocat" in capsys.readouterr().err


@pytest.mark.parametrize(
    "suffix",
    [
        ".mkv",
        ".importing.mkv",
        ".HDR10.mkv",
        ".HDR10.tmp.mkv",
        ".en.srt",
        ".en.2.forced.ass",
        ".en.sdh.sup",
    ],
)
def test_unrecognized_outputs_never_adopted_or_overwritten(rar_import, suffix):
    source, plex, calls = rar_import
    target = destination(source, plex)
    target.mkdir()
    existing = target / ("Movie" + suffix)
    existing.write_bytes(b"media")  # Even identical bytes are not ownership proof.
    run_main(1)
    assert existing.read_bytes() == b"media"
    assert not calls.extract and not calls.imports and not calls.scans


@pytest.mark.parametrize("change", ["archive", "loose", "output", "subtitle", "backup"])
def test_completed_import_rehashes_source_volumes_and_all_artifacts(rar_import, change):
    source, plex, calls = rar_import
    (source / "Movie.mkv").write_bytes(b"media")
    calls.subtitle = True
    calls.dovi = True
    run_main()
    targets = {
        "archive": source / "a.rar",
        "loose": source / "Movie.mkv",
        "output": destination(source, plex) / "Movie.mkv",
        "subtitle": destination(source, plex) / "Movie.en.srt",
        "backup": plex / "backup/movies" / source.name / "Movie.DV.mkv",
    }
    target = targets[change]
    old = target.stat()
    target.write_bytes(b"x" * old.st_size)
    os.utime(target, ns=(old.st_atime_ns, old.st_mtime_ns))
    run_main(1)
    assert len(calls.imports) == 1 and len(calls.scans) == 1
    assert not calls.extract


def test_p8_backup_uses_logical_library_path_not_work_directory(rar_import):
    source, plex, calls = rar_import
    calls.dovi = True
    calls.contents["a.rar"] = {"Season 01/Movie.mkv": b"media"}
    run_main()
    assert (
        plex / "backup/movies" / source.name / "Season 01/Movie.DV.mkv"
    ).read_bytes() == b"media"
    assert (destination(source, plex) / "Season 01/Movie.mkv").read_bytes() == b"HDR10 output"
    run_main()
    assert len(calls.imports) == 1


def test_partial_extraction_interruption_recovers_valid_retained_member(rar_import):
    source, _, calls = rar_import
    calls.failure = lambda stage: (_ for _ in ()).throw(KeyboardInterrupt())
    run_main(130)
    assert not calls.imports and calls.extract[0].exists()
    calls.failure = None
    run_main()
    assert len(calls.extract) == 1 and len(calls.imports) == 1
    assert not calls.extract[0].exists()
    assert (source / "a.rar").read_bytes() == b"archive"


def test_corrupt_interrupted_extraction_retries_fresh_private_set(rar_import):
    _, _, calls = rar_import

    def interrupt(stage):
        (stage / "Movie.mkv").write_bytes(b"bad")
        raise KeyboardInterrupt()

    calls.failure = interrupt
    run_main(130)
    calls.failure = None
    run_main()
    assert len(calls.extract) == 2 and len(calls.imports) == 1


def test_prepared_partial_publish_retries_without_reprocessing(rar_import, monkeypatch):
    source, plex, calls = rar_import
    calls.subtitle = True
    link = archive.os.link
    count = 0

    def interrupt(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("publication interrupted")
        return link(*args, **kwargs)

    monkeypatch.setattr(archive.os, "link", interrupt)
    run_main(1)
    receipt = json.loads(receipt_path(plex).read_text())
    assert receipt["jobs"]["Movie.mkv"]["phase"] == "prepared"
    monkeypatch.setattr(archive.os, "link", link)
    run_main()
    assert len(calls.imports) == len(calls.extract) == 1
    assert (destination(source, plex) / "Movie.en.srt").read_bytes() == b"subtitle"


def test_identical_but_foreign_inode_cannot_satisfy_prepared_publication(rar_import, monkeypatch):
    source, plex, calls = rar_import
    link = archive.os.link
    monkeypatch.setattr(archive.os, "link", lambda *a, **k: (_ for _ in ()).throw(OSError("stop")))
    run_main(1)
    target = destination(source, plex) / "Movie.mkv"
    target.write_bytes(b"media")
    monkeypatch.setattr(archive.os, "link", link)
    run_main(1)
    assert target.read_bytes() == b"media" and len(calls.imports) == 1
    assert not calls.scans


def test_receipt_save_failure_before_prepared_publishes_nothing(rar_import, monkeypatch):
    source, plex, calls = rar_import
    save = archive.save_receipt

    def fail_prepared(state, receipt):
        if any(job["phase"] == "prepared" for job in receipt["jobs"].values()):
            raise OSError("receipt disk full")
        save(state, receipt)

    monkeypatch.setattr(archive, "save_receipt", fail_prepared)
    run_main(1)
    assert not destination(source, plex).exists()
    assert len(calls.imports) == 1 and not calls.scans
    assert json.loads(receipt_path(plex).read_text())["jobs"]["Movie.mkv"]["phase"] == "working"


@pytest.mark.parametrize("failure", ["cleanup", "scan", "complete-save"])
def test_retry_after_publication_never_reprocesses(rar_import, monkeypatch, failure):
    _, plex, calls = rar_import
    rmtree = archive.shutil.rmtree
    scan = cli.trigger_plex_scan
    save = archive.save_receipt
    if failure == "cleanup":
        monkeypatch.setattr(
            archive.shutil,
            "rmtree",
            lambda *a, **k: (_ for _ in ()).throw(OSError("cleanup denied")),
        )
    elif failure == "scan":
        monkeypatch.setattr(cli, "trigger_plex_scan", lambda path: False)
    else:

        def failed_save(state, receipt):
            if any(job["phase"] == "complete" for job in receipt["jobs"].values()):
                raise OSError("complete receipt write failed")
            save(state, receipt)

        monkeypatch.setattr(archive, "save_receipt", failed_save)
    run_main(1)
    monkeypatch.setattr(archive.shutil, "rmtree", rmtree)
    monkeypatch.setattr(cli, "trigger_plex_scan", scan)
    monkeypatch.setattr(archive, "save_receipt", save)
    run_main()
    assert len(calls.imports) == len(calls.extract) == 1
    assert json.loads(receipt_path(plex).read_text())["scan"] == "complete"


def test_no_scan_retains_intent_for_ordinary_retry(rar_import, monkeypatch):
    _, plex, calls = rar_import
    argv = sys.argv[:]
    monkeypatch.setattr(sys, "argv", [*argv, "--no-plex-scan"])
    run_main()
    assert not calls.scans and json.loads(receipt_path(plex).read_text())["scan"] == "pending"
    monkeypatch.setattr(sys, "argv", argv)
    run_main()
    assert len(calls.scans) == len(calls.imports) == 1


@pytest.mark.parametrize(
    "banner",
    [
        "unrar-free 0.3",
        "7-Zip 24.09",
        "UNRAR 7.10 freeware unknown vendor",
        "UNRAR 6.24 freeware Alexander Roshal",
    ],
)
def test_nonvendor_or_old_extractor_rejected(rar_import, banner):
    _, _, calls = rar_import
    calls.banner = banner
    run_main(1)
    assert not calls.extract and not calls.imports


def test_missing_vendor_no_fallback(rar_import, monkeypatch):
    _, _, calls = rar_import
    monkeypatch.setattr(archive.shutil, "which", lambda name: None)
    run_main(1)
    assert not calls.imports and not calls.extract


def test_metadata_cannot_select_any_decompression_backend(rar_import, monkeypatch):
    _, _, calls = rar_import

    def comments(*args, **kwargs):
        archive.rarfile.tool_setup()
        pytest.fail("backend selection must be prohibited")

    monkeypatch.setattr(archive.rarfile, "RarFile", comments)
    run_main(1)
    assert not calls.extract and not calls.imports


@pytest.mark.parametrize(
    "feature",
    [
        "crc",
        "encrypted",
        "redirect",
        "symlink",
        "fifo",
        "duplicate",
        "ancestor",
        "traversal",
        "absolute",
        "backslash",
        "unknown-size",
        "version",
    ],
)
def test_unsafe_or_unsupported_metadata_fails_closed(rar_import, feature):
    source, _, calls = rar_import
    item = calls.make_info("Movie.mkv", b"media", source / "a.rar")
    calls.infos = [item]
    if feature == "crc":
        item.CRC = None
    elif feature == "encrypted":
        item.needs_password = lambda: True
    elif feature == "redirect":
        item.file_redir = (4, 0, "elsewhere")
    elif feature == "symlink":
        item.is_symlink = lambda: True
    elif feature == "fifo":
        item.mode = 0o010644
    elif feature == "duplicate":
        calls.infos.append(item)
    elif feature == "ancestor":
        calls.infos.append(calls.make_info("Movie.mkv/episode.mkv", b"media", source / "a.rar"))
    elif feature == "unknown-size":
        item.file_size = -1
    elif feature == "version":
        item.flags = archive.rarfile.RAR_FILE_VERSION
    else:
        item.filename = {
            "traversal": "../Movie.mkv",
            "absolute": "/Movie.mkv",
            "backslash": r"a\Movie.mkv",
        }[feature]
    run_main(1)
    assert not calls.imports and not calls.extract and not calls.scans


@pytest.mark.parametrize(
    "problem", ["missing", "bad-header", "truncated", "unfinished", "bad-chain"]
)
def test_strict_multipart_errors_block_all_imports(rar_import, monkeypatch, problem):
    source, _, calls = rar_import
    if problem in {"missing", "bad-header"}:

        def broken(*args, **kwargs):
            assert kwargs["errors"] == "strict"
            raise archive.rarfile.BadRarFile(problem)

        monkeypatch.setattr(archive.rarfile, "RarFile", broken)
    else:
        item = calls.make_info("Movie.mkv", b"media", source / "a.rar")
        calls.infos = [item]
        if problem == "truncated":
            item.add_size = 1000
        else:
            item.flags = (
                archive.rarfile.RAR_FILE_SPLIT_AFTER
                if problem == "unfinished"
                else archive.rarfile.RAR_FILE_SPLIT_BEFORE
            )
    run_main(1)
    assert not calls.imports and not calls.extract


def test_continuation_volume_content_is_bound_to_receipt(rar_import):
    source, _, calls = rar_import
    second = source / "a.r00"
    second.write_bytes(b"second")
    calls.volumes = [str(source / "a.rar"), str(second)]
    run_main()
    second.write_bytes(b"change")
    run_main(1)
    assert len(calls.imports) == 1


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_destination_ancestors_including_library_are_checked(rar_import, kind):
    source, plex, calls = rar_import
    library = plex / "movies"
    library.rmdir()
    if kind == "symlink":
        library.symlink_to(source, target_is_directory=True)
    else:
        library.write_bytes(b"not a directory")
    run_main(1)
    assert not calls.imports and not calls.extract


@pytest.mark.parametrize("special", ["symlink", "fifo"])
def test_unsafe_extraction_output_rejected(rar_import, special):
    source, _, calls = rar_import

    def unsafe(stage):
        target = stage / "unsafe.mkv"
        if special == "symlink":
            target.symlink_to(source / "a.rar")
        else:
            os.mkfifo(target)

    calls.failure = unsafe
    run_main(1)
    assert not calls.imports and not calls.scans


def test_same_destination_lock_serializes_different_sources(rar_import):
    source, plex, calls = rar_import
    target = destination(source, plex)
    key = archive.hashlib.sha256(os.fsencode(str(target))).hexdigest()
    state = target.parent / ".xbox-import-state" / key
    with archive.destination_lock(state):
        run_main(1)
    assert (state / "lock").exists()
    assert not calls.imports and not calls.extract
    run_main()


@pytest.mark.parametrize("mutation", ["symlink", "permissions", "schema", "path"])
def test_untrusted_receipt_fails_closed(rar_import, mutation):
    source, plex, calls = rar_import
    run_main()
    path = receipt_path(plex)
    if mutation == "symlink":
        path.unlink()
        path.symlink_to(source / "a.rar")
    elif mutation == "permissions":
        path.chmod(0o644)
    else:
        value = json.loads(path.read_text())
        if mutation == "schema":
            value["version"] = 999
        else:
            value["jobs"]["Movie.mkv"]["artifacts"][0]["name"] = "../victim.mkv"
        path.write_text(json.dumps(value))
    run_main(1)
    assert len(calls.imports) == 1


def test_empty_archive_alongside_loose_media_fails_without_extraction(rar_import):
    source, _, calls = rar_import
    calls.contents["a.rar"] = {"note.txt": b"keep"}
    (source / "loose.mkv").write_bytes(b"loose")
    run_main(1)
    assert not calls.extract and not calls.imports
    assert (source / "loose.mkv").read_bytes() == b"loose"


def test_dry_run_has_no_mutations_probes_logs_or_subprocesses(rar_import, monkeypatch):
    source, plex, calls = rar_import
    (source / "Movie.mkv").write_bytes(b"media")
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])
    monkeypatch.setattr(archive.subprocess, "run", lambda *a, **k: pytest.fail("subprocess"))
    monkeypatch.setattr(cli, "probe_file", lambda *a: pytest.fail("probe"))
    monkeypatch.setattr(cli, "write_log_entry", lambda *a, **k: pytest.fail("log"))
    monkeypatch.setattr(archive, "save_receipt", lambda *a: pytest.fail("receipt"))
    monkeypatch.setattr(archive, "destination_lock", lambda *a: pytest.fail("lock"))
    run_main()
    assert not (plex / "movies/.xbox-import-state").exists()
    assert not destination(source, plex).exists()
    assert not calls.extract and not calls.imports and not calls.scans


def test_orphan_continuation_volume_not_ignored(rar_import):
    source, _, calls = rar_import
    (source / "a.rar").unlink()
    (source / "a.part02.rar").write_bytes(b"archive")
    run_main(1)
    assert not calls.extract and not calls.imports


def test_standalone_media_does_not_inspect_neighbor_archives(rar_import, monkeypatch):
    source, plex, calls = rar_import
    loose = source / "Movie.mkv"
    loose.write_bytes(b"media")
    monkeypatch.setattr(sys, "argv", ["xbox-import", str(loose), "--plex", str(plex)])
    monkeypatch.setattr(
        cli, "discover_rar_archives", lambda *a: pytest.fail("neighbor archive inspection")
    )
    run_main()
    assert calls.imports == [loose] and not calls.extract


@pytest.mark.parametrize("collision", ["extension", "archive", "ancestor", "intermediate"])
def test_batch_collision_preflight_blocks_all_tools(rar_import, collision):
    source, _, calls = rar_import
    if collision == "extension":
        (source / "Movie.mp4").write_bytes(b"loose")
    elif collision == "archive":
        (source / "b.rar").write_bytes(b"other archive")
        calls.contents["b.rar"] = {"Movie.mkv": b"other"}
    elif collision == "ancestor":
        calls.contents["a.rar"]["Movie.mkv/episode.mkv"] = b"other"
    else:
        calls.contents["a.rar"]["Movie.importing.mkv"] = b"other"
    run_main(1)
    assert not calls.extract and not calls.imports


def test_all_sets_extracted_before_any_import(rar_import, monkeypatch):
    source, _, calls = rar_import
    (source / "b.rar").write_bytes(b"other archive")
    calls.contents["b.rar"] = {"Other.mkv": b"other"}
    importer = cli.import_file

    def after_extraction(*args, **kwargs):
        assert len(calls.extract) == 2
        return importer(*args, **kwargs)

    monkeypatch.setattr(cli, "import_file", after_extraction)
    run_main()
    assert len(calls.imports) == 2


def test_later_extraction_failure_prevents_all_imports(rar_import):
    source, _, calls = rar_import
    (source / "b.rar").write_bytes(b"other archive")
    calls.contents["b.rar"] = {"Other.mkv": b"other"}

    def fail_second(stage):
        if len(calls.extract) == 2:
            raise OSError("CRC failure")

    calls.failure = fail_second
    run_main(1)
    assert not calls.imports and not calls.scans
    assert calls.extract[0].exists()


@pytest.mark.parametrize("failure", ["probe", "incompatible", "import", "ownership", "validation"])
def test_processing_failures_retain_stage_without_publication(rar_import, monkeypatch, failure):
    source, plex, calls = rar_import
    if failure in {"probe", "incompatible"}:
        monkeypatch.setattr(
            cli,
            "probe_file",
            lambda path: MediaInfo(
                path=path,
                probe_error="probe failed" if failure == "probe" else None,
                incompatible_reason="incompatible" if failure == "incompatible" else None,
            ),
        )
    elif failure == "import":
        monkeypatch.setattr(
            cli, "import_file", lambda *a, **k: {"status": "failed", "error": "failed"}
        )
    elif failure == "ownership":
        monkeypatch.setattr(archive, "set_ownership", lambda *a: (False, "permission denied"))
    else:
        calls.dovi = True
        monkeypatch.setattr(archive, "validate_output", lambda *a: (False, "invalid output"))
    run_main(1)
    assert not destination(source, plex).exists() and not calls.scans
    assert calls.extract[0].exists()
    assert (source / "a.rar").read_bytes() == b"archive"


def test_unlink_cleanup_failure_retries_without_import(rar_import, monkeypatch):
    _, _, calls = rar_import
    unlink = archive.os.unlink

    def denied(path, *args, **kwargs):
        if Path(path).name == "Movie.mkv":
            raise OSError("unlink denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(archive.os, "unlink", denied)
    run_main(1)
    monkeypatch.setattr(archive.os, "unlink", unlink)
    run_main()
    assert len(calls.imports) == len(calls.extract) == 1


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_invalid_archive_source_never_extracted(rar_import, kind):
    source, _, calls = rar_import
    path = source / "a.rar"
    path.unlink()
    if kind == "symlink":
        original = source / "original.bin"
        original.write_bytes(b"archive")
        path.symlink_to(original)
    else:
        path.mkdir()
    run_main(1)
    assert not calls.extract and not calls.imports


@pytest.mark.parametrize("name", ["a.part1.rar", "a.PART01.RAR"])
def test_multipart_first_volume_names_and_continuations(rar_import, name):
    source, _, calls = rar_import
    (source / "a.rar").unlink()
    first = source / name
    second = source / "a.part02.rar"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    calls.contents = {name: {"Movie.mkv": b"media"}}
    calls.volumes = [str(first), str(second)]
    run_main()
    assert len(calls.extract) == 1


def test_old_style_orphan_volume_fails_without_receipt(rar_import):
    source, _, calls = rar_import
    (source / "a.rar").unlink()
    (source / "a.r00").write_bytes(b"continuation")
    (source / "Movie.mkv").write_bytes(b"media")
    run_main(1)
    assert not calls.extract and not calls.imports


def test_unknown_orphan_stage_is_not_scanned_or_deleted(rar_import):
    source, plex, _ = rar_import
    unknown = plex / "movies/.xbox-import-unowned"
    unknown.mkdir()
    (unknown / "Movie.mkv").write_bytes(b"do not touch")
    run_main()
    assert (unknown / "Movie.mkv").read_bytes() == b"do not touch"
    assert (source / "a.rar").exists()


def test_missing_crc_with_blake2_metadata_is_not_substituted(rar_import):
    source, _, calls = rar_import
    item = calls.make_info("Movie.mkv", b"media", source / "a.rar")
    item.CRC = None
    item.blake2sp_hash = b"x" * 32
    calls.infos = [item]
    run_main(1)
    assert not calls.extract and not calls.imports


def test_state_symlink_is_never_followed(rar_import):
    source, plex, calls = rar_import
    state = plex / "movies/.xbox-import-state"
    state.symlink_to(source, target_is_directory=True)
    run_main(1)
    assert not calls.extract and not calls.imports
    assert sorted(path.name for path in source.iterdir()) == ["a.rar"]


@pytest.mark.parametrize("failure", ["interrupt", "oserror"])
def test_dovi_backup_stage_checkpoint_survives_copy_failure(rar_import, monkeypatch, failure):
    source, plex, calls = rar_import
    calls.dovi = True
    copy = archive.shutil.copy2

    def fail_copy(*args, **kwargs):
        # create_work has saved its ownership checkpoint, but no backup copy or
        # PREPARED receipt exists. A working job must still have NO artifacts.
        path = receipt_path(plex)
        receipt = json.loads(path.read_text())
        job = receipt["jobs"]["Movie.mkv"]
        assert receipt["work"]["backup"] is not None
        assert job["phase"] == "working"
        assert job["artifacts"] == []
        archive.load_receipt(
            path.parent,
            receipt["manifest"],
            receipt["manifest"]["inventory"]["members"],
        )
        if failure == "interrupt":
            raise KeyboardInterrupt()
        raise OSError("backup copy interrupted after ownership checkpoint")

    monkeypatch.setattr(archive.shutil, "copy2", fail_copy)
    run_main(130 if failure == "interrupt" else 1)
    assert not destination(source, plex).exists()
    assert not calls.scans
    receipt = json.loads(receipt_path(plex).read_text())
    assert receipt["jobs"]["Movie.mkv"]["artifacts"] == []
    monkeypatch.setattr(archive.shutil, "copy2", copy)
    run_main()
    assert len(calls.extract) == 1
    assert len(calls.imports) == 2  # WORKING output was never prepared for reuse.
    backup = plex / "backup/movies" / source.name / "Movie.DV.mkv"
    assert backup.read_bytes() == b"media"
    assert (destination(source, plex) / "Movie.mkv").read_bytes() == b"HDR10 output"
    run_main()
    assert len(calls.imports) == 2
    assert len(calls.scans) == 1


def test_archive_and_loose_samples_use_canonical_filter(rar_import):
    source, plex, calls = rar_import
    calls.contents["a.rar"] = {
        "Movie.mkv": b"media",
        "Movie.Sample.mkv": b"archive sample",
        "Sample/clip.mkv": b"archive directory sample",
    }
    (source / "Movie.mkv").write_bytes(b"media")
    (source / "loose.sample.mkv").write_bytes(b"loose sample")
    sample_dir = source / "Sample"
    sample_dir.mkdir()
    sample = sample_dir / "clip.mkv"
    sample.write_bytes(b"source sample")
    run_main()
    assert calls.imports == [source / "Movie.mkv"]
    assert not calls.extract
    assert sorted(path.name for path in destination(source, plex).iterdir()) == ["Movie.mkv"]
    receipt = json.loads(receipt_path(plex).read_text())
    assert set(receipt["manifest"]["inventory"]["members"]) == {"Movie.mkv"}
    assert set(receipt["manifest"]["inventory"]["loose"]) == {"Movie.mkv"}
    assert sample.read_bytes() == b"source sample"
    assert (source / "loose.sample.mkv").read_bytes() == b"loose sample"


def test_ignored_sample_still_gets_archive_metadata_safety_checks(rar_import):
    source, _, calls = rar_import
    main = calls.make_info("Movie.mkv", b"media", source / "a.rar")
    sample = calls.make_info("Movie.Sample.mkv", b"sample", source / "a.rar")
    sample.file_redir = (4, 0, "../outside")
    calls.infos = [main, sample]
    run_main(1)
    assert not calls.extract and not calls.imports


def test_ignored_loose_sample_symlink_is_not_followed(rar_import):
    source, _, calls = rar_import
    (source / "Movie.mkv").write_bytes(b"media")
    (source / "loose.sample.mkv").symlink_to(source / "a.rar")
    run_main(1)
    assert not calls.extract and not calls.imports


def test_ownership_eperm_fails_before_publication_and_can_retry(rar_import, monkeypatch):
    source, plex, calls = rar_import
    (source / "Movie.mkv").write_bytes(b"media")
    ownership = archive.set_ownership
    monkeypatch.setattr(archive, "set_ownership", lambda *args: (False, "EPERM"))
    link = archive.os.link
    monkeypatch.setattr(
        archive.os,
        "link",
        lambda *args, **kwargs: pytest.fail("published despite ownership failure"),
    )
    run_main(1)
    assert not calls.extract
    assert not destination(source, plex).exists()
    assert not calls.scans
    receipt = json.loads(receipt_path(plex).read_text())
    assert receipt["jobs"]["Movie.mkv"]["phase"] == "working"
    assert receipt["jobs"]["Movie.mkv"]["artifacts"] == []
    monkeypatch.setattr(archive, "set_ownership", ownership)
    monkeypatch.setattr(archive.os, "link", link)
    run_main()
    assert len(calls.imports) == 2
    assert (source / "Movie.mkv").read_bytes() == b"media"


def test_extractor_nonzero_status_is_fatal_without_fallback(rar_import, monkeypatch):
    _, _, calls = rar_import
    run = archive.subprocess.run

    def unsupported_switch(argv, **kwargs):
        result = run(argv, **kwargs)
        if "x" in argv:
            result.returncode = 7
            result.stderr = "Unsupported switch -ol-"
        return result

    monkeypatch.setattr(archive.subprocess, "run", unsupported_switch)
    run_main(1)
    assert len(calls.extract) == 1
    assert not calls.imports and not calls.scans


def test_archive_source_destination_overlap_fails_before_extraction(rar_import, monkeypatch):
    source, _, calls = rar_import
    monkeypatch.setattr(
        sys,
        "argv",
        ["xbox-import", str(source), "--plex", str(source), "--library", "movies"],
    )
    run_main(1)
    assert not calls.extract and not calls.imports


def test_different_source_cannot_reuse_destination_receipt(rar_import):
    source, _, calls = rar_import
    run_main()
    # Simulate a new source at the same canonical path; source content, not the
    # common directory basename or existing output length, determines identity.
    (source / "a.rar").write_bytes(b"changed")
    run_main(1)
    assert len(calls.imports) == 1


def test_batch_cleanup_waits_for_all_members_then_skips_completed_on_retry(rar_import, monkeypatch):
    _, plex, calls = rar_import
    calls.contents["a.rar"] = {"A.mkv": b"first", "Z.mkv": b"second"}
    importer = cli.import_file

    def fail_second(info, *args, **kwargs):
        if info.path.name == "Z.mkv":
            return {"status": "failed", "error": "second member failed"}
        return importer(info, *args, **kwargs)

    monkeypatch.setattr(cli, "import_file", fail_second)
    run_main(1)
    path = receipt_path(plex)
    receipt = json.loads(path.read_text())
    assert receipt["jobs"]["A.mkv"]["phase"] == "complete"
    assert (calls.extract[0] / "A.mkv").read_bytes() == b"first"
    assert not calls.scans
    monkeypatch.setattr(cli, "import_file", importer)
    run_main()
    assert [item.name for item in calls.imports] == ["A.mkv", "Z.mkv"]
    assert len(calls.extract) == 1
    assert not calls.extract[0].exists()


@pytest.mark.parametrize("checkpoint", ["extraction", "import", "prepared"])
def test_inflight_source_changes_prevent_publication(rar_import, monkeypatch, checkpoint):
    source, plex, calls = rar_import
    changed = source / "a.rar"
    if checkpoint == "extraction":

        def change_after_extraction(stage):
            changed.write_bytes(b"changed")

        calls.failure = change_after_extraction
    elif checkpoint == "import":
        changed = source / "Movie.mkv"
        changed.write_bytes(b"media")
        importer = cli.import_file

        def change_during_import(*args, **kwargs):
            result = importer(*args, **kwargs)
            changed.write_bytes(b"alter")
            return result

        monkeypatch.setattr(cli, "import_file", change_during_import)
    else:
        save = archive.save_receipt

        def change_after_prepared(state, receipt):
            save(state, receipt)
            if any(job["phase"] == "prepared" for job in receipt["jobs"].values()):
                changed.write_bytes(b"changed")

        monkeypatch.setattr(archive, "save_receipt", change_after_prepared)
    run_main(1)
    assert not destination(source, plex).exists()
    assert not calls.scans
    assert changed.read_bytes() == (b"alter" if checkpoint == "import" else b"changed")
    state = receipt_path(plex)
    assert state.exists()
    assert list(state.parent.glob("work-*"))


def test_header_volume_outside_source_is_rejected(rar_import):
    source, plex, calls = rar_import
    outside = source.parent / "outside.rar"
    outside.write_bytes(b"archive")
    calls.infos = [calls.make_info("Movie.mkv", b"media", outside)]
    run_main(1)
    assert not calls.extract and not calls.imports and not calls.scans
    assert not destination(source, plex).exists()
    assert outside.read_bytes() == b"archive"


@pytest.mark.parametrize("mode", ["fresh", "complete", "scan-retry", "dry-run"])
def test_one_inventory_per_invocation(rar_import, monkeypatch, mode):
    _, _, calls = rar_import
    argv = sys.argv[:]
    if mode in {"complete", "scan-retry"}:
        if mode == "scan-retry":
            monkeypatch.setattr(sys, "argv", [*argv, "--no-plex-scan"])
        run_main()
        monkeypatch.setattr(sys, "argv", argv)
    if mode == "dry-run":
        monkeypatch.setattr(sys, "argv", [*argv, "--dry-run"])
    inventory = archive.inventory
    count = 0

    def counted(*args, **kwargs):
        nonlocal count
        count += 1
        return inventory(*args, **kwargs)

    monkeypatch.setattr(archive, "inventory", counted)
    run_main()
    assert count == 1
    assert len(calls.imports) == (0 if mode == "dry-run" else 1)


def test_completed_retry_hashes_each_input_and_artifact_once(rar_import, monkeypatch):
    source, plex, calls = rar_import
    loose = source / "Movie.mkv"
    loose.write_bytes(b"media")
    calls.subtitle = calls.dovi = True
    run_main()
    digest = archive.digest
    reads = []

    def counted(path):
        reads.append(path)
        return digest(path)

    monkeypatch.setattr(archive, "digest", counted)
    monkeypatch.setattr(cli, "probe_file", lambda *a: pytest.fail("probe on completed retry"))
    monkeypatch.setattr(cli, "import_file", lambda *a, **k: pytest.fail("reimport"))
    expected = {
        source / "a.rar",
        loose,
        destination(source, plex) / "Movie.mkv",
        destination(source, plex) / "Movie.en.srt",
        plex / "backup/movies" / source.name / "Movie.DV.mkv",
    }
    for _ in range(2):
        reads.clear()
        run_main()
        assert set(reads) == expected
        assert len(reads) == len(expected)
    assert len(calls.imports) == 1 and not calls.extract


def test_next_invocation_rehashes_even_when_identity_is_mocked_unchanged(rar_import, monkeypatch):
    source, _, calls = rar_import
    run_main()
    path = source / "a.rar"
    baseline = archive.file_identity(path.stat())
    identity = archive.file_identity
    path.write_bytes(b"changed")

    def frozen(value):
        if (value.st_dev, value.st_ino) == baseline[:2]:
            return baseline
        return identity(value)

    monkeypatch.setattr(archive, "file_identity", frozen)
    run_main(1)
    assert len(calls.imports) == len(calls.scans) == 1


@pytest.mark.parametrize("suffix", [".mkv", ".importing.mkv", ".en.srt"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_unreceipted_metadata_preflight_performs_no_content_hashes(
    rar_import, monkeypatch, suffix, dry_run
):
    source, plex, calls = rar_import
    target = destination(source, plex)
    target.mkdir()
    conflict = target / ("Movie" + suffix)
    conflict.write_bytes(b"preserve")
    monkeypatch.setattr(archive, "digest", lambda *a: pytest.fail("hash before cheap conflict"))
    if dry_run:
        monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])
    run_main(1)
    assert conflict.read_bytes() == b"preserve"
    assert not calls.extract and not calls.imports and not calls.scans


def test_progress_is_flushed_before_first_hash(rar_import, monkeypatch):
    import builtins

    real_print = builtins.print
    digest = archive.digest
    messages = []

    def printed(*args, **kwargs):
        if args and isinstance(args[0], str) and args[0].startswith("["):
            assert kwargs.get("flush") is True
            messages.append(args[0])
        return real_print(*args, **kwargs)

    def hashed(path):
        assert any(message.startswith("[inventory]") for message in messages)
        return digest(path)

    monkeypatch.setattr(builtins, "print", printed)
    monkeypatch.setattr(archive, "digest", hashed)
    run_main()
    for stage in ("inventory", "verification", "processing", "publication", "cleanup", "scan"):
        assert any(message.startswith(f"[{stage}]") for message in messages)


def test_digest_reports_throttled_byte_progress(tmp_path, monkeypatch):
    path = tmp_path / "large.mkv"
    path.write_bytes(b"x" * (4 * 1024 * 1024))
    messages = []
    ticks = iter([0, 0.2, 0.4, 1.1, 1.2, 1.3])
    monkeypatch.setattr(archive.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(archive, "progress", messages.append)
    value = archive.digest(path)
    assert value["size"] == 4 * 1024 * 1024
    assert len(messages) == 3  # Start, one timed update, verified completion.
    assert str(path) in messages[1] and "3145728/4194304 bytes" in messages[1]
    assert "MiB/s" in messages[1] and "Verified" in messages[-1]


@pytest.mark.parametrize("checkpoint", ["extraction", "import", "prepared", "complete"])
@pytest.mark.parametrize("mutation", ["inode", "mtime_ns", "ctime_ns", "names", "symlink", "fifo"])
def test_identity_and_tree_changes_fail_at_existing_checkpoints(
    rar_import, monkeypatch, checkpoint, mutation
):
    source, plex, calls = rar_import
    changed = source / "a.rar"

    def mutate():
        if mutation == "inode":
            replacement = source / "replacement"
            replacement.write_bytes(changed.read_bytes())
            replacement.replace(changed)
        elif mutation == "mtime_ns":
            value = changed.stat()
            os.utime(changed, ns=(value.st_atime_ns, value.st_mtime_ns + 1_000_000))
        elif mutation == "ctime_ns":
            # Freeze every identity field except ctime, without changing payload bytes.
            identity = archive.file_identity
            original = identity(changed.stat())

            def changed_ctime(value):
                result = identity(value)
                return (*result[:4], original[4] + 1) if result[:2] == original[:2] else result

            monkeypatch.setattr(archive, "file_identity", changed_ctime)
        elif mutation == "names":
            (source / "new.mkv").write_bytes(b"new")
        elif mutation == "symlink":
            (source / "unsafe.sample.mkv").symlink_to(changed)
        else:
            os.mkfifo(source / "unsafe")

    if checkpoint == "extraction":
        calls.failure = lambda stage: mutate()
    elif checkpoint == "import":
        importer = cli.import_file

        def after_import(*args, **kwargs):
            result = importer(*args, **kwargs)
            mutate()
            return result

        monkeypatch.setattr(cli, "import_file", after_import)
    else:
        save = archive.save_receipt

        def after_save(state, receipt):
            save(state, receipt)
            if any(job["phase"] == checkpoint for job in receipt["jobs"].values()):
                mutate()

        monkeypatch.setattr(archive, "save_receipt", after_save)
    run_main(1)
    assert not calls.scans
    if checkpoint != "complete":
        assert not destination(source, plex).exists()
    else:
        assert (destination(source, plex) / "Movie.mkv").read_bytes() == b"media"


def test_fresh_post_import_hash_catches_content_change_with_mocked_identity(
    rar_import, monkeypatch
):
    source, plex, calls = rar_import
    loose = source / "Movie.mkv"
    loose.write_bytes(b"media")
    importer = cli.import_file
    identity = archive.file_identity
    original = identity(loose.stat())

    def frozen(value):
        result = identity(value)
        return original if result[:2] == original[:2] else result

    def mutate(*args, **kwargs):
        result = importer(*args, **kwargs)
        loose.write_bytes(b"alter")
        return result

    monkeypatch.setattr(archive, "file_identity", frozen)
    monkeypatch.setattr(cli, "import_file", mutate)
    run_main(1)
    assert len(calls.imports) == 1 and not calls.scans
    assert not destination(source, plex).exists()


def test_digest_cache_checks_identity_before_and_after_hash(tmp_path, monkeypatch):
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"media")
    verification = archive.Verification(tmp_path)
    digest = archive.digest

    def changed_after_hash(path):
        value = digest(path)
        old = path.stat()
        os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
        return value

    monkeypatch.setattr(archive, "digest", changed_after_hash)
    with pytest.raises(RuntimeError, match="changed"):
        verification.read(path)
    assert not verification.digests


@pytest.mark.parametrize("dry_run", [False, True])
def test_inventory_digest_reuse_retains_only_targeted_fresh_input_check(
    rar_import, monkeypatch, dry_run
):
    source, _, _ = rar_import
    loose = source / "Movie.mkv"
    loose.write_bytes(b"media")
    reads = []
    digest = archive.digest

    def counted(path):
        reads.append(path)
        return digest(path)

    monkeypatch.setattr(archive, "digest", counted)
    if dry_run:
        monkeypatch.setattr(sys, "argv", [*sys.argv, "--dry-run"])
    run_main()
    assert reads.count(source / "a.rar") == 1
    # Inventory supplies loose matching and the pre-processing source hash.
    # A real import still freshly hashes its input after processing.
    assert reads.count(loose) == (1 if dry_run else 2)


def test_cached_digest_rejects_changed_identity_without_rehash(tmp_path, monkeypatch):
    path = tmp_path / "movie.mkv"
    path.write_bytes(b"media")
    verification = archive.Verification(tmp_path)
    verification.read(path)
    old = path.stat()
    os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 1_000_000))
    monkeypatch.setattr(archive, "digest", lambda *a: pytest.fail("silently rebased digest"))
    with pytest.raises(RuntimeError, match="changed"):
        verification.read(path)


@pytest.fixture
def source_verification_io(rar_import, monkeypatch):
    """Count real verifier descriptors/bytes, not calls to digest or inventory."""
    source, _, _ = rar_import
    opened = archive.os.open
    fdopen = archive.os.fdopen
    descriptors = {}
    counts = SimpleNamespace(opens=[], bytes={}, artifacts=set())

    def tracked_open(path, flags, *args, **kwargs):
        fd = opened(path, flags, *args, **kwargs)
        candidate = Path(os.fsdecode(path))
        if (
            source in candidate.parents or candidate in counts.artifacts
        ) and flags & os.O_ACCMODE == os.O_RDONLY:
            descriptors[fd] = candidate
            counts.opens.append(candidate)
        return fd

    class Reader:
        def __init__(self, stream, path):
            self.stream = stream
            self.path = path

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            block = self.stream.read(size)
            counts.bytes[self.path] = counts.bytes.get(self.path, 0) + len(block)
            return block

    def tracked_fdopen(fd, *args, **kwargs):
        stream = fdopen(fd, *args, **kwargs)
        path = descriptors.pop(fd, None)
        return Reader(stream, path) if path is not None else stream

    monkeypatch.setattr(archive.os, "open", tracked_open)
    monkeypatch.setattr(archive.os, "fdopen", tracked_fdopen)
    return counts


@pytest.mark.parametrize("mode", ["fresh", "dry-run", "complete", "scan-retry"])
def test_actual_source_verification_opens_and_bytes(
    rar_import, source_verification_io, monkeypatch, mode
):
    source, _, _ = rar_import
    loose = source / "Movie.mkv"
    loose.write_bytes(b"media")
    counts = source_verification_io
    argv = sys.argv[:]
    if mode in {"complete", "scan-retry"}:
        if mode == "scan-retry":
            monkeypatch.setattr(sys, "argv", [*argv, "--no-plex-scan"])
        run_main()
        monkeypatch.setattr(sys, "argv", argv)
    if mode == "dry-run":
        monkeypatch.setattr(sys, "argv", [*argv, "--dry-run"])
    for _ in range(2 if mode == "complete" else 1):
        counts.opens.clear()
        counts.bytes.clear()
        run_main()
        loose_reads = 2 if mode == "fresh" else 1
        assert counts.opens.count(source / "a.rar") == 1
        assert counts.opens.count(loose) == loose_reads
        assert len(counts.opens) == 1 + loose_reads
        assert counts.bytes == {source / "a.rar": 7, loose: 5 * loose_reads}


@pytest.mark.parametrize("phase", ["prepared", "complete"])
@pytest.mark.parametrize("corruption", ["schema", "manifest", "artifact"])
def test_unverified_receipt_cannot_authorize_actions(
    rar_import, monkeypatch, capsys, phase, corruption
):
    _, plex, calls = rar_import
    if phase == "prepared":

        def interrupted(*args):
            raise OSError("stop before publication")

        monkeypatch.setattr(archive, "publish", interrupted)
        run_main(1)
    else:
        run_main()
    capsys.readouterr()
    path = receipt_path(plex)
    receipt = json.loads(path.read_text())
    assert receipt["jobs"]["Movie.mkv"]["phase"] == phase
    if corruption == "schema":
        receipt["version"] = 999
    elif corruption == "manifest":
        receipt["manifest"]["hardware"] = not receipt["manifest"]["hardware"]
    else:
        receipt["jobs"]["Movie.mkv"]["artifacts"][0]["sha256"] = "not-a-digest"
    path.write_text(json.dumps(receipt))
    before = path.read_bytes()
    stages = sorted(path.parent.glob("work-*"))
    import_count, scan_count = len(calls.imports), len(calls.scans)

    def forbidden(*args, **kwargs):
        pytest.fail("unverified receipt authorized an action")

    monkeypatch.setattr(archive, "publish", forbidden)
    monkeypatch.setattr(archive.shutil, "rmtree", forbidden)
    monkeypatch.setattr(cli, "probe_file", forbidden)
    monkeypatch.setattr(cli, "import_file", forbidden)
    monkeypatch.setattr(cli, "trigger_plex_scan", forbidden)
    run_main(1)
    assert "Already verified" not in capsys.readouterr().out
    assert path.read_bytes() == before
    assert sorted(path.parent.glob("work-*")) == stages
    assert len(calls.imports) == import_count and len(calls.scans) == scan_count


def test_completed_retries_freshly_read_source_and_artifact_bytes_once(
    rar_import, source_verification_io, monkeypatch
):
    source, plex, calls = rar_import
    loose = source / "Movie.mkv"
    loose.write_bytes(b"media")
    calls.subtitle = calls.dovi = True
    run_main()
    counts = source_verification_io
    counts.artifacts.update(
        {
            destination(source, plex) / "Movie.mkv",
            destination(source, plex) / "Movie.en.srt",
            plex / "backup/movies" / source.name / "Movie.DV.mkv",
        }
    )
    inputs = {source / "a.rar", loose}
    expected = inputs | counts.artifacts
    identities = {path: archive.file_identity(path.stat()) for path in expected}
    sizes = {path: path.stat().st_size for path in expected}

    def forbidden(*args, **kwargs):
        pytest.fail("completed retry invoked processing")

    monkeypatch.setattr(cli, "probe_file", forbidden)
    monkeypatch.setattr(cli, "import_file", forbidden)
    for _ in range(2):
        counts.opens.clear()
        counts.bytes.clear()
        run_main()
        assert set(counts.opens) == expected
        assert len(counts.opens) == len(expected)
        assert counts.bytes == sizes
        assert {path: archive.file_identity(path.stat()) for path in expected} == identities
    assert len(calls.imports) == 1
    assert not calls.extract
