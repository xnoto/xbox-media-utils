import json
from pathlib import Path

import pytest

from xbox_media_utils.api import PlexError
from xbox_media_utils.cli import recode
from xbox_media_utils.models import AudioTrack, MediaInfo, SubtitleTrack


class NullLock:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


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

    monkeypatch.setattr(recode, "PlexScanner", FakeScanner)

    assert recode.trigger_plex_scan(target) is expected
    assert "[plex_scan] scan requested" in capsys.readouterr().out


def test_trigger_plex_scan_handles_plex_errors(tmp_path: Path, monkeypatch, capsys):
    class FakeScanner:
        def scan_path(self, path):
            raise PlexError("Plex unavailable")

    monkeypatch.setattr(recode, "PlexScanner", FakeScanner)

    assert recode.trigger_plex_scan(tmp_path) is False
    assert "[plex_scan] Plex unavailable" in capsys.readouterr().err


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

    def fake_remux(input_path, output, **kwargs):
        output_path.write_text("remuxed")
        return True, ""

    def fake_set_ownership(path, user, group):
        ownership_calls.append((Path(path), user, group))
        return True, None

    monkeypatch.setattr(recode, "extract_subtitles", fake_extract_subtitles)
    monkeypatch.setattr("xbox_media_utils.ffmpeg.remux_with_mkvmerge", fake_remux)
    monkeypatch.setattr(recode, "validate_output", lambda info, path: (True, "OK"))
    monkeypatch.setattr(recode, "set_ownership", fake_set_ownership)

    result = recode.process_file(info)

    assert result["status"] == "success"
    assert (subtitle_path, "plex", "libstoragemgmt") in ownership_calls
    assert (media_path, "plex", "libstoragemgmt") in ownership_calls


def test_process_file_refuses_incompatible_format(tmp_path: Path):
    media_path = tmp_path / "movie.mkv"
    media_path.write_text("input")

    info = MediaInfo(
        path=media_path,
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=5,
        incompatible_reason="Dolby Vision Profile 5 cannot be tonemapped (libdovi required)",
    )

    result = recode.process_file(info)

    assert result["status"] == "incompatible"
    assert result["video_action"] == "skip"
    assert "Profile 5" in result["error"]


def test_recover_interrupted_recode_finalizes_validated_output(tmp_path: Path, monkeypatch):
    backup = tmp_path / "movie.mkv.bak"
    output = tmp_path / "movie.xbox.mkv"
    final = tmp_path / "movie.mkv"
    backup.write_text("original")
    output.write_text("complete output")
    monkeypatch.setattr(recode, "probe_file", lambda path: MediaInfo(path=path))
    monkeypatch.setattr(recode, "validate_output", lambda info, path: (True, "OK"))
    monkeypatch.setattr(recode, "set_ownership", lambda *args: (True, None))

    events = recode.recover_interrupted_recodes(tmp_path)

    assert events[0]["status"] == "finalize"
    assert final.read_text() == "complete output"
    assert not backup.exists()
    assert not output.exists()


def test_recover_interrupted_recode_restores_backup_when_output_invalid(
    tmp_path: Path, monkeypatch
):
    backup = tmp_path / "movie.mkv.bak"
    final = tmp_path / "movie.mkv"
    backup.write_text("original")
    final.write_text("invalid output")
    monkeypatch.setattr(recode, "probe_file", lambda path: MediaInfo(path=path))
    monkeypatch.setattr(recode, "validate_output", lambda info, path: (False, "duration mismatch"))
    monkeypatch.setattr(recode, "set_ownership", lambda *args: (True, None))

    events = recode.recover_interrupted_recodes(tmp_path)

    assert events[0]["status"] == "restore"
    assert final.read_text() == "original"
    assert not backup.exists()


def test_recover_interrupted_recode_removes_partial_when_original_exists(tmp_path: Path):
    original = tmp_path / "movie.mkv"
    output = tmp_path / "movie.xbox.mkv"
    original.write_text("original")
    output.write_text("partial")

    events = recode.recover_interrupted_recodes(tmp_path)

    assert events[0]["status"] == "removed_partial"
    assert original.read_text() == "original"
    assert not output.exists()


