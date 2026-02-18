"""Media file probing and analysis utilities."""

import json
import subprocess
from pathlib import Path
from typing import Optional

from .constants import (
    COMPATIBLE_VIDEO_CODECS,
    IMAGE_SUBTITLE_CODECS,
    TEXT_SUBTITLE_CODECS,
)
from .models import AudioTrack, MediaInfo, SubtitleTrack


def run_cmd(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(cmd, capture_output=capture, text=True)


def detect_dovi_profile(filepath: Path) -> Optional[int]:
    """Detect Dolby Vision profile using ffprobe.

    Returns the profile number (e.g., 5, 7, 8) or None if not DoVi.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream_side_data=dv_profile",
        "-of",
        "json",
        str(filepath),
    ]
    result = run_cmd(cmd)
    if result.returncode != 0:
        return None

    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        for stream in streams:
            side_data_list = stream.get("side_data_list", [])
            for sd in side_data_list:
                if "dv_profile" in sd:
                    return int(sd["dv_profile"])
    except (json.JSONDecodeError, ValueError, KeyError):
        pass

    # Fallback: check with mediainfo if available
    try:
        result = run_cmd(["mediainfo", "--Output=Video;%HDR_Format_Profile%", str(filepath)])
        if result.returncode == 0 and result.stdout.strip():
            profile_str = result.stdout.strip()
            if "08" in profile_str or ".8" in profile_str:
                return 8
            elif "05" in profile_str or ".5" in profile_str:
                return 5
            elif "07" in profile_str or ".7" in profile_str:
                return 7
    except FileNotFoundError:
        pass

    return None


def probe_file(filepath: Path) -> MediaInfo:
    """Probe media file with ffprobe and return MediaInfo."""
    info = MediaInfo(path=filepath)

    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(filepath),
    ]

    result = run_cmd(cmd)
    if result.returncode != 0:
        info.probe_error = result.stderr or "ffprobe failed"
        return info

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        info.probe_error = f"JSON parse error: {e}"
        return info

    streams = data.get("streams", [])

    # Find video stream
    for stream in streams:
        if stream.get("codec_type") == "video":
            info.video_codec = stream.get("codec_name", "").lower()
            info.video_width = stream.get("width")
            info.video_height = stream.get("height")

            # Bit depth detection
            pix_fmt = stream.get("pix_fmt", "")
            if "10le" in pix_fmt or "10be" in pix_fmt or "p010" in pix_fmt:
                info.video_bit_depth = 10
            elif "12le" in pix_fmt or "12be" in pix_fmt:
                info.video_bit_depth = 12
            else:
                info.video_bit_depth = 8

            # HDR detection
            side_data = stream.get("side_data_list", [])
            for sd in side_data:
                sd_type = sd.get("side_data_type", "").lower()
                if "mastering" in sd_type or "content light" in sd_type:
                    info.video_hdr = True
                if "dovi" in sd_type or "dolby" in sd_type:
                    info.video_hdr_type = "dolby vision"
                    info.video_hdr = True
                # Check for DoVi profile in side data
                if "dv_profile" in sd:
                    info.dovi_profile = int(sd["dv_profile"])

            color_transfer = stream.get("color_transfer", "").lower()
            color_primaries = stream.get("color_primaries", "").lower()
            if "smpte2084" in color_transfer or "arib-std-b67" in color_transfer:
                info.video_hdr = True
                info.video_hdr_type = "hlg" if "arib-std-b67" in color_transfer else "hdr10"
            if "bt2020" in color_primaries:
                info.video_hdr = True
            break

    # Find all audio streams
    for stream in streams:
        if stream.get("codec_type") == "audio":
            tags = stream.get("tags", {})
            info.audio_tracks.append(
                AudioTrack(
                    index=stream.get("index", 0),
                    codec=stream.get("codec_name", "").lower(),
                    channels=stream.get("channels", 0),
                    language=tags.get("language", "und"),
                )
            )

    # Find subtitle streams
    for stream in streams:
        if stream.get("codec_type") == "subtitle":
            codec = stream.get("codec_name", "").lower()
            tags = stream.get("tags", {})
            disposition = stream.get("disposition", {})

            sub = SubtitleTrack(
                index=stream.get("index", 0),
                codec=codec,
                language=tags.get("language", "und"),
                title=tags.get("title"),
                is_text=codec in TEXT_SUBTITLE_CODECS,
                is_image=codec in IMAGE_SUBTITLE_CODECS,
                is_default=disposition.get("default", 0) == 1,
                is_forced=disposition.get("forced", 0) == 1,
            )
            info.subtitle_tracks.append(sub)

    # DoVi profile detection (if not already found in stream side_data)
    if info.dovi_profile is None and info.video_hdr_type == "dolby vision":
        info.dovi_profile = detect_dovi_profile(filepath)

    # Check for problematic DoVi Profile 8
    if info.dovi_profile == 8:
        info.has_dovi_profile_8 = True

    # Determine recode needs
    analyze_recode_needs(info)

    return info


def analyze_recode_needs(info: MediaInfo) -> None:
    """Determine if video/audio need recoding (per-track for audio)."""
    if info.video_codec and info.video_codec not in COMPATIBLE_VIDEO_CODECS:
        info.needs_video_recode = True
        info.video_recode_reason = f"incompatible codec: {info.video_codec}"

    for track in info.audio_tracks:
        if track.channels > 2:
            ch_label = (
                f"{track.channels - 1}.1" if track.channels in (6, 8) else f"{track.channels}ch"
            )
            track.needs_recode = True
            track.recode_reason = f"{track.codec} {ch_label} -> AAC stereo"


def needs_processing(info: MediaInfo) -> bool:
    """Check if file needs any processing."""
    return info.needs_video_recode or info.needs_audio_recode


def has_extractable_subs(info: MediaInfo) -> bool:
    """Check if file has any subtitles to extract (text or image-based)."""
    return any(sub.is_text or sub.is_image for sub in info.subtitle_tracks)


def is_sample_file(filepath: Path) -> bool:
    """Check if file is a sample file that should be skipped.

    Sample files are typically truncated clips that fail validation.
    """
    name_lower = filepath.name.lower()
    # Check filename patterns
    if "sample" in name_lower:
        return True
    # Check if in a Sample directory
    return any(part.lower() == "sample" for part in filepath.parts)


def can_use_vaapi(info: MediaInfo, use_hardware: bool = True) -> bool:
    """Check if VAAPI hardware encoding can be used."""
    from .constants import VAAPI_INCOMPATIBLE_CODECS

    if not use_hardware or not info.needs_video_recode:
        return False
    # VAAPI cannot encode 10-bit (Radeon VII only supports HEVC Main/8-bit)
    if info.video_bit_depth and info.video_bit_depth > 8:
        return False
    # VAAPI cannot decode certain codecs (MPEG-4/XviD causes hwaccel init failure)
    if info.video_codec in VAAPI_INCOMPATIBLE_CODECS:
        return False
    return True
