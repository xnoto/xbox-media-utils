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
        extract=[], imports=[], scans=[], contents={"a.rar": {"Movie.mkv": b"media"}},
        banner="UNRAR 7.10 freeware Copyright (c) 1993-2025 Alexander Roshal",
        infos=None, volumes=None, failure=None, subtitle=False, dovi=False,
    )
    monkeypatch.setattr(sys, "argv", ["xbox-import", str(source), "--plex", str(plex)])
    monkeypatch.setattr(archive.shutil, "which", lambda name: "/usr/bin/unrar")
    monkeypatch.setattr(archive, "set_ownership", lambda *args: (True, None))
    monkeypatch.setattr(archive, "validate_output", lambda *args: (True, "OK"))
    monkeypatch.setattr(cli, "probe_file", lambda path: MediaInfo(path=path, has_dovi_profile_8=calls.dovi))
    monkeypatch.setattr(cli, "write_log_entry", lambda *args, **kwargs: None)

    def info(name, data, path):
        return SimpleNamespace(
            filename=name, file_size=len(data), CRC=zlib.crc32(data),
            host_os=archive.rarfile.RAR_OS_UNIX, mode=0o100644,
            flags=0, file_redir=None, type=archive.rarfile.RAR_BLOCK_FILE,
            data_offset=0, add_size=0, volume_file=str(path),
            is_dir=lambda: False, is_file=lambda: True,
            is_symlink=lambda: False, needs_password=lambda: False,
        )

    def metadata(path, *, errors, info_callback):
        assert errors == "strict"
        path = Path(path)
        infos = calls.infos if calls.infos is not None else [
            info(name, data, path) for name, data in calls.contents[path.name].items()
        ]
        for item in infos:
            info_callback(item)
        return SimpleNamespace(
            __enter__=None, infos=infos,
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
            original = kwargs["dovi_backup_root"] / target.relative_to(plex_root).with_suffix(".DV.mkv")
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
    assert loose.read_bytes() == data if data is not None else not loose.exists()
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


@pytest.mark.parametrize("suffix", [".mkv", ".importing.mkv", ".HDR10.mkv", ".HDR10.tmp.mkv", ".en.srt", ".en.2.forced.ass", ".en.sdh.sup"])
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
        "archive": source / "a.rar", "loose": source / "Movie.mkv",
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
    assert (plex / "backup/movies" / source.name / "Season 01/Movie.DV.mkv").read_bytes() == b"media"
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
        monkeypatch.setattr(archive.shutil, "rmtree", lambda *a, **k: (_ for _ in ()).throw(OSError("cleanup denied")))
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


@pytest.mark.parametrize("banner", ["unrar-free 0.3", "7-Zip 24.09", "UNRAR 7.10 freeware unknown vendor", "UNRAR 6.24 freeware Alexander Roshal"])
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


@pytest.mark.parametrize("feature", ["crc", "encrypted", "redirect", "symlink", "fifo", "duplicate", "ancestor", "traversal", "absolute", "backslash", "unknown-size", "version"])
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
        item.filename = {"traversal": "../Movie.mkv", "absolute": "/Movie.mkv", "backslash": r"a\Movie.mkv"}[feature]
    run_main(1)
    assert not calls.imports and not calls.extract and not calls.scans


@pytest.mark.parametrize("problem", ["missing", "bad-header", "truncated", "unfinished", "bad-chain"])
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
            item.flags = archive.rarfile.RAR_FILE_SPLIT_AFTER if problem == "unfinished" else archive.rarfile.RAR_FILE_SPLIT_BEFORE
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
    monkeypatch.setattr(cli, "discover_rar_archives", lambda *a: pytest.fail("neighbor archive inspection"))
    run_main()
    assert calls.imports == [loose] and not calls.extract
