"""Directory archive state must not be bypassed by removing the archives."""

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from xbox_media_utils.cli import import_ as cli


@pytest.mark.parametrize("phase", ["complete", "working", "prepared"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_missing_archives_refuse_existing_state_before_loose_import(
    tmp_path, monkeypatch, capsys, phase, dry_run
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "movie.mkv").write_bytes(b"loose source")
    plex = tmp_path / "plex"
    destination = plex / "movies" / source.name
    destination.mkdir(parents=True)
    (destination / "movie.mkv").write_bytes(b"changed destination")
    (destination / "movie.importing.mkv").write_bytes(b"legacy partial")
    (destination / "movie.en.srt").write_bytes(b"subtitle")
    key = hashlib.sha256(os.fsencode(str(destination.absolute()))).hexdigest()
    state = destination.parent / ".xbox-import-state" / key
    state.mkdir(parents=True)
    receipt = state / "receipt.json"
    receipt.write_text(json.dumps({"jobs": {"movie.mkv": {"phase": phase}}, "scan": "pending"}))
    before = {
        path: path.read_bytes()
        for path in [source / "movie.mkv", receipt, *destination.iterdir()]
    }
    argv = ["xbox-import", str(source), "--plex", str(plex), "--library", "movies"]
    if dry_run:
        argv.append("--dry-run")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(cli, "import_file", lambda *a, **k: pytest.fail("unsafe loose fallback"))
    monkeypatch.setattr(cli, "probe_file", lambda *a, **k: pytest.fail("unexpected probe"))
    monkeypatch.setattr(cli, "trigger_plex_scan", lambda *a, **k: pytest.fail("unexpected scan"))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
    assert "refusing loose-only fallback" in capsys.readouterr().err
    assert {path: path.read_bytes() for path in before} == before


def test_inaccessible_archive_state_is_not_treated_as_missing(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "movie.mkv").write_bytes(b"source")
    plex = tmp_path / "plex"
    plex.mkdir()
    original = Path.lstat

    def inaccessible(path, *args, **kwargs):
        if ".xbox-import-state" in path.parts:
            raise PermissionError("state inaccessible")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", inaccessible)
    monkeypatch.setattr(
        sys, "argv", ["xbox-import", str(source), "--plex", str(plex), "--library", "movies"]
    )
    monkeypatch.setattr(cli, "import_file", lambda *a, **k: pytest.fail("unsafe fallback"))
    with pytest.raises(SystemExit) as result:
        cli.main()
    assert result.value.code == 1
