"""Media file probing and analysis utilities."""

import json
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .constants import (
    AUDIO_CODECS_REQUIRING_RECODE,
    COMPATIBLE_VIDEO_CODECS,
    H264_MAX_HEIGHT,
    H264_MAX_WIDTH,
    IMAGE_SUBTITLE_CODECS,
    SUPPORTED_H264_PROFILES,
    SUPPORTED_HEVC_PROFILES,
    TEXT_SUBTITLE_CODECS,
    UHD_MAX_HEIGHT,
    UHD_MAX_WIDTH,
    XBOX_MAX_VIDEO_FPS,
)
from .models import AudioTrack, MediaInfo, SubtitleTrack


@lru_cache(maxsize=1)
def _static_binaries() -> tuple[str, str]:
    """Return (ffmpeg, ffprobe) paths from the static-ffmpeg package.

    Downloads binaries on first call; cached thereafter.
    """
    from static_ffmpeg import run

    ffmpeg, ffprobe = run.get_or_fetch_platform_executables_else_raise()
    return ffmpeg, ffprobe


def ffmpeg_path() -> str:
    """Return the path to a statically-linked ffmpeg binary."""
    return _static_binaries()[0]


def ffprobe_path() -> str:
    """Return the path to a statically-linked ffprobe binary."""
    return _static_binaries()[1]


