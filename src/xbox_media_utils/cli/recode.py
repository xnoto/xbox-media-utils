"""Xbox Series X Media Library Recoder CLI.

Processes existing media files in-place for Xbox Series X / Plex compatibility.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from xbox_media_utils.api import PlexError, PlexScanner
from xbox_media_utils.cli.common import (
    add_dry_run_argument,
    add_no_hardware_argument,
    add_quiet_argument,
    validate_path_exists,
)
from xbox_media_utils.constants import MEDIA_EXTENSIONS
from xbox_media_utils.core import (
    LOCK_FILE,
    LOG_DIR,
    PLEX_GROUP,
    PLEX_USER,
    LockAcquisitionError,
    acquire_lock,
    get_dovi_backup_root,
    get_plex_root,
    write_log_entry,
)
from xbox_media_utils.ffmpeg import run_ffmpeg_with_fallback, validate_output
from xbox_media_utils.files import collect_media_files, set_ownership
from xbox_media_utils.hdr import (
    archive_dovi_original,
    create_hdr10_copy,
    get_dovi_archive_path,
    needs_hdr10_copy,
    promote_hdr10_copy,
)
from xbox_media_utils.media import (
    has_extractable_subs,
    is_sample_file,
    needs_processing,
    probe_file,
)
from xbox_media_utils.subtitles import extract_subtitles


def log(msg: str, quiet: bool = False) -> None:
    """Print message unless in quiet mode."""
    if not quiet:
        print(msg, flush=True)


def trigger_plex_scan(target: Path, quiet: bool = False) -> bool:
    """Trigger a partial Plex scan for a successfully processed path."""
    try:
        result = PlexScanner().scan_path(target)
    except PlexError as e:
        print(f"  [plex_scan] {e}", file=sys.stderr)
        return False

    log(f"  [plex_scan] {result['message']}", quiet)
    return bool(result["success"])


def process_file(
    info,
    dry_run: bool = False,
    quiet: bool = False,
    use_hardware: bool = True,
    plex_user: str = PLEX_USER,
    plex_group: str = PLEX_GROUP,
    dovi_backup_root: Path | None = None,
    library_root: Path | None = None,
    process_dovi_backup: bool = False,
) -> dict:
    """Process a single file."""
    result: dict[str, Any] = {
        "path": str(info.path),
        "status": "skipped",
        "video_action": "copy",
        "audio_action": "copy",
        "subtitle_action": "none",
        "dovi_action": "none",
        "subtitles_extracted": [],
        "hdr10_copy": None,
        "error": None,
    }

    has_subs = has_extractable_subs(info)
    needs_hdr10 = False if process_dovi_backup else needs_hdr10_copy(info)
    can_promote_hdr10 = info.has_dovi_profile_8 and not info.needs_audio_recode and not has_subs
    processing_info = info
    processing_from_hdr10 = False

    if process_dovi_backup:
        processing_info = replace(
            info,
            needs_video_recode=False,
            video_recode_reason=None,
            incompatible_reason=None,
            has_dovi_profile_8=False,
        )
        result["video_action"] = "copy: archived DoVi backup"
    elif info.incompatible_reason:
        result["status"] = "incompatible"
        result["video_action"] = "skip"
        result["error"] = info.incompatible_reason
        return result

    if not needs_processing(processing_info) and not has_subs and not needs_hdr10:
        result["status"] = "compatible"
        return result

    if processing_info.needs_video_recode:
        result["video_action"] = f"recode: {processing_info.video_recode_reason}"
    if processing_info.needs_audio_recode:
        result["audio_action"] = f"recode: {processing_info.audio_recode_reason}"
    if has_subs:
        text_count = sum(1 for s in info.subtitle_tracks if s.is_text)
        image_count = sum(1 for s in info.subtitle_tracks if s.is_image)
        sub_parts = []
        if text_count:
            sub_parts.append(f"{text_count} text")
        if image_count:
            sub_parts.append(f"{image_count} image")
        result["subtitle_action"] = f"extract {', '.join(sub_parts)} subtitle(s), remux to strip"
    if needs_hdr10:
        if can_promote_hdr10:
            result["dovi_action"] = (
                f"promote HDR10 copy to primary and archive original outside Plex "
                f"(DoVi Profile {info.dovi_profile})"
            )
        else:
            result["dovi_action"] = (
                f"create HDR10 copy, process it, and archive original "
                f"(DoVi Profile {info.dovi_profile})"
            )

    if dry_run:
        result["status"] = "would_process"
        return result

    def set_output_ownership(path_str: str) -> None:
        set_ownership(Path(path_str), plex_user, plex_group)

    final_path = info.path.with_suffix(".mkv")
    output_path = info.path.with_suffix(".xbox.mkv")

    # Extract subtitles first
    if has_subs:
        log(f"  Extracting subtitles from: {info.path.name}", quiet)
        result["subtitles_extracted"] = extract_subtitles(
            info, final_path, logger=lambda m: log(m, quiet)
        )
        for extracted in result["subtitles_extracted"]:
            if extracted.get("success") and extracted.get("output"):
                set_output_ownership(extracted["output"])

    # Create HDR10 copy for DoVi Profile 8 content
    if needs_hdr10:
        log(f"  Creating HDR10 copy for DoVi P8: {info.path.name}", quiet)
        hdr10_success, hdr10_msg, hdr10_path = create_hdr10_copy(
            info, info.path.parent, logger=lambda m: log(m, quiet)
        )
        result["hdr10_copy"] = {
            "success": hdr10_success,
            "message": hdr10_msg,
            "path": str(hdr10_path) if hdr10_path else None,
        }
        if not hdr10_success:
            log(f"    WARNING: HDR10 copy creation failed: {hdr10_msg}", quiet)
            result["status"] = "failed"
            result["error"] = f"HDR10 copy creation failed: {hdr10_msg}"
            return result
        elif hdr10_path:
            set_ownership(hdr10_path, plex_user, plex_group)

        if can_promote_hdr10 and hdr10_success and hdr10_path:
            log(f"  Promoting HDR10 copy to primary filename: {info.path.name}", quiet)
            promote_success, promote_msg, dv_path = promote_hdr10_copy(
                info, hdr10_path, dovi_backup_root, library_root
            )
            if not promote_success:
                result["status"] = "failed"
                result["error"] = promote_msg
                return result

            set_ownership(info.path, plex_user, plex_group)
            if dv_path:
                set_ownership(dv_path, plex_user, plex_group)

            result["status"] = "success"
            result["output_path"] = str(info.path)
            result["archived_dovi_path"] = str(dv_path) if dv_path else None
            return result

        if hdr10_success and hdr10_path:
            processing_info = replace(
                info,
                path=hdr10_path,
                video_hdr_type="hdr10",
                needs_video_recode=False,
                video_recode_reason=None,
                dovi_profile=None,
                has_dovi_profile_8=False,
            )
            processing_from_hdr10 = True

    needs_recode = needs_processing(processing_info)

    # Remux-only path (no recode needed)
    if not needs_recode and has_subs:
        log(f"  Remuxing to strip embedded subs: {info.path.name}", quiet)
        from xbox_media_utils.media import ffmpeg_path

        cmd = [
            ffmpeg_path(),
            "-y",
            "-v",
            "error",
            "-i",
            str(processing_info.path),
            "-map",
            "0:v:0",
            "-map",
            "0:a",
            "-c:v",
            "copy",
            "-c:a",
            "copy",
            "-sn",
            "-max_muxing_queue_size",
            "65536",
            str(output_path),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode != 0:
            result["status"] = "failed"
            result["error"] = proc.stderr[-500:] if proc.stderr else "Remux failed"
            if output_path.exists():
                output_path.unlink()
            return result
    elif not needs_recode:
        # No recode, no subs - just HDR10 copy was needed
        result["status"] = "success"
        result["output_path"] = str(info.path)
        return result
    else:
        # Transcode path
        log(f"  Processing: {info.path.name}", quiet)
        success, error = run_ffmpeg_with_fallback(processing_info, output_path, use_hardware)

        if not success:
            result["status"] = "failed"
            result["error"] = error[-500:] if error else "Unknown error"
            if output_path.exists():
                output_path.unlink()
            return result

    # Validate output
    valid, msg = validate_output(processing_info, output_path)
    if not valid:
        result["status"] = "failed"
        result["error"] = f"Validation failed: {msg}"
        if output_path.exists():
            output_path.unlink()
        return result

    # Safe file replacement
    try:
        archived_dovi_path = None
        archived_dovi_moved = False
        backup_path = None
        if processing_from_hdr10:
            archive_success, archive_msg, archived_dovi_path = archive_dovi_original(
                info.path, dovi_backup_root, library_root
            )
            if not archive_success:
                if archive_msg.startswith("Archive path already exists: "):
                    archived_dovi_path = get_dovi_archive_path(
                        info.path, dovi_backup_root, library_root
                    )
                    result["dovi_action"] += "; backup already exists, replacing Plex copy"
                else:
                    result["status"] = "failed"
                    result["error"] = archive_msg
                    if output_path.exists():
                        output_path.unlink()
                    return result
            else:
                archived_dovi_moved = True
        else:
            backup_path = info.path.with_suffix(info.path.suffix + ".bak")
            info.path.rename(backup_path)

        try:
            output_path.replace(final_path)
        except Exception as e:
            if processing_from_hdr10 and archived_dovi_moved and archived_dovi_path:
                shutil.move(str(archived_dovi_path), str(info.path))
            elif backup_path:
                backup_path.rename(info.path)
            result["status"] = "failed"
            result["error"] = f"Rename failed: {e}"
            if output_path.exists():
                output_path.unlink()
            return result

        # Set ownership
        set_ownership(final_path, plex_user, plex_group)

        if processing_from_hdr10 and processing_info.path.exists():
            processing_info.path.unlink()
        elif backup_path:
            # Delete backup
            backup_path.unlink()

    except Exception as e:
        result["status"] = "failed"
        result["error"] = f"File operation failed: {e}"
        if output_path.exists():
            output_path.unlink()
        return result

    result["status"] = "success"
    result["output_path"] = str(final_path)
    if processing_from_hdr10 and archived_dovi_path:
        result["archived_dovi_path"] = str(archived_dovi_path)
    return result


def scan_directory(path: Path, quiet: bool = False) -> list:
    """Scan directory for media files."""
    files = collect_media_files(path, MEDIA_EXTENSIONS)

    results = []
    for f in files:
        if ".xbox." in f.name or f.name.endswith(".HDR10.mkv") or f.name.endswith(".DV.mkv"):
            continue
        if is_sample_file(f):
            log(f"Skipping sample file: {f.name}", quiet)
            continue
        log(f"Probing: {f.name}...", quiet)
        info = probe_file(f)
        if info.probe_error:
            log(f"  ERROR: {info.probe_error}", quiet)
        else:
            reasons = []
            if needs_processing(info):
                reasons.append("RECODE")
            if has_extractable_subs(info):
                text_count = sum(1 for s in info.subtitle_tracks if s.is_text)
                image_count = sum(1 for s in info.subtitle_tracks if s.is_image)
                sub_parts = []
                if text_count:
                    sub_parts.append(f"{text_count}txt")
                if image_count:
                    sub_parts.append(f"{image_count}img")
                reasons.append(f"SUBS({'+'.join(sub_parts)})")
            if info.has_dovi_profile_8:
                reasons.append("DOVI-P8")
            if reasons:
                log(f"  -> {' '.join(reasons)}", quiet)
            else:
                log("  -> OK", quiet)
        results.append(info)

    return results


def scan_dovi_backups(path: Path, quiet: bool = False) -> list:
    """Scan archived DoVi backup files for audio/subtitle processing."""
    files = [f for f in collect_media_files(path, MEDIA_EXTENSIONS) if f.name.endswith(".DV.mkv")]

    results = []
    for f in files:
        if ".xbox." in f.name or is_sample_file(f):
            continue
        log(f"Probing backup: {f.name}...", quiet)
        info = probe_file(f)
        if info.probe_error:
            log(f"  ERROR: {info.probe_error}", quiet)
        else:
            reasons = []
            if info.needs_audio_recode:
                reasons.append("AUDIO")
            if has_extractable_subs(info):
                text_count = sum(1 for s in info.subtitle_tracks if s.is_text)
                image_count = sum(1 for s in info.subtitle_tracks if s.is_image)
                sub_parts = []
                if text_count:
                    sub_parts.append(f"{text_count}txt")
                if image_count:
                    sub_parts.append(f"{image_count}img")
                reasons.append(f"SUBS({'+'.join(sub_parts)})")
            if reasons:
                log(f"  -> {' '.join(reasons)}", quiet)
            else:
                log("  -> OK", quiet)
        results.append(info)

    return results


def print_scan_summary(results: list, quiet: bool = False) -> None:
    """Print summary of scan results."""
    total = len(results)
    errors = sum(1 for r in results if r.probe_error)
    needs_video = sum(1 for r in results if r.needs_video_recode)
    needs_audio = sum(1 for r in results if r.needs_audio_recode)
    has_subs = sum(1 for r in results if has_extractable_subs(r))
    has_dovi_p8 = sum(1 for r in results if r.has_dovi_profile_8)
    incompatible = sum(1 for r in results if r.incompatible_reason)
    needs_any = sum(
        1 for r in results if needs_processing(r) or has_extractable_subs(r) or needs_hdr10_copy(r)
    )
    compatible = total - needs_any - errors

    log("\n" + "=" * 60, quiet)
    log("SCAN SUMMARY", quiet)
    log("=" * 60, quiet)
    log(f"Total files:           {total}", quiet)
    log(f"Already compatible:    {compatible}", quiet)
    log(f"Need processing:       {needs_any}", quiet)
    log(f"  - Video recode:      {needs_video}", quiet)
    log(f"  - Audio recode:      {needs_audio}", quiet)
    log(f"  - Subtitle extract:  {has_subs}", quiet)
    log(f"  - DoVi P8 HDR10:     {has_dovi_p8}", quiet)
    log(f"Incompatible (block):  {incompatible}", quiet)
    log(f"Probe errors:          {errors}", quiet)
    log("=" * 60, quiet)


def write_incompatible_report(results: list, output: Path) -> int:
    """Write tab-separated report of files the current pipeline cannot process.

    Returns the count of incompatible files written.
    """
    lines = []
    for r in results:
        if not r.incompatible_reason:
            continue
        details = []
        if r.dovi_profile is not None:
            details.append(f"DV Profile {r.dovi_profile}")
        if r.video_codec:
            details.append(r.video_codec)
        if r.video_bit_depth:
            details.append(f"{r.video_bit_depth}-bit")
        detail_str = ", ".join(details) if details else "?"
        lines.append(f"{r.path}\t{detail_str}\t{r.incompatible_reason}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Xbox Series X Media Library Recoder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Scan command
    scan_parser = subparsers.add_parser("scan", help="Scan and report")
    scan_parser.add_argument("path", type=Path, help="Directory or file to scan")
    add_quiet_argument(scan_parser)

    # Process command
    process_parser = subparsers.add_parser("process", help="Process files")
    process_parser.add_argument("path", type=Path, help="Directory or file to process")
    process_parser.add_argument("--file", action="store_true", help="Single file only")
    process_parser.add_argument("--plex", type=str, default=None, help="Plex root path")
    process_parser.add_argument("--dovi-backup", type=str, default=None, help="DoVi backup root")
    process_parser.add_argument(
        "--no-plex-scan",
        action="store_true",
        help="Do not trigger a Plex scan after successful processing",
    )
    add_dry_run_argument(process_parser)
    add_quiet_argument(process_parser)
    add_no_hardware_argument(process_parser)

    # Backup command — process archived DoVi originals without video recoding.
    backups_parser = subparsers.add_parser(
        "process-backups",
        help="Audio/subtitle-process archived .DV.mkv backups while copying video",
    )
    backups_parser.add_argument("path", type=Path, help="DoVi backup directory or file")
    backups_parser.add_argument("--file", action="store_true", help="Single backup file only")
    add_dry_run_argument(backups_parser)
    add_quiet_argument(backups_parser)
    add_no_hardware_argument(backups_parser)

    # Incompat command — list files the current pipeline refuses to process.
    incompat_parser = subparsers.add_parser(
        "incompat",
        help="List files that the current pipeline cannot make Xbox-compatible",
    )
    incompat_parser.add_argument("path", type=Path, help="Directory to scan")
    incompat_parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        metavar="FILE",
        help="Path to write the tab-separated incompatibility report",
    )
    add_quiet_argument(incompat_parser)

    args = parser.parse_args()

    validate_path_exists(args.path)

    quiet = getattr(args, "quiet", False)
    use_hardware = not getattr(args, "no_hardware", False)
    plex_root = get_plex_root(getattr(args, "plex", None))
    dovi_backup_root = get_dovi_backup_root(getattr(args, "dovi_backup", None), plex_root)

    if args.command == "scan":
        results = scan_directory(args.path, quiet)
        print_scan_summary(results, quiet)

    elif args.command == "incompat":
        results = scan_directory(args.path, quiet)
        count = write_incompatible_report(results, args.output)
        log(f"\nWrote {count} incompatible file(s) to {args.output}", quiet)

    elif args.command in ("process", "process-backups"):
        try:
            with acquire_lock(LOCK_FILE):
                process_dovi_backup = args.command == "process-backups"
                if args.file:
                    results = [probe_file(args.path)]
                elif process_dovi_backup:
                    results = scan_dovi_backups(args.path, quiet)
                else:
                    results = scan_directory(args.path, quiet)

                if process_dovi_backup:
                    to_process = [
                        r for r in results if r.needs_audio_recode or has_extractable_subs(r)
                    ]
                else:
                    to_process = [
                        r
                        for r in results
                        if needs_processing(r) or has_extractable_subs(r) or needs_hdr10_copy(r)
                    ]

                if not to_process:
                    log("No files need processing.", quiet)
                    return

                log(f"\nProcessing {len(to_process)} files...", quiet)

                failed_count = 0
                successful_count = 0
                all_succeeded = True
                for info in to_process:
                    result = process_file(
                        info,
                        dry_run=args.dry_run,
                        quiet=quiet,
                        use_hardware=use_hardware,
                        dovi_backup_root=dovi_backup_root,
                        library_root=plex_root,
                        process_dovi_backup=process_dovi_backup,
                    )
                    write_log_entry(result, LOG_DIR, prefix="recode")
                    if result["status"] == "success":
                        successful_count += 1
                    else:
                        all_succeeded = False

                    if result["status"] == "failed":
                        failed_count += 1

                    if result["status"] == "incompatible":
                        log(
                            f"  ! {info.path.name}: INCOMPATIBLE FORMAT — refusing to recode",
                            quiet,
                        )
                        log(f"      Reason: {result.get('error')}", quiet)
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
                            log(f"      Detected: {', '.join(details)}", quiet)
                        log(
                            "      Action: skipped — re-acquire a Dolby Vision Profile 8 "
                            "(HDR10-compatible) or non-DV source",
                            quiet,
                        )
                    else:
                        symbol = (
                            "✓"
                            if result["status"] == "success"
                            else "✗"
                            if result["status"] == "failed"
                            else "○"
                        )
                        log(f"  {symbol} {info.path.name}: {result['status']}", quiet)
                        if result.get("error"):
                            log(f"      Error: {result['error']}", quiet)

                scan_ok = True
                if (
                    args.command == "process"
                    and successful_count > 0
                    and all_succeeded
                    and not args.dry_run
                    and not args.no_plex_scan
                ):
                    scan_target = args.path.parent if args.path.is_file() else args.path
                    log(f"Triggering Plex scan for: {scan_target}", quiet)
                    scan_ok = trigger_plex_scan(scan_target, quiet)

                if failed_count or not scan_ok:
                    sys.exit(1)
        except LockAcquisitionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