def test_recover_interrupted_recode_leaves_orphan_for_manual_review(tmp_path: Path):
    output = tmp_path / "movie.xbox.mkv"
    output.write_text("unknown")

    events = recode.recover_interrupted_recodes(tmp_path)

    assert events[0]["status"] == "orphaned"
    assert output.exists()


def test_recover_interrupted_recode_ignores_unrelated_backup_files(tmp_path: Path):
    backup = tmp_path / "settings.json.bak"
    final = tmp_path / "settings.mkv"
    backup.write_text("configuration")
    final.write_text("media")

    events = recode.recover_interrupted_recodes(tmp_path)

    assert events == []
    assert backup.read_text() == "configuration"
    assert final.read_text() == "media"


def test_process_file_organizes_compatible_root_media_and_sidecar(tmp_path: Path, monkeypatch):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    library_root.mkdir(parents=True)
    media_path = library_root / "Movie.2024.mkv"
    subtitle_path = library_root / "Movie.2024.en.srt"
    media_path.write_text("input")
    subtitle_path.write_text("subtitle")
    info = MediaInfo(path=media_path, video_codec="h264")

    monkeypatch.setattr(recode, "set_ownership", lambda path, user, group: (True, None))

    result = recode.process_file(info, library_root=plex_root)

    destination_dir = library_root / "Movie.2024"
    destination = destination_dir / media_path.name
    assert result["status"] == "success"
    assert result["organized_path"] == str(destination)
    assert result["scan_target"] == str(library_root)
    assert destination.read_text() == "input"
    assert (destination_dir / subtitle_path.name).read_text() == "subtitle"
    assert not media_path.exists()


