"""Dolby Vision HDR10 copy utilities."""

from pathlib import Path
from typing import Optional

from .media import run_cmd
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
        "ffmpeg",
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
        "filter_units=remove_types=62",  # Remove DoVi RPU NAL units
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
