"""Tests for api.plex module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from xbox_media_utils.api.plex import (
    PlexAuthError,
    PlexConnectionError,
    PlexError,
    PlexScanner,
)


class TestPlexScannerInit:
    """Test suite for PlexScanner initialization."""

    def test_init_with_explicit_token(self):
        """Should accept explicit token."""
        scanner = PlexScanner(token="test_token")

        assert scanner.token == "test_token"

    def test_init_resolves_token_from_env_xbox_plex_token(self, monkeypatch):
        """Should resolve token from XBOX_PLEX_TOKEN env var."""
        monkeypatch.setenv("XBOX_PLEX_TOKEN", "xbox_token")

        scanner = PlexScanner()

        assert scanner.token == "xbox_token"

    def test_init_resolves_token_from_env_plex_token(self, monkeypatch):
        """Should resolve token from PLEX_TOKEN env var."""
        monkeypatch.delenv("XBOX_PLEX_TOKEN", raising=False)
        monkeypatch.setenv("PLEX_TOKEN", "plex_token")

        scanner = PlexScanner()

        assert scanner.token == "plex_token"

    def test_init_raises_without_token(self, monkeypatch, tmp_path):
        """Should raise PlexAuthError when no token available."""
        monkeypatch.delenv("XBOX_PLEX_TOKEN", raising=False)
        monkeypatch.delenv("PLEX_TOKEN", raising=False)
        # Point to non-existent prefs file
        monkeypatch.setattr(
            "xbox_media_utils.api.plex.DEFAULT_PREFS_PATH",
            str(tmp_path / "nonexistent.xml"),
        )

        with pytest.raises(PlexAuthError) as exc_info:
            PlexScanner()

        assert "No Plex token found" in str(exc_info.value)

    def test_init_reads_token_from_preferences_xml(self, monkeypatch, tmp_path):
        """Should read token from Preferences.xml."""
        monkeypatch.delenv("XBOX_PLEX_TOKEN", raising=False)
        monkeypatch.delenv("PLEX_TOKEN", raising=False)

        prefs_file = tmp_path / "Preferences.xml"
        prefs_file.write_text(
            '<?xml version="1.0" encoding="UTF-8"?><Preferences PlexOnlineToken="xml_token" />'
        )
        monkeypatch.setattr(
            "xbox_media_utils.api.plex.DEFAULT_PREFS_PATH",
            str(prefs_file),
        )

        scanner = PlexScanner()

        assert scanner.token == "xml_token"


class TestResolveToken:
    """Test suite for _resolve_token static method."""

    def test_xbox_plex_token_priority_over_plex_token(self, monkeypatch):
        """XBOX_PLEX_TOKEN should take priority over PLEX_TOKEN."""
        monkeypatch.setenv("XBOX_PLEX_TOKEN", "xbox")
        monkeypatch.setenv("PLEX_TOKEN", "plex")

        token = PlexScanner._resolve_token()

        assert token == "xbox"


class TestListSections:
    """Test suite for list_sections method."""

    def test_returns_sections_list(self):
        """Should return list of sections with key, type, title, locations."""
        scanner = PlexScanner(token="test")
        scanner._sections = [
            {
                "key": "1",
                "type": "movie",
                "title": "Movies",
                "Location": [{"path": "/movies"}],
            },
            {
                "key": "2",
                "type": "show",
                "title": "TV Shows",
                "Location": [{"path": "/tv"}],
            },
        ]

        sections = scanner.list_sections()

        assert len(sections) == 2
        assert sections[0]["key"] == "1"
        assert sections[0]["type"] == "movie"
        assert sections[0]["title"] == "Movies"
        assert sections[0]["locations"] == ["/movies"]


class TestResolveSectionForPath:
    """Test suite for _resolve_section_for_path method."""

    def test_finds_exact_match(self):
        """Should find section with exact path match."""
        scanner = PlexScanner(token="test")
        scanner._sections = [
            {
                "key": "1",
                "title": "Movies",
                "Location": [{"path": "/mnt/media/movies"}],
            },
        ]

        result = scanner._resolve_section_for_path(Path("/mnt/media/movies"))

        assert result is not None
        assert result["key"] == "1"

    def test_finds_child_path(self):
        """Should find section when target is child of location."""
        scanner = PlexScanner(token="test")
        scanner._sections = [
            {
                "key": "1",
                "title": "Movies",
                "Location": [{"path": "/mnt/media/movies"}],
            },
        ]

        result = scanner._resolve_section_for_path(Path("/mnt/media/movies/Action"))

        assert result is not None
        assert result["key"] == "1"

    def test_prefers_longest_match(self):
        """Should prefer section with longest matching path prefix."""
        scanner = PlexScanner(token="test")
        scanner._sections = [
            {
                "key": "1",
                "title": "Media",
                "Location": [{"path": "/mnt/media"}],
            },
            {
                "key": "2",
                "title": "Movies",
                "Location": [{"path": "/mnt/media/movies"}],
            },
        ]

        result = scanner._resolve_section_for_path(Path("/mnt/media/movies/Action"))

        assert result["key"] == "2"  # Movies, not Media

    def test_returns_none_for_no_match(self):
        """Should return None when no section matches."""
        scanner = PlexScanner(token="test")
        scanner._sections = [
            {
                "key": "1",
                "title": "Movies",
                "Location": [{"path": "/mnt/media/movies"}],
            },
        ]

        result = scanner._resolve_section_for_path(Path("/other/path"))

        assert result is None


class TestScanPath:
    """Test suite for scan_path method."""

    @patch("xbox_media_utils.api.plex.urlopen")
    def test_successful_scan(self, mock_urlopen, tmp_path):
        """Should return success when scan is triggered."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        scanner = PlexScanner(token="test")
        scanner._sections = [
            {
                "key": "1",
                "title": "Movies",
                "Location": [{"path": str(tmp_path)}],
            },
        ]

        result = scanner.scan_path(tmp_path / "movie.mkv")

        assert result["success"] is True
        assert result["section"]["title"] == "Movies"

    def test_fails_when_no_section_found(self, tmp_path):
        """Should return failure when no matching section."""
        scanner = PlexScanner(token="test")
        scanner._sections = []

        result = scanner.scan_path(tmp_path / "movie.mkv")

        assert result["success"] is False
        assert result["section"] is None
        assert "No library section found" in result["message"]


