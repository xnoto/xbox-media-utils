"""Plex library scanner via HTTP API.

Triggers partial or full library scans and resolves filesystem paths
to the correct Plex library section automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

# Environment variable configuration (following XBOX_* convention)
DEFAULT_PLEX_URL = os.environ.get("XBOX_PLEX_URL", "http://localhost:32400")
DEFAULT_PREFS_PATH = os.environ.get(
    "XBOX_PLEX_PREFS_PATH",
    "/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Preferences.xml",
)


class PlexScanError(Exception):
    """Raised when Plex scan operations fail."""

    pass


class PlexScanner:
    """Client for triggering Plex library scans via HTTP API."""

    def __init__(self, token: Optional[str] = None, base_url: str = DEFAULT_PLEX_URL):
        """Initialize scanner with optional token and base URL.

        Args:
            token: Plex authentication token. If not provided, attempts to
                   resolve from XBOX_PLEX_TOKEN, PLEX_TOKEN env vars, or
                   Preferences.xml file.
            base_url: Plex server URL (default from XBOX_PLEX_URL env var).

        Raises:
            PlexScanError: If no token can be resolved.
        """
        self.base_url = base_url.rstrip("/")
        self.token = token or self._resolve_token()
        if not self.token:
            raise PlexScanError(
                "No Plex token found. Set XBOX_PLEX_TOKEN or PLEX_TOKEN env var, "
                "or ensure Preferences.xml is readable."
            )
        self._sections: Optional[list[dict]] = None

    @staticmethod
    def _resolve_token() -> Optional[str]:
        """Resolve token from environment or Preferences.xml.

        Resolution order:
        1. XBOX_PLEX_TOKEN environment variable
        2. PLEX_TOKEN environment variable (for backwards compatibility)
        3. Preferences.xml on disk
        """
        for env_var in ("XBOX_PLEX_TOKEN", "PLEX_TOKEN"):
            env_token = os.environ.get(env_var)
            if env_token:
                return env_token

        prefs_path = Path(DEFAULT_PREFS_PATH)
        if not prefs_path.exists():
            return None
        try:
            tree = ET.parse(prefs_path)
            return tree.getroot().get("PlexOnlineToken")
        except (ET.ParseError, PermissionError, OSError):
            return None

    def _api_get(self, path: str) -> Optional[dict]:
        """GET request to Plex API.

        Args:
            path: API endpoint path (e.g., "/library/sections").

        Returns:
            Parsed JSON response or None for empty responses.

        Raises:
            PlexScanError: On HTTP errors or connection failures.
        """
        url = f"{self.base_url}{path}"
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}X-Plex-Token={self.token}"
        req = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=10) as resp:
                body = resp.read()
                if not body or not body.strip():
                    return None
                return json.loads(body)
        except HTTPError as e:
            raise PlexScanError(f"Plex API HTTP {e.code}: {path}") from e
        except URLError as e:
            raise PlexScanError(f"Plex API unreachable: {e.reason}") from e

    def _get_sections(self) -> list[dict]:
        """Fetch and cache library sections."""
        if self._sections is None:
            data = self._api_get("/library/sections")
            if data:
                self._sections = data.get("MediaContainer", {}).get("Directory", [])
            else:
                self._sections = []
        return self._sections

    def _resolve_section_for_path(self, target: Path) -> Optional[dict]:
        """Find library section containing the target path.

        Args:
            target: Filesystem path to locate.

        Returns:
            Section dict with longest matching path prefix, or None.
        """
        target_str = str(target.resolve())
        best_match = None
        best_len = 0

        for section in self._get_sections():
            for loc in section.get("Location", []):
                loc_path = loc["path"]
                # target must be under (or equal to) this location
                if target_str == loc_path or target_str.startswith(loc_path + "/"):
                    if len(loc_path) > best_len:
                        best_len = len(loc_path)
                        best_match = section
        return best_match

    def scan_path(self, target: Path) -> bool:
        """Trigger partial scan on library section containing target.

        Args:
            target: Filesystem path to scan.

        Returns:
            True if scan was triggered, False if no matching section.
        """
        section = self._resolve_section_for_path(target)
        if not section:
            print(f"  [plex_scan] No library section found for: {target}")
            return False

        key = section["key"]
        title = section["title"]
        target_resolved = str(target.resolve())

        # Partial scan: pass the specific path (URL-encoded for spaces etc.)
        encoded_path = quote(target_resolved, safe="")
        path = f"/library/sections/{key}/refresh?path={encoded_path}"
        try:
            self._api_get(path)
            print(f"  [plex_scan] Triggered partial scan: {title} (section {key}) -> {target}")
            return True
        except PlexScanError as e:
            print(f"  [plex_scan] Scan failed for {title}: {e}")
            return False

    def scan_sections(self, keys: list[int]) -> dict[int, bool]:
        """Trigger full scan on specified section keys.

        Args:
            keys: List of section keys to scan.

        Returns:
            Dict mapping section key to success boolean.
        """
        # Validate keys exist
        valid_keys = {int(s["key"]) for s in self._get_sections()}
        results = {}

        for key in keys:
            if key not in valid_keys:
                print(f"  [plex_scan] Section {key} does not exist, skipping")
                results[key] = False
                continue

            title = next(
                (s["title"] for s in self._get_sections() if int(s["key"]) == key),
                f"section {key}",
            )
            try:
                self._api_get(f"/library/sections/{key}/refresh")
                print(f"  [plex_scan] Triggered scan: {title} (section {key})")
                results[key] = True
            except PlexScanError as e:
                print(f"  [plex_scan] Scan failed for {title}: {e}")
                results[key] = False

        return results

    def list_sections(self) -> None:
        """Print all library sections for diagnostics."""
        for section in self._get_sections():
            locs = ", ".join(loc["path"] for loc in section.get("Location", []))
            print(
                f"  key={section['key']}  type={section['type']}  "
                f"title={section['title']}  path={locs}"
            )


def main() -> int:
    """CLI entry point for Plex scanner.

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
  XBOX_PLEX_PREFS_PATH  Path to Preferences.xml (default: {DEFAULT_PREFS_PATH})

Examples:
  %(prog)s /mnt/jbod/plex/movies/Some.Movie
      Partial scan of the Movies library for that path

  %(prog)s --sections 6 9 10
      Full scan of specified section keys

  %(prog)s --list
      Show all library sections and their paths
""",
    )
    parser.add_argument("path", nargs="?", type=Path, help="Filesystem path to scan (partial scan)")
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
    except PlexScanError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.list:
        scanner.list_sections()
        return 0

    ok = True

    if args.path:
        if not scanner.scan_path(args.path):
            ok = False

    if args.sections:
        results = scanner.scan_sections(args.sections)
        if not all(results.values()):
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
