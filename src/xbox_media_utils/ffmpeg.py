"""FFmpeg command building and execution utilities."""

import json
import subprocess
from pathlib import Path

from .media import run_cmd
from .models import MediaInfo

# Encoding settings
CRF_QUALITY = 16
VAAPI_QP = 18
HEVC_PRESET = "slow"

# Audio downmix filter
DOWNMIX_FILTER = (
    "pan=stereo|FL=0.5*FC+0.707*FL+0.707*BL+0.5*LFE|FR=0.5*FC+0.707*FR+0.707*BR+0.5*LFE"
)


def build_ffmpeg_cmd(info: MediaInfo, output_path: Path, use_vaapi: bool = True) -> list[str]:
    """Build ffmpeg command for transcoding."""
    cmd = ["ffmpeg"]

    if info.needs_video_recode and use_vaapi:
        # Use GPU for both decode and encode to reduce CPU load
        cmd.extend(
            [
                "-hwaccel",
                "vaapi",
                "-hwaccel_output_format",
                "vaapi",
                "-vaapi_device",
                "/dev/dri/renderD128",
            ]
        )

    cmd.extend(["-i", str(info.path)])

    # Map video and ALL audio tracks (subtitles extracted separately)
    cmd.extend(["-map", "0:v:0", "-map", "0:a"])

    # Video handling
    if info.needs_video_recode:
        if use_vaapi:
            cmd.extend(
                [
                    "-c:v",
                    "hevc_vaapi",
                    "-qp",
                    str(VAAPI_QP),
                    "-tag:v",
                    "hvc1",
                ]
            )
        else:
            x265_params = ["hdr-opt=1", "repeat-headers=1"] if info.video_hdr else []
            cmd.extend(
                [
                    "-c:v",
                    "libx265",
                    "-crf",
                    str(CRF_QUALITY),
                    "-preset",
                    HEVC_PRESET,
                    "-tag:v",
                    "hvc1",
                ]
            )
            if info.video_bit_depth and info.video_bit_depth >= 10:
                cmd.extend(["-pix_fmt", "yuv420p10le"])
                x265_params.append("profile=main10")
            if x265_params:
                cmd.extend(["-x265-params", ":".join(x265_params)])
    else:
        cmd.extend(["-c:v", "copy"])

    # Audio handling — per-track codec decisions
    for i, track in enumerate(info.audio_tracks):
        if track.needs_recode:
            cmd.extend([f"-c:a:{i}", "aac", f"-ac:a:{i}", "2", f"-b:a:{i}", "256k"])
            cmd.extend([f"-filter:a:{i}", DOWNMIX_FILTER])
        else:
            cmd.extend([f"-c:a:{i}", "copy"])

    # No embedded subtitles - they're extracted to sidecar files
    cmd.extend(["-sn"])

    # Increase muxing queue size for high-bitrate REMUXes
    cmd.extend(["-max_muxing_queue_size", "65536"])

    cmd.extend(["-y", str(output_path)])
    return cmd


def validate_output(input_info: MediaInfo, output_path: Path) -> tuple[bool, str]:
    """Validate output file is complete."""
    if not output_path.exists():
        return False, "Output file does not exist"

    output_size = output_path.stat().st_size
    input_size = input_info.path.stat().st_size

    if output_size < input_size * 0.1:
        return False, f"Output too small: {output_size} vs {input_size}"

    # Duration check
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json"]
    result_out = run_cmd(cmd + [str(output_path)])
    result_in = run_cmd(cmd + [str(input_info.path)])

    try:
        out_dur = float(json.loads(result_out.stdout).get("format", {}).get("duration", 0))
        in_dur = float(json.loads(result_in.stdout).get("format", {}).get("duration", 0))
        if in_dur > 0 and out_dur > 0:
            diff = abs(out_dur - in_dur) / in_dur
            if diff > 0.01:
                return False, f"Duration mismatch: {in_dur:.1f}s vs {out_dur:.1f}s"
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Stream check
    result_streams = run_cmd(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(output_path),
        ]
    )
    try:
        streams = json.loads(result_streams.stdout).get("streams", [])
        types = [s.get("codec_type") for s in streams]
        if "video" not in types:
            return False, "Output missing video stream"
        if "audio" not in types and input_info.audio_tracks:
            return False, "Output missing audio stream"
    except (json.JSONDecodeError, ValueError):
        pass

    return True, "OK"


def run_ffmpeg_with_fallback(
    info: MediaInfo, output_path: Path, use_hardware: bool = True
) -> tuple[bool, str]:
    """Run ffmpeg with VAAPI hardware acceleration, falling back to software if needed.

    Returns: (success, error_message)
    """
    from .media import can_use_vaapi

    # First attempt: use VAAPI if eligible
    use_vaapi = can_use_vaapi(info, use_hardware)

    if use_vaapi:
        print("  Attempting VAAPI hardware transcode...")
        cmd = build_ffmpeg_cmd(info, output_path, use_vaapi=True)
        proc = subprocess.run(cmd, capture_output=True, text=True)

        if proc.returncode == 0:
            return True, ""

        # Check if error is VAAPI-related (hwaccel init failure)
        stderr = proc.stderr.lower() if proc.stderr else ""
        vaapi_errors = [
            "failed setup for format vaapi",
            "hwaccel initialisation returned error",
            "impossible to convert between the formats",
            "error reinitializing filters",
            "failed to inject frame into filter network",
        ]

        if any(err in stderr for err in vaapi_errors):
            print("  VAAPI failed, falling back to software decode...")
            if output_path.exists():
                output_path.unlink()
        else:
            # Non-VAAPI error, don't retry
            return False, proc.stderr

    # Second attempt: software decode/encode (no hwaccel)
    print("  Using software transcode...")
    cmd = build_ffmpeg_cmd(info, output_path, use_vaapi=False)
    proc = subprocess.run(cmd, capture_output=True, text=True)

    if proc.returncode == 0:
        return True, ""
    else:
        return False, proc.stderr
