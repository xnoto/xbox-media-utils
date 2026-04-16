"""Xbox Series X Media Importer CLI.

Imports new media files to Plex library with Xbox Series X compatibility.
Unlike recode (which processes in-place), this COPIES from source to destination.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

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
    get_plex_root,
    write_log_entry,
)
from xbox_media_utils.ffmpeg import run_ffmpeg_with_fallback, validate_output
from xbox_media_utils.files import collect_media_files, set_ownership
from xbox_media_utils.hdr import create_hdr10_copy, needs_hdr10_copy
from xbox_media_utils.media import has_extractable_subs, needs_processing, probe_file
from xbox_media_utils.subtitles import extract_subtitles


def import_file(
    info,
    dest_dir: Path,
    plex_root: Path,
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
        "error": None,
    }

    output_name = info.path.stem + ".mkv" if needs_processing(info) else info.path.name
    dest_path = dest_dir / output_name
    result["destination"] = str(dest_path)

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
            result["dovi_action"] = f"create HDR10 copy (DoVi Profile {info.dovi_profile})"
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
            str(temp_path),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode != 0:
            result["status"] = "failed"
            result["error"] = proc.stderr[-500:] if proc.stderr else "Remux failed"
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
    add_dry_run_argument(parser)
    add_no_hardware_argument(parser)

    args = parser.parse_args()

    validate_path_exists(args.source)

    use_hardware = not args.no_hardware

    plex_root = get_plex_root(args.plex)
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
        dest_base = library_path

    files = collect_media_files(args.source, MEDIA_EXTENSIONS)

    if not files:
        print(f"No media files found in: {args.source}")
        sys.exit(1)

    print(f"Source: {args.source}")
    print(f"Library: {library_name} ({library_path})")
    print(f"Destination: {dest_base}")
    print(f"Files: {len(files)}")
    print()

    success = failed = 0

    for idx, filepath in enumerate(files):
        if filepath.name.endswith(".HDR10.mkv"):
            continue

        if source_is_dir:
            rel_path = filepath.relative_to(args.source)
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
            info, dest_dir, plex_root, dry_run=args.dry_run, use_hardware=use_hardware
        )
        result["timestamp"] = datetime.now().isoformat()
        if not args.dry_run:
            write_log_entry(result, IMPORT_LOG_DIR, prefix="import")

        if result["status"] == "success":
            print(f"    -> {result['action']}: {result['destination']}")
            success += 1
        elif result["status"] == "would_import":
            print(f"    -> Would {result['action']}: {result['destination']}")
            if result.get("dovi_action"):
                print(f"      + {result['dovi_action']}")
            success += 1
        else:
            print(f"    X Failed: {result.get('error')}")
            failed += 1

    print()
    print(f"Complete: {success} succeeded, {failed} failed")
    print(f"Source preserved at: {args.source}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
