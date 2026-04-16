"""Xbox Series X Media Library Recoder CLI.

Processes existing media files in-place for Xbox Series X / Plex compatibility.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

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
    write_log_entry,
)
from xbox_media_utils.ffmpeg import run_ffmpeg_with_fallback, validate_output
from xbox_media_utils.files import collect_media_files, set_ownership
from xbox_media_utils.hdr import create_hdr10_copy, needs_hdr10_copy, promote_hdr10_copy
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


def process_file(
    info,
    dry_run: bool = False,
    quiet: bool = False,
    use_hardware: bool = True,
    plex_user: str = PLEX_USER,
    plex_group: str = PLEX_GROUP,
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

    needs_recode = needs_processing(info)
    has_subs = has_extractable_subs(info)
    needs_hdr10 = needs_hdr10_copy(info)
    can_promote_hdr10 = info.has_dovi_profile_8 and not info.needs_audio_recode and not has_subs

    if not needs_recode and not has_subs and not needs_hdr10:
        result["status"] = "compatible"
        return result

    if info.needs_video_recode:
        result["video_action"] = f"recode: {info.video_recode_reason}"
    if info.needs_audio_recode:
        result["audio_action"] = f"recode: {info.audio_recode_reason}"
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
                f"promote HDR10 copy to primary and archive original as .DV.mkv "
                f"(DoVi Profile {info.dovi_profile})"
            )
        else:
            result["dovi_action"] = f"create HDR10 copy (DoVi Profile {info.dovi_profile})"

    if dry_run:
        result["status"] = "would_process"
        return result

    final_path = info.path.with_suffix(".mkv")
    output_path = info.path.with_suffix(".xbox.mkv")

    # Extract subtitles first
    if has_subs:
        log(f"  Extracting subtitles from: {info.path.name}", quiet)
        result["subtitles_extracted"] = extract_subtitles(
            info, final_path, logger=lambda m: log(m, quiet)
        )

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

        if can_promote_hdr10 and hdr10_success and hdr10_path:
            log(f"  Promoting HDR10 copy to primary filename: {info.path.name}", quiet)
            promote_success, promote_msg, dv_path = promote_hdr10_copy(info, hdr10_path)
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
            str(info.path),
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
        success, error = run_ffmpeg_with_fallback(info, output_path, use_hardware)

        if not success:
            result["status"] = "failed"
            result["error"] = error[-500:] if error else "Unknown error"
            if output_path.exists():
                output_path.unlink()
            return result

    # Validate output
    valid, msg = validate_output(info, output_path)
    if not valid:
        result["status"] = "failed"
        result["error"] = f"Validation failed: {msg}"
        if output_path.exists():
            output_path.unlink()
        return result

    # Safe file replacement
    try:
        backup_path = info.path.with_suffix(info.path.suffix + ".bak")
        info.path.rename(backup_path)

        try:
            output_path.rename(final_path)
        except Exception as e:
            backup_path.rename(info.path)
            result["status"] = "failed"
            result["error"] = f"Rename failed: {e}"
            if output_path.exists():
                output_path.unlink()
            return result

        # Set ownership
        set_ownership(final_path, plex_user, plex_group)

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


def print_scan_summary(results: list, quiet: bool = False) -> None:
    """Print summary of scan results."""
    total = len(results)
    errors = sum(1 for r in results if r.probe_error)
    needs_video = sum(1 for r in results if r.needs_video_recode)
    needs_audio = sum(1 for r in results if r.needs_audio_recode)
    has_subs = sum(1 for r in results if has_extractable_subs(r))
    has_dovi_p8 = sum(1 for r in results if r.has_dovi_profile_8)
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
    log(f"Probe errors:          {errors}", quiet)
    log("=" * 60, quiet)


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
    add_dry_run_argument(process_parser)
    add_quiet_argument(process_parser)
    add_no_hardware_argument(process_parser)

    args = parser.parse_args()

    validate_path_exists(args.path)

    quiet = getattr(args, "quiet", False)
    use_hardware = not getattr(args, "no_hardware", False)

    if args.command == "scan":
        results = scan_directory(args.path, quiet)
        print_scan_summary(results, quiet)

    elif args.command == "process":
        try:
            with acquire_lock(LOCK_FILE):
                if args.file:
                    results = [probe_file(args.path)]
                else:
                    results = scan_directory(args.path, quiet)

                to_process = [
                    r
                    for r in results
                    if needs_processing(r) or has_extractable_subs(r) or needs_hdr10_copy(r)
                ]

                if not to_process:
                    log("No files need processing.", quiet)
                    return

                log(f"\nProcessing {len(to_process)} files...", quiet)

                for info in to_process:
                    result = process_file(
                        info,
                        dry_run=args.dry_run,
                        quiet=quiet,
                        use_hardware=use_hardware,
                    )
                    write_log_entry(result, LOG_DIR, prefix="recode")

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
        except LockAcquisitionError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
