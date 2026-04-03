"""Plex API client for triggering library scans."""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from xbox_media_utils.core.config import DEFAULT_PLEX_URL, DEFAULT_PREFS_PATH


class PlexError(Exception):
    """Base exception for Plex API errors."""

    pass


class PlexAuthError(PlexError):
    """Raised when authentication fails."""

    pass


class PlexConnectionError(PlexError):
    """Raised when connection to Plex fails."""

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
            PlexAuthError: If no token can be resolved.
        """
        self.base_url = base_url.rstrip("/")
        self.token = token or self._resolve_token()
        if not self.token:
            raise PlexAuthError(
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
            PlexError: On HTTP errors.
            PlexConnectionError: On connection failures.
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
            raise PlexError(f"Plex API HTTP {e.code}: {path}") from e
        except URLError as e:
            raise PlexConnectionError(f"Plex API unreachable: {e.reason}") from e

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

    def scan_path(self, target: Path) -> dict:
        """Trigger partial scan on library section containing target.

        Args:
            target: Filesystem path to scan.

        Returns:
            Dict with 'success', 'section', and 'message' keys.
        """
        section = self._resolve_section_for_path(target)
        if not section:
            return {
                "success": False,
                "section": None,
                "message": f"No library section found for: {target}",
            }

        key = section["key"]
        title = section["title"]
        target_resolved = str(target.resolve())

        # Partial scan: pass the specific path (URL-encoded for spaces etc.)
        encoded_path = quote(target_resolved, safe="")
        path = f"/library/sections/{key}/refresh?path={encoded_path}"
        try:
            self._api_get(path)
            return {
                "success": True,
                "section": {"key": key, "title": title},
                "message": f"Triggered partial scan: {title} (section {key}) -> {target}",
            }
        except PlexError as e:
            return {
                "success": False,
                "section": {"key": key, "title": title},
                "message": str(e),
            }

    def scan_sections(self, keys: list[int]) -> dict[int, dict]:
        """Trigger full scan on specified section keys.

        Args:
            keys: List of section keys to scan.

        Returns:
            Dict mapping section key to result dict with 'success' and 'message'.
        """
        # Validate keys exist
        valid_keys = {int(s["key"]) for s in self._get_sections()}
        results = {}

        for key in keys:
            if key not in valid_keys:
                results[key] = {
                    "success": False,
                    "message": f"Section {key} does not exist",
                }
                continue

            title = next(
                (s["title"] for s in self._get_sections() if int(s["key"]) == key),
                f"section {key}",
            )
            try:
                self._api_get(f"/library/sections/{key}/refresh")
                results[key] = {
                    "success": True,
                    "message": f"Triggered scan: {title} (section {key})",
                }
            except PlexError as e:
                results[key] = {
                    "success": False,
                    "message": str(e),
                }

        return results

    def list_sections(self) -> list[dict]:
        """Get all library sections.

        Returns:
            List of section dicts with key, type, title, and locations.
        """
        sections = []
        for section in self._get_sections():
            locs = [loc["path"] for loc in section.get("Location", [])]
            sections.append(
                {
                    "key": section["key"],
                    "type": section["type"],
                    "title": section["title"],
                    "locations": locs,
                }
            )
        return sections
