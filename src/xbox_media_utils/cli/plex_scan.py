"""Plex library scanner CLI.

Triggers partial or full library scans via HTTP API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from xbox_media_utils.api import PlexAuthError, PlexScanner
from xbox_media_utils.core import DEFAULT_PLEX_URL


def main() -> int:
    """CLI entry point.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    parser = argparse.ArgumentParser(
        description="Trigger Plex library scans via HTTP API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Environment Variables:
  XBOX_PLEX_URL         Plex server URL (default: {DEFAULT_PLEX_URL})
  XBOX_PLEX_TOKEN       Plex auth token (or PLEX_TOKEN)
  XBOX_PLEX_PREFS_PATH  Path to Preferences.xml

Examples:
  %(prog)s /mnt/jbod/plex/movies/Some.Movie
      Partial scan of the Movies library for that path

  %(prog)s --sections 6 9 10
      Full scan of specified section keys

  %(prog)s --list
      Show all library sections and their paths
""",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Filesystem path to scan (partial scan)",
    )
    parser.add_argument(
        "--sections",
        "-s",
        nargs="+",
        type=int,
        help="Section key(s) to scan (full scan)",
    )
    parser.add_argument("--list", action="store_true", help="List library sections")

    args = parser.parse_args()

    if not args.path and not args.sections and not args.list:
        parser.print_help()
        return 1

    try:
        scanner = PlexScanner()
    except PlexAuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.list:
        sections = scanner.list_sections()
        for section in sections:
            locs = ", ".join(section["locations"])
            print(
                f"  key={section['key']}  type={section['type']}  "
                f"title={section['title']}  path={locs}"
            )
        return 0

    ok = True

    if args.path:
        result = scanner.scan_path(args.path)
        print(f"  [plex_scan] {result['message']}")
        if not result["success"]:
            ok = False

    if args.sections:
        results = scanner.scan_sections(args.sections)
        for _key, result in results.items():
            print(f"  [plex_scan] {result['message']}")
            if not result["success"]:
                ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())