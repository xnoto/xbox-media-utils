"""Dolby Vision HDR10 copy utilities."""

import shutil
from pathlib import Path
from typing import Optional

from .media import ffmpeg_path, run_cmd
from .models import MediaInfo


def needs_hdr10_copy(info: MediaInfo, existing_path: Optional[Path] = None) -> bool:
    """Check if file needs an HDR10-only copy created.

    Required for DoVi Profile 8 content (Xbox crashes on DoVi P8 MKV).
    """
    if not info.has_dovi_profile_8:
        return False
    if existing_path and existing_path.exists():
        return False
    return True


def create_hdr10_copy(
    info: MediaInfo, dest_dir: Path, logger=print
) -> tuple[bool, str, Optional[Path]]:
    """Create HDR10-only copy by stripping DoVi RPU metadata.

    DoVi Profile 8 has HDR10 base layer + DoVi RPU (NAL unit type 62).
    Removing NAL unit 62 leaves a valid HDR10 stream.

    Returns: (success, message, output_path)
    """
    if not info.has_dovi_profile_8:
        return False, "Not DoVi Profile 8", None

    output_name = info.path.stem + ".HDR10.mkv"
    output_path = dest_dir / output_name

    if output_path.exists():
        return True, "HDR10 copy already exists", output_path

    temp_path = output_path.with_suffix(".tmp.mkv")
    logger("      Creating HDR10 copy (stripping DoVi RPU)...")

    cmd = [
        ffmpeg_path(),
        "-y",
        "-v",
        "error",
        "-i",
        str(info.path),
        "-map",
        "0:v:0",  # First video stream only
        "-map",
        "0:a",  # All audio streams
        "-map",
        "0:s?",  # All subtitle streams (optional)
        "-c:v",
        "copy",
        "-bsf:v",
        "dovi_rpu=strip=1",  # Remove Dolby Vision RPU metadata
        "-c:a",
        "copy",
        "-c:s",
        "copy",
        str(temp_path),
    ]

    result = run_cmd(cmd)

    if result.returncode != 0:
        if temp_path.exists():
            temp_path.unlink()
        return (
            False,
            f"ffmpeg failed: {result.stderr[-200:] if result.stderr else 'unknown'}",
            None,
        )

    if not temp_path.exists():
        return False, "Output file not created", None

    output_size = temp_path.stat().st_size
    input_size = info.path.stat().st_size

    if output_size < input_size * 0.9:
        temp_path.unlink()
        return False, f"Output too small: {output_size} vs {input_size}", None

    try:
        temp_path.rename(output_path)
    except Exception as e:
        if temp_path.exists():
            temp_path.unlink()
        return False, f"Rename failed: {e}", None

    logger(f"      Created: {output_path.name}")
    return True, "HDR10 copy created", output_path


def get_dovi_archive_path(
    primary_path: Path,
    backup_root: Optional[Path] = None,
    library_root: Optional[Path] = None,
) -> Path:
    """Return where the original DoVi file should be archived."""
    if backup_root is None:
        return primary_path.with_suffix(".DV.mkv")

    try:
        relative_path = (
            primary_path.relative_to(library_root) if library_root else Path(primary_path.name)
        )
    except ValueError:
        relative_path = Path(primary_path.name)

    return backup_root / relative_path.with_suffix(".DV.mkv")


def archive_dovi_original(
    primary_path: Path,
    backup_root: Optional[Path] = None,
    library_root: Optional[Path] = None,
) -> tuple[bool, str, Optional[Path]]:
    """Move the original DoVi file out of the Plex library."""
    dv_path = get_dovi_archive_path(primary_path, backup_root, library_root)

    if not primary_path.exists():
        return False, "Primary file does not exist", None
    if dv_path.exists():
        return False, f"Archive path already exists: {dv_path}", None

    try:
        dv_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(primary_path), str(dv_path))
    except Exception as e:
        return False, f"Archive move failed: {e}", None

    return True, "DoVi original archived", dv_path


def copy_dovi_original_to_archive(
    source_path: Path,
    primary_path: Path,
    backup_root: Optional[Path] = None,
    library_root: Optional[Path] = None,
) -> tuple[bool, str, Optional[Path]]:
    """Copy an imported DoVi original to the archive location outside Plex."""
    dv_path = get_dovi_archive_path(primary_path, backup_root, library_root)

    if not source_path.exists():
        return False, "Source file does not exist", None
    if dv_path.exists():
        return False, f"Archive path already exists: {dv_path}", dv_path

    try:
        dv_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, dv_path)
    except Exception as e:
        return False, f"Archive copy failed: {e}", None

    return True, "DoVi original archived", dv_path


def promote_hdr10_copy(
    info: MediaInfo,
    hdr10_path: Path,
    backup_root: Optional[Path] = None,
    library_root: Optional[Path] = None,
) -> tuple[bool, str, Optional[Path]]:
    """Promote an HDR10 sidecar to the primary filename and archive the DoVi original."""
    if not hdr10_path.exists():
        return False, "HDR10 copy does not exist", None

    primary_path = info.path

    archive_success, archive_msg, dv_path = archive_dovi_original(
        primary_path, backup_root, library_root
    )
    if not archive_success:
        if archive_msg.startswith("Archive path already exists: "):
            dv_path = get_dovi_archive_path(primary_path, backup_root, library_root)
        else:
            return False, archive_msg, None

    try:
        try:
            if archive_success:
                shutil.move(str(hdr10_path), str(primary_path))
            else:
                hdr10_path.replace(primary_path)
        except Exception as e:
            if archive_success and dv_path:
                shutil.move(str(dv_path), str(primary_path))
            return False, f"Rename failed: {e}", None
    except Exception as e:
        return False, f"Rollback failed: {e}", None

    return True, "HDR10 copy promoted to primary", dv_path