def run_cmd(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    """Run a command and return result."""
    return subprocess.run(cmd, capture_output=capture, text=True)


def parse_frame_rate(value: Optional[str]) -> Optional[float]:
    """Parse an ffprobe frame-rate fraction such as ``24000/1001``."""
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        numerator, denominator = value.split("/", 1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            return None
        return float(numerator) / denominator_value
    except (TypeError, ValueError):
        return None


def fits_oriented_resolution(
    width: Optional[int],
    height: Optional[int],
    max_width: int,
    max_height: int,
) -> bool:
    """Return whether dimensions fit a landscape limit in either orientation."""
    if not width or not height:
        return True
    return max(width, height) <= max_width and min(width, height) <= max_height


def exceeds_uhd_resolution(info: MediaInfo) -> bool:
    """Return whether a video exceeds the Xbox 4K media-app limit."""
    return not fits_oriented_resolution(
        info.video_width,
        info.video_height,
        UHD_MAX_WIDTH,
        UHD_MAX_HEIGHT,
    )


def exceeds_xbox_frame_rate(info: MediaInfo) -> bool:
    """Return whether a video exceeds the Xbox media-app frame-rate limit."""
    return bool(info.video_frame_rate and info.video_frame_rate > XBOX_MAX_VIDEO_FPS + 0.01)


def has_unsupported_chroma(info: MediaInfo) -> bool:
    """Return whether a known pixel format is outside Main/Main10 4:2:0."""
    if not info.video_pixel_format:
        return False
    return "422" in info.video_pixel_format or "444" in info.video_pixel_format


def detect_dovi_profile(filepath: Path) -> Optional[int]:
    """Detect Dolby Vision profile using ffprobe.

    Returns the profile number (e.g., 5, 7, 8) or None if not DoVi.
    """
    cmd = [
        ffprobe_path(),
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
        ffprobe_path(),
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
            info.video_profile = (stream.get("profile") or "").lower() or None
            info.video_pixel_format = stream.get("pix_fmt") or None
            info.video_width = stream.get("width")
            info.video_height = stream.get("height")
            info.video_frame_rate = parse_frame_rate(
                stream.get("avg_frame_rate")
            ) or parse_frame_rate(stream.get("r_frame_rate"))

            # Bit depth detection
            pix_fmt = stream.get("pix_fmt", "")
            bits_per_raw_sample = stream.get("bits_per_raw_sample")
            if bits_per_raw_sample and str(bits_per_raw_sample).isdigit():
                info.video_bit_depth = int(bits_per_raw_sample)
            elif "10le" in pix_fmt or "10be" in pix_fmt or "p010" in pix_fmt:
                info.video_bit_depth = 10
            elif "12le" in pix_fmt or "12be" in pix_fmt:
                info.video_bit_depth = 12
            elif pix_fmt:
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
                if info.video_hdr_type != "dolby vision":
                    info.video_hdr_type = "hlg" if "arib-std-b67" in color_transfer else "hdr10"
            if "bt2020" in color_primaries:
                info.video_hdr = True
            break

    # Find all audio streams
    for stream in streams:
        if stream.get("codec_type") == "audio":
            tags = stream.get("tags", {})
            disposition = stream.get("disposition", {})
            info.audio_tracks.append(
                AudioTrack(
                    index=stream.get("index", 0),
                    codec=stream.get("codec_name", "").lower(),
                    channels=stream.get("channels", 0),
                    language=tags.get("language", "und"),
                    title=tags.get("title"),
                    is_default=disposition.get("default", 0) == 1,
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
    if info.dovi_profile is not None:
        info.video_hdr = True
        info.video_hdr_type = "dolby vision"
    if info.dovi_profile == 8:
        info.has_dovi_profile_8 = True

    # Determine recode needs
    analyze_recode_needs(info)

    return info


def analyze_recode_needs(info: MediaInfo) -> None:
    """Determine if video/audio need recoding (per-track for audio)."""
    if info.video_hdr_type == "dolby vision" or info.dovi_profile is not None:
        info.video_hdr = True
        info.video_hdr_type = "dolby vision"
        info.needs_video_recode = True
        if info.dovi_profile is not None:
            info.video_recode_reason = (
                f"Dolby Vision Profile {info.dovi_profile} is incompatible with Plex on Xbox"
            )
            # Profile 8 has an HDR10-compatible base layer (BT.2020/PQ) that the
            # current zscale tonemap pipeline can correctly convert to BT.709.
            # Other profiles (4/5/7) carry a non-HDR10-compatible BL — e.g. P5's
            # IPT-PQ-C2 — which cannot be tonemapped without applying the DV RPU
            # reshaping. That requires libdovi, which the bundled ffmpeg lacks.
            if info.dovi_profile != 8:
                info.incompatible_reason = (
                    f"Dolby Vision Profile {info.dovi_profile} cannot be tonemapped "
                    "to BT.709 with the current pipeline (libdovi required)"
                )
        else:
            info.video_recode_reason = "Dolby Vision is incompatible with Plex on Xbox"
            info.incompatible_reason = "Unknown Dolby Vision profile cannot be safely tonemapped"
    elif info.video_codec and info.video_codec not in COMPATIBLE_VIDEO_CODECS:
        info.needs_video_recode = True
        info.video_recode_reason = f"incompatible codec: {info.video_codec}"
    elif info.video_codec == "h264" and (
        not fits_oriented_resolution(
            info.video_width,
            info.video_height,
            H264_MAX_WIDTH,
            H264_MAX_HEIGHT,
        )
        or exceeds_xbox_frame_rate(info)
        or (info.video_profile is not None and info.video_profile not in SUPPORTED_H264_PROFILES)
        or bool(info.video_bit_depth and info.video_bit_depth > 8)
        or has_unsupported_chroma(info)
    ):
        details = []
        if not fits_oriented_resolution(
            info.video_width,
            info.video_height,
            H264_MAX_WIDTH,
            H264_MAX_HEIGHT,
        ):
            details.append(f"{info.video_width}x{info.video_height}")
        if exceeds_xbox_frame_rate(info):
            details.append(f"{info.video_frame_rate:.3f} fps")
        if info.video_profile and info.video_profile not in SUPPORTED_H264_PROFILES:
            details.append(f"{info.video_profile} profile")
        if info.video_bit_depth and info.video_bit_depth > 8:
            details.append(f"{info.video_bit_depth}-bit")
        if has_unsupported_chroma(info):
            details.append(info.video_pixel_format or "unsupported chroma")
        info.needs_video_recode = True
        info.video_recode_reason = f"H.264 {'/'.join(details)} exceeds Xbox H.264 1080p60 support"
    elif info.video_codec == "hevc" and (
        exceeds_uhd_resolution(info)
        or exceeds_xbox_frame_rate(info)
        or (info.video_profile is not None and info.video_profile not in SUPPORTED_HEVC_PROFILES)
        or bool(info.video_bit_depth and info.video_bit_depth > 10)
        or has_unsupported_chroma(info)
    ):
        info.needs_video_recode = True
        info.video_recode_reason = "HEVC exceeds Xbox Main/Main10 4K60 support"
    elif info.video_codec == "vp9" and (
        exceeds_uhd_resolution(info)
        or exceeds_xbox_frame_rate(info)
        or bool(info.video_bit_depth and info.video_bit_depth > 10)
        or has_unsupported_chroma(info)
    ):
        info.needs_video_recode = True
        info.video_recode_reason = "VP9 exceeds Xbox 4K60 support"
    elif info.video_bit_depth and info.video_bit_depth >= 10 and not info.video_hdr:
        # 10-bit SDR HEVC (BT.709) crashes the Plex Xbox client on direct-play.
        # The Xbox HEVC HW decoder handles 10-bit fine for HDR content (BT.2020/PQ),
        # but the Plex client app's renderer faults on the 10-bit-without-HDR combo
        # produced by some 4K SDR releases (e.g. AOC-style hybrids).
        info.needs_video_recode = True
        info.video_recode_reason = (
            f"{info.video_bit_depth}-bit SDR {info.video_codec} crashes Plex on Xbox"
        )

    for track in info.audio_tracks:
        if track.codec in AUDIO_CODECS_REQUIRING_RECODE:
            track.needs_recode = True
            track.recode_reason = f"incompatible codec: {track.codec} -> AAC stereo"
        elif track.channels > 2:
            ch_label = (
                f"{track.channels - 1}.1" if track.channels in (6, 8) else f"{track.channels}ch"
            )
            track.needs_recode = True
            track.recode_reason = f"{track.codec} {ch_label} -> AAC stereo"
        elif track.channels == 1:
            track.needs_recode = True
            track.recode_reason = "mono track -> AAC stereo"


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
    if info.video_hdr_type == "dolby vision":
        return False
    # Scaling and frame-rate limiting use software filters. Keeping them off the
    # VAAPI path also avoids feeding hardware frames through CPU-only filters.
    if exceeds_uhd_resolution(info) or exceeds_xbox_frame_rate(info):
        return False
    # VAAPI cannot encode 10-bit (Radeon VII only supports HEVC Main/8-bit)
    if info.video_bit_depth and info.video_bit_depth > 8:
        return False
    # VAAPI cannot decode certain codecs (MPEG-4/XviD causes hwaccel init failure)
    if info.video_codec in VAAPI_INCOMPATIBLE_CODECS:
        return False
    return True