def test_process_file_dry_run_reports_organization_without_moving(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    library_root.mkdir(parents=True)
    media_path = library_root / "Movie.2024.mkv"
    media_path.write_text("input")
    info = MediaInfo(path=media_path, video_codec="h264")

    result = recode.process_file(info, library_root=plex_root, dry_run=True)

    assert result["status"] == "would_process"
    assert result["organization_action"] == f"move into {library_root / 'Movie.2024'}"
    assert media_path.exists()
    assert not (library_root / "Movie.2024").exists()


def test_process_file_dry_run_checks_organization_collisions(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    destination_dir = library_root / "The Show" / "Season 01"
    destination_dir.mkdir(parents=True)
    media_path = library_root / "The.Show.S01E01.mkv"
    destination = destination_dir / media_path.name
    media_path.write_text("source")
    destination.write_text("existing")
    info = MediaInfo(path=media_path, video_codec="h264")

    result = recode.process_file(info, library_root=plex_root, dry_run=True)

    assert result["status"] == "failed"
    assert result["error"] == f"Organization destination already exists: {destination}"
    assert media_path.read_text() == "source"
    assert destination.read_text() == "existing"


def test_process_file_reports_ambiguous_root_tv_name_without_blocking_recode(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    library_root.mkdir(parents=True)
    media_path = library_root / "Unsorted Recording.mp4"
    media_path.write_text("input")
    info = MediaInfo(
        path=media_path,
        video_codec="mpeg4",
        needs_video_recode=True,
        video_recode_reason="incompatible codec: mpeg4",
    )

    result = recode.process_file(info, library_root=plex_root, dry_run=True)

    assert result["status"] == "would_process"
    assert result["organization_action"].startswith("skipped: ambiguous TV filename")
    assert result["organized_path"] is None
    assert media_path.exists()


def test_process_file_does_not_report_ambiguous_tv_skip_for_compatible_file(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    library_root.mkdir(parents=True)
    media_path = library_root / "Unsorted Recording.mkv"
    media_path.write_text("input")
    info = MediaInfo(path=media_path, video_codec="h264")

    result = recode.process_file(info, library_root=plex_root)

    assert result["status"] == "compatible"
    assert result["organization_action"] == "none"
    assert media_path.exists()


def test_process_file_refuses_nested_mp4_to_mkv_collision(tmp_path: Path, monkeypatch):
    movie_dir = tmp_path / "movies" / "Movie"
    movie_dir.mkdir(parents=True)
    media_path = movie_dir / "Movie.mp4"
    existing_mkv = movie_dir / "Movie.mkv"
    media_path.write_text("mp4 source")
    existing_mkv.write_text("distinct mkv")
    info = MediaInfo(
        path=media_path,
        video_codec="mpeg4",
        needs_video_recode=True,
        video_recode_reason="incompatible codec: mpeg4",
    )

    def unexpected_ffmpeg(*args, **kwargs):
        raise AssertionError("FFmpeg must not run when the final path already exists")

    monkeypatch.setattr(recode, "run_ffmpeg_with_fallback", unexpected_ffmpeg)

    result = recode.process_file(info)

    assert result["status"] == "failed"
    assert result["error"] == f"Recode destination already exists: {existing_mkv}"
    assert media_path.read_text() == "mp4 source"
    assert existing_mkv.read_text() == "distinct mkv"


def test_process_file_recodes_from_organized_path(tmp_path: Path, monkeypatch):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    library_root.mkdir(parents=True)
    media_path = library_root / "Movie.2024.mkv"
    media_path.write_text("10-bit input")
    destination_dir = library_root / "Movie.2024"
    destination = destination_dir / media_path.name
    info = MediaInfo(
        path=media_path,
        video_codec="hevc",
        needs_video_recode=True,
        video_recode_reason="10-bit SDR hevc crashes Plex on Xbox",
    )

    def fake_run_ffmpeg_with_fallback(processing_info, output, use_hardware, **kwargs):
        assert processing_info.path == destination
        output.write_text("recode output")
        return True, ""

    monkeypatch.setattr(recode, "run_ffmpeg_with_fallback", fake_run_ffmpeg_with_fallback)
    monkeypatch.setattr(recode, "validate_output", lambda info, path: (True, "OK"))
    monkeypatch.setattr(recode, "set_ownership", lambda path, user, group: (True, None))

    result = recode.process_file(info, library_root=plex_root)

    assert result["status"] == "success"
    assert result["output_path"] == str(destination)
    assert destination.read_text() == "recode output"
    assert not media_path.exists()
    assert not destination.with_suffix(".xbox.mkv").exists()


def test_process_file_does_not_organize_dovi_backup(tmp_path: Path):
    plex_root = tmp_path / "plex"
    backup_root = plex_root / "backup"
    backup_root.mkdir(parents=True)
    media_path = backup_root / "Movie.DV.mkv"
    media_path.write_text("input")
    info = MediaInfo(
        path=media_path,
        video_codec="hevc",
        audio_tracks=[AudioTrack(index=1, codec="dts", channels=6, needs_recode=True)],
    )

    result = recode.process_file(
        info,
        library_root=plex_root,
        process_dovi_backup=True,
        dry_run=True,
    )

    assert result["status"] == "would_process"
    assert result["organization_action"] == "none"
    assert media_path.exists()


def test_write_incompatible_report_lists_only_blocked_files(tmp_path: Path):
    blocked = MediaInfo(
        path=Path("/lib/Show.S01E01.mkv"),
        video_codec="hevc",
        video_bit_depth=10,
        video_hdr=True,
        video_hdr_type="dolby vision",
        dovi_profile=5,
        incompatible_reason="Dolby Vision Profile 5 cannot be tonemapped",
    )
    fine = MediaInfo(path=Path("/lib/Show.S01E02.mkv"), video_codec="hevc")
    output = tmp_path / "incompat.txt"

    count = recode.write_incompatible_report([blocked, fine], output)

    assert count == 1
    contents = output.read_text(encoding="utf-8")
    assert "/lib/Show.S01E01.mkv" in contents
    assert "/lib/Show.S01E02.mkv" not in contents
    assert "DV Profile 5" in contents
    assert "10-bit" in contents
    # Tab-separated: path<tab>details<tab>reason
    line = contents.strip().split("\n")[0]
    assert line.count("\t") == 2


def test_process_file_recodes_audio_from_hdr10_copy_and_archives_dovi(tmp_path: Path, monkeypatch):
    plex_root = tmp_path / "plex"
    movie_dir = plex_root / "movies" / "Movie"
    movie_dir.mkdir(parents=True)
    media_path = movie_dir / "movie.mkv"
    media_path.write_text("dovi")
    hdr10_path = movie_dir / "movie.HDR10.mkv"
    output_path = movie_dir / "movie.xbox.mkv"
    backup_root = plex_root / "backup"

    info = MediaInfo(
        path=media_path,
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        audio_tracks=[
            AudioTrack(
                index=1,
                codec="dts",
                channels=6,
                needs_recode=True,
                recode_reason="incompatible codec: dts -> AAC stereo",
            )
        ],
        needs_video_recode=True,
        video_recode_reason="Dolby Vision Profile 8 is incompatible with Plex on Xbox",
        dovi_profile=8,
        has_dovi_profile_8=True,
    )

    def fake_create_hdr10_copy(info_arg, dest_dir, logger=print, **kwargs):
        hdr10_path.write_text("hdr10")
        return True, "HDR10 copy created", hdr10_path

    def fake_run_ffmpeg_with_fallback(processing_info, output, use_hardware, **kwargs):
        assert processing_info.path == hdr10_path
        assert processing_info.needs_video_recode is False
        assert processing_info.needs_audio_recode is True
        output.write_text("hdr10 with aac")
        return True, ""

    monkeypatch.setattr(recode, "create_hdr10_copy", fake_create_hdr10_copy)
    monkeypatch.setattr(recode, "run_ffmpeg_with_fallback", fake_run_ffmpeg_with_fallback)
    monkeypatch.setattr(recode, "validate_output", lambda info, path: (True, "OK"))
    monkeypatch.setattr(recode, "set_ownership", lambda path, user, group: (True, None))

    result = recode.process_file(
        info,
        dovi_backup_root=backup_root,
        library_root=plex_root,
    )

    archived_path = backup_root / "movies" / "Movie" / "movie.DV.mkv"
    assert result["status"] == "success"
    assert result["output_path"] == str(media_path)
    assert result["archived_dovi_path"] == str(archived_path)
    assert media_path.read_text() == "hdr10 with aac"
    assert archived_path.read_text() == "dovi"
    assert not hdr10_path.exists()
    assert not output_path.exists()


def test_process_file_replaces_plex_copy_when_dovi_backup_already_exists(
    tmp_path: Path, monkeypatch
):
    plex_root = tmp_path / "plex"
    movie_dir = plex_root / "movies" / "Movie"
    movie_dir.mkdir(parents=True)
    media_path = movie_dir / "movie.mkv"
    media_path.write_text("already-backed-up plex copy")
    hdr10_path = movie_dir / "movie.HDR10.mkv"
    output_path = movie_dir / "movie.xbox.mkv"
    backup_root = plex_root / "backup"
    archived_path = backup_root / "movies" / "Movie" / "movie.DV.mkv"
    archived_path.parent.mkdir(parents=True)
    archived_path.write_text("original dovi backup")

    info = MediaInfo(
        path=media_path,
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        audio_tracks=[
            AudioTrack(
                index=1,
                codec="eac3",
                channels=6,
                needs_recode=True,
                recode_reason="eac3 5.1 -> AAC stereo",
            )
        ],
        needs_video_recode=True,
        video_recode_reason="Dolby Vision Profile 8 is incompatible with Plex on Xbox",
        dovi_profile=8,
        has_dovi_profile_8=True,
    )

    def fake_create_hdr10_copy(info_arg, dest_dir, logger=print, **kwargs):
        hdr10_path.write_text("hdr10 sidecar")
        return True, "HDR10 copy already exists", hdr10_path

    def fake_run_ffmpeg_with_fallback(processing_info, output, use_hardware, **kwargs):
        assert processing_info.path == hdr10_path
        output.write_text("processed hdr10 with aac")
        return True, ""

    monkeypatch.setattr(recode, "create_hdr10_copy", fake_create_hdr10_copy)
    monkeypatch.setattr(recode, "run_ffmpeg_with_fallback", fake_run_ffmpeg_with_fallback)
    monkeypatch.setattr(recode, "validate_output", lambda info, path: (True, "OK"))
    monkeypatch.setattr(recode, "set_ownership", lambda path, user, group: (True, None))

    result = recode.process_file(
        info,
        dovi_backup_root=backup_root,
        library_root=plex_root,
    )

    assert result["status"] == "success"
    assert result["output_path"] == str(media_path)
    assert result["archived_dovi_path"] == str(archived_path)
    assert media_path.read_text() == "processed hdr10 with aac"
    assert archived_path.read_text() == "original dovi backup"
    assert not hdr10_path.exists()
    assert not output_path.exists()


def test_process_file_recodes_audio_for_dovi_backup_without_video_recode(
    tmp_path: Path, monkeypatch
):
    media_path = tmp_path / "movie.DV.mkv"
    media_path.write_text("dovi backup")
    output_path = tmp_path / "movie.DV.xbox.mkv"

    info = MediaInfo(
        path=media_path,
        video_codec="hevc",
        video_hdr=True,
        video_hdr_type="dolby vision",
        audio_tracks=[
            AudioTrack(
                index=1,
                codec="dts",
                channels=6,
                needs_recode=True,
                recode_reason="incompatible codec: dts -> AAC stereo",
            )
        ],
        needs_video_recode=True,
        video_recode_reason="Dolby Vision Profile 5 is incompatible with Plex on Xbox",
        dovi_profile=5,
        incompatible_reason="Dolby Vision Profile 5 cannot be tonemapped",
    )

    def fake_run_ffmpeg_with_fallback(processing_info, output, use_hardware, **kwargs):
        assert processing_info.path == media_path
        assert processing_info.needs_video_recode is False
        assert processing_info.incompatible_reason is None
        assert processing_info.needs_audio_recode is True
        output.write_text("dovi backup with aac")
        return True, ""

    monkeypatch.setattr(recode, "run_ffmpeg_with_fallback", fake_run_ffmpeg_with_fallback)
    monkeypatch.setattr(recode, "validate_output", lambda info, path: (True, "OK"))
    monkeypatch.setattr(recode, "set_ownership", lambda path, user, group: (True, None))

    result = recode.process_file(info, process_dovi_backup=True)

    assert result["status"] == "success"
    assert result["video_action"] == "copy: archived DoVi backup"
    assert result["audio_action"] == "recode: incompatible codec: dts -> AAC stereo"
    assert media_path.read_text() == "dovi backup with aac"
    assert not output_path.exists()


def test_scan_dovi_backups_includes_only_dv_mkv_files(tmp_path: Path, monkeypatch):
    backup = tmp_path / "backup"
    backup.mkdir()
    dv_path = backup / "movie.DV.mkv"
    normal_path = backup / "movie.mkv"
    hdr10_path = backup / "movie.HDR10.mkv"
    for path in (dv_path, normal_path, hdr10_path):
        path.write_text("media")

    def fake_probe_file(path):
        return MediaInfo(path=path)

    monkeypatch.setattr(recode, "probe_file", fake_probe_file)

    results = recode.scan_dovi_backups(backup, quiet=True)

    assert [result.path for result in results] == [dv_path]


def test_scan_directory_reports_ambiguous_root_tv_organization_skip(
    tmp_path: Path, monkeypatch, capsys
):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    library_root.mkdir(parents=True)
    media_path = library_root / "Unsorted Recording.mkv"
    media_path.write_text("input")
    monkeypatch.setattr(
        recode,
        "probe_file",
        lambda path: MediaInfo(path=path, video_codec="h264"),
    )

    recode.scan_directory(library_root, library_root=plex_root)

    assert "ORGANIZE-SKIP(ambiguous TV filename)" in capsys.readouterr().out


def configure_process_main(monkeypatch, results: list[MediaInfo]) -> None:
    monkeypatch.setattr(recode, "acquire_lock", lambda path: NullLock())
    monkeypatch.setattr(recode, "scan_directory", lambda path, quiet, library_root=None: results)
    monkeypatch.setattr(recode, "write_log_entry", lambda *args, **kwargs: None)


def test_main_scans_successful_process_target(tmp_path: Path, monkeypatch):
    target = tmp_path / "movies" / "Movie"
    target.mkdir(parents=True)
    info = MediaInfo(path=target / "movie.mkv", needs_video_recode=True)
    configure_process_main(monkeypatch, [info])
    monkeypatch.setattr(
        recode,
        "process_file",
        lambda *args, **kwargs: {"status": "success", "error": None},
    )

    scan_calls = []

    def fake_trigger_plex_scan(path, quiet=False):
        scan_calls.append(path)
        return True

    monkeypatch.setattr(recode, "trigger_plex_scan", fake_trigger_plex_scan)
    monkeypatch.setattr(
        "sys.argv",
        ["xbox-recode", "process", str(target), "--plex", str(tmp_path)],
    )

    recode.main()

    assert scan_calls == [target]


def test_main_does_not_select_compatible_ambiguous_root_tv_file(
    tmp_path: Path, monkeypatch, capsys
):
    plex_root = tmp_path / "plex"
    target = plex_root / "tv"
    target.mkdir(parents=True)
    ambiguous = MediaInfo(path=target / "Unsorted Recording.mkv", video_codec="h264")
    configure_process_main(monkeypatch, [ambiguous])

    def unexpected_process(*args, **kwargs):
        raise AssertionError("compatible ambiguous TV file must not be selected")

    def unexpected_scan(*args, **kwargs):
        raise AssertionError("no processing means no Plex scan")

    monkeypatch.setattr(recode, "process_file", unexpected_process)
    monkeypatch.setattr(recode, "trigger_plex_scan", unexpected_scan)
    monkeypatch.setattr(
        "sys.argv",
        ["xbox-recode", "process", str(target), "--plex", str(plex_root)],
    )

    recode.main()

    assert "No files need processing." in capsys.readouterr().out


def test_main_ambiguous_root_tv_file_does_not_suppress_successful_scan(tmp_path: Path, monkeypatch):
    plex_root = tmp_path / "plex"
    target = plex_root / "tv"
    target.mkdir(parents=True)
    ambiguous = MediaInfo(path=target / "Unsorted Recording.mkv", video_codec="h264")
    needed = MediaInfo(
        path=target / "The Show" / "Season 01" / "The.Show.S01E01.mkv",
        needs_video_recode=True,
    )
    configure_process_main(monkeypatch, [ambiguous, needed])
    process_calls = []

    def fake_process_file(info, *args, **kwargs):
        process_calls.append(info.path)
        return {"status": "success", "error": None, "scan_target": str(info.path.parent)}

    monkeypatch.setattr(recode, "process_file", fake_process_file)
    scan_calls = []

    def fake_trigger_plex_scan(path, quiet=False):
        scan_calls.append(path)
        return True

    monkeypatch.setattr(recode, "trigger_plex_scan", fake_trigger_plex_scan)
    monkeypatch.setattr(
        "sys.argv",
        ["xbox-recode", "process", str(target), "--plex", str(plex_root)],
    )

    recode.main()

    assert process_calls == [needed.path]
    assert scan_calls == [target]


def test_main_scans_parent_for_successful_file_and_propagates_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "movies" / "Movie" / "movie.mkv"
    target.parent.mkdir(parents=True)
    target.write_text("input")
    info = MediaInfo(path=target, needs_video_recode=True)
    monkeypatch.setattr(recode, "acquire_lock", lambda path: NullLock())
    monkeypatch.setattr(recode, "probe_file", lambda path: info)
    monkeypatch.setattr(recode, "write_log_entry", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        recode,
        "process_file",
        lambda *args, **kwargs: {"status": "success", "error": None},
    )

    scan_calls = []

    def fake_trigger_plex_scan(path, quiet=False):
        scan_calls.append(path)
        return False

    monkeypatch.setattr(recode, "trigger_plex_scan", fake_trigger_plex_scan)
    monkeypatch.setattr(
        "sys.argv",
        ["xbox-recode", "process", str(target), "--file", "--plex", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        recode.main()

    assert exc_info.value.code == 1
    assert scan_calls == [target.parent]


def test_main_scans_library_root_for_organized_single_file(tmp_path: Path, monkeypatch):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    library_root.mkdir(parents=True)
    target = library_root / "Movie.2024.mkv"
    target.write_text("input")
    info = MediaInfo(path=target, video_codec="h264")
    destination_dir = library_root / target.stem

    monkeypatch.setattr(recode, "acquire_lock", lambda path: NullLock())
    monkeypatch.setattr(recode, "probe_file", lambda path: info)
    monkeypatch.setattr(recode, "write_log_entry", lambda *args, **kwargs: None)
    process_calls = []

    def fake_process_file(info_arg, *args, **kwargs):
        process_calls.append(info_arg.path)
        return {
            "status": "success",
            "error": None,
            "organization_action": f"move into {destination_dir}",
            "scan_target": str(library_root),
        }

    monkeypatch.setattr(recode, "process_file", fake_process_file)
    scan_calls = []

    def fake_trigger_plex_scan(path, quiet=False):
        scan_calls.append(path)
        return True

    monkeypatch.setattr(recode, "trigger_plex_scan", fake_trigger_plex_scan)
    monkeypatch.setattr(
        "sys.argv",
        ["xbox-recode", "process", str(target), "--file", "--plex", str(plex_root)],
    )

    recode.main()

    assert process_calls == [target]
    assert scan_calls == [library_root]


def test_main_does_not_scan_after_partial_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "movies" / "Movie"
    target.mkdir(parents=True)
    infos = [
        MediaInfo(path=target / "part-1.mkv", needs_video_recode=True),
        MediaInfo(path=target / "part-2.mkv", needs_video_recode=True),
    ]
    configure_process_main(monkeypatch, infos)

    def fake_process_file(info, *args, **kwargs):
        return {
            "status": "success" if info is infos[0] else "failed",
            "error": None if info is infos[0] else "recode failed",
        }

    monkeypatch.setattr(recode, "process_file", fake_process_file)
    monkeypatch.setattr(
        recode,
        "trigger_plex_scan",
        lambda path, quiet=False: pytest.fail(f"unexpected Plex scan for {path}"),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["xbox-recode", "process", str(target), "--plex", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        recode.main()

    assert exc_info.value.code == 1


@pytest.mark.parametrize(
    ("status", "extra_args"),
    [
        ("would_process", ["--dry-run"]),
        ("success", ["--no-plex-scan"]),
        ("incompatible", []),
    ],
)
def test_main_skips_scan_when_process_not_eligible(
    tmp_path: Path,
    monkeypatch,
    status,
    extra_args,
):
    target = tmp_path / "movies" / "Movie"
    target.mkdir(parents=True)
    info = MediaInfo(path=target / "movie.mkv", needs_video_recode=True)
    configure_process_main(monkeypatch, [info])
    monkeypatch.setattr(
        recode,
        "process_file",
        lambda *args, **kwargs: {"status": status, "error": None},
    )
    monkeypatch.setattr(
        recode,
        "trigger_plex_scan",
        lambda path, quiet=False: pytest.fail(f"unexpected Plex scan for {path}"),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "xbox-recode",
            "process",
            str(target),
            "--plex",
            str(tmp_path),
            *extra_args,
        ],
    )

    recode.main()


def test_main_writes_correlated_started_and_finished_lifecycle_entries(tmp_path: Path, monkeypatch):
    target = tmp_path / "movies" / "Movie"
    target.mkdir(parents=True)
    info = MediaInfo(path=target / "movie.mkv", needs_video_recode=True)
    monkeypatch.setattr(recode, "acquire_lock", lambda path: NullLock())
    monkeypatch.setattr(recode, "scan_directory", lambda *args, **kwargs: [info])
    monkeypatch.setattr(recode, "recover_interrupted_recodes", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        recode,
        "process_file",
        lambda *args, **kwargs: {"status": "success", "path": str(info.path)},
    )
    entries = []
    monkeypatch.setattr(
        recode, "write_log_entry", lambda entry, *args, **kwargs: entries.append(entry)
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "xbox-recode",
            "process",
            str(target),
            "--plex",
            str(tmp_path),
            "--no-plex-scan",
        ],
    )

    recode.main()

    assert [entry["event"] for entry in entries] == ["started", "finished"]
    assert entries[0]["operation_id"] == entries[1]["operation_id"]
    assert entries[0]["run_id"] == entries[1]["run_id"]


def test_main_waits_for_rocm_idle_before_processing_file(tmp_path: Path, monkeypatch):
    target = tmp_path / "movies" / "Movie"
    target.mkdir(parents=True)
    info = MediaInfo(path=target / "movie.mkv", needs_video_recode=True)
    configure_process_main(monkeypatch, [info])
    monkeypatch.setattr(recode, "recover_interrupted_recodes", lambda *args, **kwargs: [])
    waits = []
    monkeypatch.setattr(
        recode,
        "wait_for_rocm_gpu_idle",
        lambda **kwargs: waits.append(kwargs),
    )
    monkeypatch.setattr(
        recode,
        "process_file",
        lambda *args, **kwargs: {"status": "success", "path": str(info.path)},
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "xbox-recode",
            "process",
            str(target),
            "--plex",
            str(tmp_path),
            "--no-plex-scan",
            "--wait-for-gpu-idle",
            "--gpu-poll-seconds",
            "15",
        ],
    )

    recode.main()

    assert len(waits) == 2
    assert waits[0]["poll_seconds"] == 15
    assert waits[0]["max_use_percent"] == 5
    assert waits[0]["max_memory_percent"] == 10


def test_main_checks_plex_playback_before_processing_file(tmp_path: Path, monkeypatch):
    target = tmp_path / "movies" / "Movie"
    target.mkdir(parents=True)
    info = MediaInfo(path=target / "movie.mkv", needs_video_recode=True)
    configure_process_main(monkeypatch, [info])
    monkeypatch.setattr(recode, "recover_interrupted_recodes", lambda *args, **kwargs: [])
    waits = []
    monkeypatch.setattr(
        recode,
        "wait_for_plex_playback_idle",
        lambda **kwargs: waits.append(kwargs),
    )
    monkeypatch.setattr(
        recode,
        "process_file",
        lambda *args, **kwargs: {"status": "success", "path": str(info.path)},
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "xbox-recode",
            "process",
            str(target),
            "--plex",
            str(tmp_path),
            "--no-plex-scan",
            "--wait-for-plex-idle",
            "--plex-poll-seconds",
            "10",
        ],
    )

    recode.main()

    assert len(waits) == 4
    assert all(wait["poll_seconds"] == 10 for wait in waits)


def test_status_subcommand_emits_json_summary(tmp_path: Path, monkeypatch, capsys):
    log_file = tmp_path / "recode-2026-08-06.jsonl"
    log_file.write_text('{"status":"success","space_saved_bytes":2048}\n')
    monkeypatch.setattr("sys.argv", ["xbox-recode", "status", "--log-dir", str(tmp_path), "--json"])

    recode.main()

    summary = json.loads(capsys.readouterr().out)
    assert summary["succeeded"] == 1
    assert summary["space_saved_bytes"] == 2048
