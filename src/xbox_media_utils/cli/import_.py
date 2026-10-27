"""Xbox Series X Media Importer CLI.

Imports new media files to Plex library with Xbox Series X compatibility.
Unlike recode (which processes in-place), this COPIES from source to destination.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from xbox_media_utils.api import PlexError, PlexScanner
from xbox_media_utils.cli.common import (
    add_dry_run_argument,
    add_no_hardware_argument,
    validate_path_exists,
)
from xbox_media_utils.constants import MEDIA_EXTENSIONS
from xbox_media_utils.core import (
    DEFAULT_LIBRARY,
    ENV_LIBRARY,
    ENV_PLEX_ROOT,
    IMPORT_LOG_DIR,
    PLEX_GROUP,
    PLEX_USER,
    get_config_value,
    get_dovi_backup_root,
    get_plex_root,
    write_log_entry,
)
from xbox_media_utils.ffmpeg import run_ffmpeg_with_fallback, validate_output
from xbox_media_utils.files import collect_media_files, set_ownership
from xbox_media_utils.hdr import (
    copy_dovi_original_to_archive,
    create_hdr10_copy,
    needs_hdr10_copy,
)
from xbox_media_utils.media import has_extractable_subs, needs_processing, probe_file
from xbox_media_utils.subtitles import extract_subtitles


def trigger_plex_scan(target: Path) -> bool:
    """Trigger a partial Plex scan for an imported path."""
    try:
        result = PlexScanner().scan_path(target)
    except PlexError as e:
        print(f"  [plex_scan] {e}", file=sys.stderr)
        return False

    print(f"  [plex_scan] {result['message']}")
    return bool(result["success"])


def import_file(
    info,
    dest_dir: Path,
    plex_root: Path,
    dovi_backup_root: Path | None = None,
    dry_run: bool = False,
    use_hardware: bool = True,
) -> dict:
    """Import a single media file."""
    result: dict[str, Any] = {
        "source": str(info.path),
        "destination": None,
        "status": "pending",
        "action": "copy" if not needs_processing(info) else "transcode",
        "subtitles_extracted": [],
        "hdr10_copy": None,
        "archived_dovi_path": None,
        "error": None,
    }

    output_name = info.path.stem + ".mkv" if needs_processing(info) else info.path.name
    dest_path = dest_dir / output_name
    result["destination"] = str(dest_path)

    if info.incompatible_reason:
        result["status"] = "incompatible"
        result["action"] = "skip"
        result["error"] = info.incompatible_reason
        return result

    has_subs = has_extractable_subs(info)
    hdr10_path = dest_dir / (info.path.stem + ".HDR10.mkv")
    needs_hdr10 = needs_hdr10_copy(info, hdr10_path)

    if dry_run:
        result["status"] = "would_import"
        if has_subs:
            text_count = sum(1 for s in info.subtitle_tracks if s.is_text)
            image_count = sum(1 for s in info.subtitle_tracks if s.is_image)
            sub_parts = []
            if text_count:
                sub_parts.append(f"{text_count} text")
            if image_count:
                sub_parts.append(f"{image_count} image")
            result["subtitle_action"] = f"extract {', '.join(sub_parts)} subtitle(s)"
        if needs_hdr10:
            result["dovi_action"] = (
                f"import HDR10 copy as primary and archive DoVi original "
                f"(DoVi Profile {info.dovi_profile})"
            )
        return result

    def set_output_ownership(path_str: str) -> None:
        set_ownership(Path(path_str), PLEX_USER, PLEX_GROUP)

    # Create destination directory
    dest_dir.mkdir(parents=True, exist_ok=True)
    current = dest_dir
    while current != plex_root and current != current.parent:
        set_ownership(current, PLEX_USER, PLEX_GROUP)
        current = current.parent

    # Extract subtitles first
    if has_subs:
        print("    Extracting subtitles...")
        result["subtitles_extracted"] = extract_subtitles(info, dest_path)
        for extracted in result["subtitles_extracted"]:
            if extracted.get("success") and extracted.get("output"):
                set_output_ownership(extracted["output"])

    if needs_hdr10:
        backup_root = dovi_backup_root or (plex_root / "backup")
        archive_success, archive_msg, archived_path = copy_dovi_original_to_archive(
            info.path, dest_path, backup_root, plex_root
        )
        if not archive_success and not archive_msg.startswith("Archive path already exists: "):
            result["status"] = "failed"
            result["error"] = archive_msg
            return result
        result["archived_dovi_path"] = str(archived_path) if archived_path else None

        print("    Creating HDR10 primary for DoVi P8...")
        hdr10_success, hdr10_msg, hdr10_path_result = create_hdr10_copy(info, dest_dir)
        result["hdr10_copy"] = {
            "success": hdr10_success,
            "message": hdr10_msg,
            "path": str(hdr10_path_result) if hdr10_path_result else None,
        }
        if not hdr10_success or not hdr10_path_result:
            result["status"] = "failed"
            result["error"] = f"HDR10 copy creation failed: {hdr10_msg}"
            return result

        set_output_ownership(str(hdr10_path_result))
        processing_info = replace(
            info,
            path=hdr10_path_result,
            video_hdr_type="hdr10",
            needs_video_recode=False,
            video_recode_reason=None,
            dovi_profile=None,
            has_dovi_profile_8=False,
        )

        temp_path = dest_dir / (info.path.stem + ".importing.mkv")
        if processing_info.needs_audio_recode:
            print(f"    Transcoding audio from HDR10 copy: {info.path.name}")
            success, error = run_ffmpeg_with_fallback(processing_info, temp_path, use_hardware)

            if not success:
                result["status"] = "failed"
                result["error"] = error[-500:] if error else "Transcode failed"
                if temp_path.exists():
                    temp_path.unlink()
                return result

            valid, msg = validate_output(processing_info, temp_path)
            if not valid:
                result["status"] = "failed"
                result["error"] = f"Validation: {msg}"
                if temp_path.exists():
                    temp_path.unlink()
                return result

            temp_path.replace(dest_path)
            if hdr10_path_result.exists():
                hdr10_path_result.unlink()
            result["action"] = "transcode"
        elif has_subs:
            print(f"    Remuxing HDR10 copy (strip subs): {info.path.name}")
            from xbox_media_utils.ffmpeg import remux_with_mkvmerge

            success, error = remux_with_mkvmerge(processing_info.path, temp_path)

            if not success:
                result["status"] = "failed"
                result["error"] = error[-500:] if error else "Remux failed"
                if temp_path.exists():
                    temp_path.unlink()
                return result

            valid, msg = validate_output(processing_info, temp_path)
            if not valid:
                result["status"] = "failed"
                result["error"] = f"Validation: {msg}"
                if temp_path.exists():
                    temp_path.unlink()
                return result

            temp_path.replace(dest_path)
            if hdr10_path_result.exists():
                hdr10_path_result.unlink()
            result["action"] = "remux"
        else:
            hdr10_path_result.replace(dest_path)
            result["action"] = "hdr10-copy"

        set_ownership(dest_path, PLEX_USER, PLEX_GROUP)
        result["status"] = "success"
        return result

    # Handle main file
    if needs_processing(info):
        temp_path = dest_dir / (info.path.stem + ".importing.mkv")
        print(f"    Transcoding: {info.path.name}")
        success, error = run_ffmpeg_with_fallback(info, temp_path, use_hardware)

        if not success:
            result["status"] = "failed"
            result["error"] = error[-500:] if error else "Transcode failed"
            if temp_path.exists():
                temp_path.unlink()
            return result

        valid, msg = validate_output(info, temp_path)
        if not valid:
            result["status"] = "failed"
            result["error"] = f"Validation: {msg}"
            if temp_path.exists():
                temp_path.unlink()
            return result

        try:
            temp_path.rename(dest_path)
        except Exception as e:
            result["status"] = "failed"
            result["error"] = f"Rename failed: {e}"
            if temp_path.exists():
                temp_path.unlink()
            return result

    elif has_subs:
        # Remux to strip subs
        temp_path = dest_dir / (info.path.stem + ".importing.mkv")
        print(f"    Remuxing (strip subs): {info.path.name}")
        from xbox_media_utils.ffmpeg import remux_with_mkvmerge

        success, error = remux_with_mkvmerge(info.path, temp_path)

        if not success:
            result["status"] = "failed"
            result["error"] = error[-500:] if error else "Remux failed"
            if temp_path.exists():
                temp_path.unlink()
            return result

        valid, msg = validate_output(info, temp_path)
        if not valid:
            result["status"] = "failed"
            result["error"] = f"Validation: {msg}"
            if temp_path.exists():
                temp_path.unlink()
            return result

        try:
            temp_path.rename(dest_path)
        except Exception as e:
            result["status"] = "failed"
            result["error"] = f"Rename failed: {e}"
            if temp_path.exists():
                temp_path.unlink()
            return result

        result["action"] = "remux"
    else:
        print(f"    Copying: {info.path.name}")
        try:
            shutil.copy2(info.path, dest_path)
        except Exception as e:
            result["status"] = "failed"
            result["error"] = f"Copy failed: {e}"
            return result

    set_ownership(dest_path, PLEX_USER, PLEX_GROUP)

    # Create HDR10 copy
    if needs_hdr10:
        print("    Creating HDR10 copy for DoVi P8...")
        hdr10_success, hdr10_msg, hdr10_path_result = create_hdr10_copy(info, dest_dir)
        result["hdr10_copy"] = {
            "success": hdr10_success,
            "message": hdr10_msg,
            "path": str(hdr10_path_result) if hdr10_path_result else None,
        }
        if not hdr10_success:
            print(f"      WARNING: HDR10 copy creation failed: {hdr10_msg}")
        elif hdr10_path_result:
            set_output_ownership(str(hdr10_path_result))

    result["status"] = "success"
    return result


def discover_rar_archives(source: Path) -> list[Path]:
    """Find archive sets, leaving continuation volumes for unrar to consume."""
    archives = sorted(path for path in source.rglob("*") if path.suffix.lower() == ".rar")
    first_volumes = set()
    for archive in archives:
        match = re.fullmatch(r"(.+)\.part(\d+)\.rar", archive.name, re.IGNORECASE)
        if match and int(match[2]) == 1:
            first_volumes.add((archive.parent, match[1].casefold()))
    selected = []
    for archive in archives:
        if archive.is_symlink() or not archive.is_file():
            raise RuntimeError(f"RAR source is not a regular file: {archive}")
        archive.resolve().relative_to(source.resolve())
        match = re.fullmatch(r"(.+)\.part(\d+)\.rar", archive.name, re.IGNORECASE)
        if match and int(match[2]) != 1:
            if (archive.parent, match[1].casefold()) not in first_volumes:
                raise RuntimeError(f"Missing first RAR volume for: {archive}")
            continue
        selected.append(archive)
    return selected


def stage_rar_media(
    source: Path, dest_base: Path, archives: list[Path]
) -> tuple[Path, dict[Path, Path]]:
    """Extract archive sets outside the source and map media to logical source paths."""
    source_root = source.resolve()
    destination = dest_base.resolve()
    if source_root == destination or source_root in destination.parents or destination in source_root.parents:
        raise RuntimeError("RAR import source and destination must not overlap")
    unrar = shutil.which("unrar")
    if unrar is None:
        raise RuntimeError("RAR imports require host-provided unrar with -ol- support")
    staging = Path(tempfile.mkdtemp(prefix=".xbox-import-", dir=source_root.parent))
    print(f"Archive staging: {staging}")
    media_paths: dict[Path, Path] = {}
    try:
        for index, archive in enumerate(archives):
            extract_dir = staging / str(index)
            extract_dir.mkdir()
            print(f"Extracting RAR: {archive}")
            completed = subprocess.run(
                [
                    unrar,
                    "x",
                    "-cfg-",
                    "-ol-",
                    "-o-",
                    "-p-",
                    "-idq",
                    "--",
                    str(archive.resolve()),
                    str(extract_dir) + os.sep,
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                cwd=extract_dir,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "Extraction failed")[-2000:]
                raise RuntimeError(f"unrar failed for {archive} (exit {completed.returncode}): {detail}")
            for path in extract_dir.rglob("*"):
                if path.is_symlink() or not (path.is_file() or path.is_dir()):
                    raise RuntimeError(f"Unsafe RAR output (link or special file): {path}")
                path.resolve().relative_to(extract_dir)
            for path in collect_media_files(extract_dir, MEDIA_EXTENSIONS):
                media_paths[path] = archive.parent.relative_to(source) / path.relative_to(extract_dir)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(f"{error}\nStaged files retained at: {staging}") from error
    return staging, media_paths


def preflight_archive_imports(relative_paths: dict[Path, Path], dest_base: Path) -> None:
    """Refuse ambiguous or existing outputs before importing an archive batch."""
    reserved: dict[Path, Path] = {}
    for source_path, relative in relative_paths.items():
        # Recode may normalize any input extension to .mkv; reserve both names.
        targets = {dest_base / relative, dest_base / relative.with_suffix(".mkv")}
        for target in targets:
            previous = reserved.get(target)
            if previous is not None and previous != source_path:
                raise RuntimeError(f"RAR import destination collision: {target}")
            reserved[target] = source_path
            if target.exists() or target.is_symlink():
                raise RuntimeError(f"RAR import destination already exists: {target}")
            current = target.parent
            while current != dest_base.parent:
                if current.is_symlink() or (current.exists() and not current.is_dir()):
                    raise RuntimeError(f"Unsafe RAR import destination directory: {current}")
                current = current.parent


def finish_archive_staging(staging: Path, pending: set[Path]) -> bool:
    """Remove a completed private stage, retaining anything not successfully imported."""
    if pending:
        print(f"Staged files retained at: {staging}")
        return True
    try:
        shutil.rmtree(staging)
    except OSError as error:
        print(f"Error cleaning archive staging {staging}: {error}", file=sys.stderr)
        return False
    return True


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Import media with Xbox compatibility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", type=Path, help="Source file or directory")
    parser.add_argument(
        "--library",
        "-l",
        type=str,
        default=None,
        metavar="NAME",
        help=f"Target library name (env: {ENV_LIBRARY})",
    )
    parser.add_argument(
        "--plex",
        "-p",
        type=str,
        default=None,
        metavar="PATH",
        help=f"Plex root path (env: {ENV_PLEX_ROOT})",
    )
    parser.add_argument("--dovi-backup", type=str, default=None, help="DoVi backup root")
    parser.add_argument(
        "--no-plex-scan",
        action="store_true",
        help="Do not trigger a Plex scan after a successful import",
    )
    add_dry_run_argument(parser)
    add_no_hardware_argument(parser)

    args = parser.parse_args()

    validate_path_exists(args.source)

    use_hardware = not args.no_hardware

    plex_root = get_plex_root(args.plex)
    dovi_backup_root = get_dovi_backup_root(args.dovi_backup, plex_root)
    library_name = get_config_value(args.library, ENV_LIBRARY, DEFAULT_LIBRARY)
    library_path = plex_root / library_name

    if not plex_root.exists():
        print(f"Error: Plex root does not exist: {plex_root}", file=sys.stderr)
        sys.exit(1)

    if not library_path.exists():
        print(f"Warning: Library path does not exist, will create: {library_path}")

    source_is_dir = args.source.is_dir()
    if source_is_dir:
        dest_base = library_path / args.source.name
    else:
        # Plex partial scans operate on directories, not individual media files.
        # Give a standalone source file its own import directory so the completed
        # import can be scanned without refreshing the entire library.
        dest_base = library_path / args.source.stem

    staging: Path | None = None
    staged_paths: dict[Path, Path] = {}
    archives: list[Path] = []
    try:
        if source_is_dir:
            archives = discover_rar_archives(args.source)
        if args.dry_run:
            for archive in archives:
                print(f"Would extract RAR before importing: {archive}")
        elif archives:
            staging, staged_paths = stage_rar_media(args.source, dest_base, archives)
        files = collect_media_files(args.source, MEDIA_EXTENSIONS)
        relative_paths = {
            path: path.relative_to(args.source) if source_is_dir else Path(path.name)
            for path in files
        }
        relative_paths.update(staged_paths)
        if staging is not None:
            preflight_archive_imports(relative_paths, dest_base)
        files = sorted(relative_paths, key=lambda path: str(relative_paths[path]))
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error preparing import: {error}", file=sys.stderr)
        if staging is not None:
            print(f"Staged files retained at: {staging}")
        sys.exit(1)

    if not files:
        if staging is not None:
            print(f"Staged files retained at: {staging}")
        if args.dry_run and archives:
            print("Archived media will be discovered and checked during the real import.")
            sys.exit(0)
        print(f"No media files found in: {args.source}")
        sys.exit(1)
    staged_pending = set(staged_paths)

    print(f"Source: {args.source}")
    print(f"Library: {library_name} ({library_path})")
    print(f"Destination: {dest_base}")
    print(f"Files: {len(files)}")
    print()

    success = failed = 0
    scan_target: Path | None = None

    for idx, filepath in enumerate(files):
        if filepath.name.endswith(".HDR10.mkv"):
            if filepath in staged_pending:
                failed += 1
            continue

        if source_is_dir:
            rel_path = relative_paths[filepath]
            dest_dir = dest_base / rel_path.parent
        else:
            dest_dir = dest_base

        print(f"  [{idx + 1}/{len(files)}] {filepath.name}")

        info = probe_file(filepath)
        if info.probe_error:
            print(f"    ERROR: {info.probe_error}")
            failed += 1
            continue

        flags = []
        if needs_processing(info):
            flags.append("RECODE")
        if has_extractable_subs(info):
            text_count = sum(1 for s in info.subtitle_tracks if s.is_text)
            image_count = sum(1 for s in info.subtitle_tracks if s.is_image)
            sub_parts = []
            if text_count:
                sub_parts.append(f"{text_count}txt")
            if image_count:
                sub_parts.append(f"{image_count}img")
            flags.append(f"SUBS({'+'.join(sub_parts)})")
        if info.has_dovi_profile_8:
            flags.append("DOVI-P8")
        if flags:
            print(f"    Detected: {' '.join(flags)}")

        result = import_file(
            info,
            dest_dir,
            plex_root,
            dovi_backup_root=dovi_backup_root,
            dry_run=args.dry_run,
            use_hardware=use_hardware,
        )
        result["timestamp"] = datetime.now().isoformat()
        if not args.dry_run:
            write_log_entry(result, IMPORT_LOG_DIR, prefix="import")

        if result["status"] == "success":
            print(f"    -> {result['action']}: {result['destination']}")
            success += 1
            scan_target = dest_base
            if filepath in staged_pending:
                try:
                    filepath.unlink()
                    staged_pending.remove(filepath)
                except OSError as error:
                    print(f"    ERROR cleaning staged media {filepath}: {error}")
                    failed += 1
        elif result["status"] == "would_import":
            print(f"    -> Would {result['action']}: {result['destination']}")
            if result.get("dovi_action"):
                print(f"      + {result['dovi_action']}")
            success += 1
        elif result["status"] == "incompatible":
            print("    !! INCOMPATIBLE FORMAT — refusing to import")
            print(f"       Reason: {result.get('error')}")
            details = []
            if info.dovi_profile is not None:
                details.append(f"Dolby Vision Profile {info.dovi_profile}")
            if info.video_codec:
                details.append(info.video_codec.upper())
            if info.video_bit_depth:
                details.append(f"{info.video_bit_depth}-bit")
            if info.video_hdr_type:
                details.append(info.video_hdr_type.upper())
            if details:
                print(f"       Detected: {', '.join(details)}")
            print(f"       File:    {filepath.name}")
            print(
                "       Action:  skipped — re-acquire a Dolby Vision Profile 8 "
                "(HDR10-compatible) source, or a non-DV HDR10/SDR source"
            )
            failed += 1
        else:
            print(f"    X Failed: {result.get('error')}")
            failed += 1

    if staging is not None and not finish_archive_staging(staging, staged_pending):
        failed += 1

    print()
    print(f"Complete: {success} succeeded, {failed} failed")
    print(f"Source preserved at: {args.source}")

    scan_ok = True
    if (
        failed == 0
        and success > 0
        and not args.dry_run
        and not args.no_plex_scan
        and scan_target is not None
    ):
        print(f"Triggering Plex scan for: {scan_target}")
        scan_ok = trigger_plex_scan(scan_target)

    sys.exit(0 if failed == 0 and scan_ok else 1)


if __name__ == "__main__":
    main()
