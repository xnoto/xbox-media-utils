"""Tests for core.logging module."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from xbox_media_utils.core.logging import (
    get_log_file_path,
    read_log_entries,
    write_log_entry,
)


class TestWriteLogEntry:
    """Test suite for write_log_entry function."""

    def test_creates_log_directory(self, tmp_path):
        """Should create log directory if it doesn't exist."""
        log_dir = tmp_path / "logs" / "deep" / "nested"
        entry = {"status": "test"}

        write_log_entry(entry, log_dir)

        assert log_dir.exists()

    def test_writes_jsonl_entry(self, tmp_path):
        """Should write entry as JSON line."""
        log_dir = tmp_path / "logs"
        entry = {"status": "success", "file": "movie.mkv"}

        write_log_entry(entry, log_dir, prefix="test")

        log_files = list(log_dir.glob("*.jsonl"))
        assert len(log_files) == 1

        content = log_files[0].read_text()
        parsed = json.loads(content.strip())
        assert parsed == entry

    def test_appends_to_existing_file(self, tmp_path):
        """Should append to existing log file, not overwrite."""
        log_dir = tmp_path / "logs"

        write_log_entry({"entry": 1}, log_dir, prefix="test")
        write_log_entry({"entry": 2}, log_dir, prefix="test")

        log_files = list(log_dir.glob("*.jsonl"))
        content = log_files[0].read_text()
        lines = content.strip().split("\n")

        assert len(lines) == 2
        assert json.loads(lines[0]) == {"entry": 1}
        assert json.loads(lines[1]) == {"entry": 2}

    def test_uses_date_in_filename(self, tmp_path):
        """Should include date in filename."""
        log_dir = tmp_path / "logs"
        specific_date = datetime(2024, 1, 15, 10, 30, 0)

        write_log_entry({"test": True}, log_dir, prefix="app", timestamp=specific_date)

        expected_file = log_dir / "app-2024-01-15.jsonl"
        assert expected_file.exists()

    def test_returns_log_file_path(self, tmp_path):
        """Should return the path to the log file."""
        log_dir = tmp_path / "logs"

        result = write_log_entry({"test": True}, log_dir, prefix="myapp")

        assert isinstance(result, Path)
        assert result.exists()
        assert result.name.startswith("myapp-")

    def test_handles_complex_nested_data(self, tmp_path):
        """Should handle nested dictionaries and lists."""
        log_dir = tmp_path / "logs"
        entry = {
            "status": "success",
            "data": {
                "nested": [1, 2, 3],
                "bool": True,
                "none": None,
            },
        }

        write_log_entry(entry, log_dir)

        log_file = list(log_dir.glob("*.jsonl"))[0]
        parsed = json.loads(log_file.read_text().strip())
        assert parsed == entry


class TestGetLogFilePath:
    """Test suite for get_log_file_path function."""

    def test_returns_correct_path(self, tmp_path):
        """Should return correct path with date and prefix."""
        log_dir = tmp_path / "logs"
        date = datetime(2024, 6, 15)

        result = get_log_file_path(log_dir, prefix="test", date=date)

        assert result == log_dir / "test-2024-06-15.jsonl"

    def test_uses_current_date_by_default(self, tmp_path):
        """Should use today's date by default."""
        log_dir = tmp_path / "logs"

        result = get_log_file_path(log_dir, prefix="app")

        today_str = datetime.now().strftime("%Y-%m-%d")
        assert result.name == f"app-{today_str}.jsonl"


class TestReadLogEntries:
    """Test suite for read_log_entries function."""

    def test_reads_single_entry(self, tmp_path):
        """Should read single entry from JSONL file."""
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"status": "ok"}\n')

        entries = read_log_entries(log_file)

        assert len(entries) == 1
        assert entries[0] == {"status": "ok"}

    def test_reads_multiple_entries(self, tmp_path):
        """Should read multiple entries from JSONL file."""
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"a": 1}\n{"b": 2}\n{"c": 3}\n')

        entries = read_log_entries(log_file)

        assert len(entries) == 3
        assert entries == [{"a": 1}, {"b": 2}, {"c": 3}]

    def test_handles_empty_lines(self, tmp_path):
        """Should skip empty lines in log file."""
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"a": 1}\n\n{"b": 2}\n\n')

        entries = read_log_entries(log_file)

        assert len(entries) == 2

    def test_raises_file_not_found(self, tmp_path):
        """Should raise FileNotFoundError for missing file."""
        log_file = tmp_path / "nonexistent.jsonl"

        with pytest.raises(FileNotFoundError):
            read_log_entries(log_file)

    def test_raises_json_decode_error(self, tmp_path):
        """Should raise JSONDecodeError for invalid JSON."""
        log_file = tmp_path / "test.jsonl"
        log_file.write_text("not valid json\n")

        with pytest.raises(json.JSONDecodeError):
            read_log_entries(log_file)
