"""Shared types and models for xbox-media-utils."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class AudioTrack:
    """Audio track information."""

    index: int
    codec: str
    channels: int = 0
    language: Optional[str] = None
    needs_recode: bool = False
    recode_reason: Optional[str] = None


@dataclass
class SubtitleTrack:
    """Subtitle track information."""

    index: int
    codec: str
    language: Optional[str] = None
    title: Optional[str] = None
    is_text: bool = False
    is_image: bool = False
    is_default: bool = False
    is_forced: bool = False


@dataclass
class MediaInfo:
    """Parsed media file information."""

    path: Path
    video_codec: Optional[str] = None
    video_bit_depth: Optional[int] = None
    video_hdr: bool = False
    video_hdr_type: Optional[str] = None
    video_width: Optional[int] = None
    video_height: Optional[int] = None
    audio_tracks: list = field(default_factory=list)
    needs_video_recode: bool = False
    video_recode_reason: Optional[str] = None
    probe_error: Optional[str] = None
    subtitle_tracks: list = field(default_factory=list)
    dovi_profile: Optional[int] = None
    has_dovi_profile_8: bool = False

    @property
    def needs_audio_recode(self) -> bool:
        return any(t.needs_recode for t in self.audio_tracks)

    @property
    def audio_recode_reason(self) -> Optional[str]:
        reasons = [t.recode_reason for t in self.audio_tracks if t.recode_reason]
        return "; ".join(reasons) if reasons else None
