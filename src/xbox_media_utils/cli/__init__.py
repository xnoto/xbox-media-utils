"""CLI entry points for xbox-media-utils."""

from xbox_media_utils.cli.import_ import main as import_main
from xbox_media_utils.cli.plex_scan import main as plex_scan_main
from xbox_media_utils.cli.recode import main as recode_main

__all__ = [
    "recode_main",
    "import_main",
    "plex_scan_main",
]