class TestScanSections:
    """Test suite for scan_sections method."""

    @patch("xbox_media_utils.api.plex.urlopen")
    def test_successful_section_scan(self, mock_urlopen):
        """Should return success for valid section."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        scanner = PlexScanner(token="test")
        scanner._sections = [
            {"key": "1", "title": "Movies"},
        ]

        results = scanner.scan_sections([1])

        assert results[1]["success"] is True
        assert "Movies" in results[1]["message"]

    def test_fails_for_invalid_section(self):
        """Should return failure for non-existent section."""
        scanner = PlexScanner(token="test")
        scanner._sections = [
            {"key": "1", "title": "Movies"},
        ]

        results = scanner.scan_sections([99])

        assert results[99]["success"] is False
        assert "does not exist" in results[99]["message"]


class TestApiGet:
    """Test suite for _api_get method."""

    @patch("xbox_media_utils.api.plex.urlopen")
    def test_successful_request(self, mock_urlopen):
        """Should return parsed JSON response."""
        mock_response = MagicMock()
        mock_response.read.return_value = b'{"test": "value"}'
        mock_urlopen.return_value.__enter__.return_value = mock_response

        scanner = PlexScanner(token="test_token")
        result = scanner._api_get("/test")

        assert result == {"test": "value"}

    @patch("xbox_media_utils.api.plex.urlopen")
    def test_empty_response_returns_none(self, mock_urlopen):
        """Should return None for empty response."""
        mock_response = MagicMock()
        mock_response.read.return_value = b""
        mock_urlopen.return_value.__enter__.return_value = mock_response

        scanner = PlexScanner(token="test")
        result = scanner._api_get("/test")

        assert result is None

    @patch("xbox_media_utils.api.plex.urlopen")
    def test_http_error_raises_plex_error(self, mock_urlopen):
        """Should raise PlexError on HTTP error."""
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            url="http://test",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )

        scanner = PlexScanner(token="test")

        with pytest.raises(PlexError) as exc_info:
            scanner._api_get("/test")

        assert "HTTP 401" in str(exc_info.value)

    @patch("xbox_media_utils.api.plex.urlopen")
    def test_url_error_raises_connection_error(self, mock_urlopen):
        """Should raise PlexConnectionError on connection failure."""
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")

        scanner = PlexScanner(token="test")

        with pytest.raises(PlexConnectionError) as exc_info:
            scanner._api_get("/test")

        assert "unreachable" in str(exc_info.value)
