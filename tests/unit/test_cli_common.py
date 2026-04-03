"""Tests for cli.common module."""

import argparse

import pytest

from xbox_media_utils.cli.common import (
    add_dry_run_argument,
    add_no_hardware_argument,
    add_quiet_argument,
    validate_path_exists,
)


class TestAddDryRunArgument:
    """Test suite for add_dry_run_argument."""

    def test_adds_dry_run_flag(self):
        """Should add --dry-run argument."""
        parser = argparse.ArgumentParser()
        add_dry_run_argument(parser)

        args = parser.parse_args(["--dry-run"])
        assert args.dry_run is True

    def test_defaults_to_false(self):
        """Should default to False when not provided."""
        parser = argparse.ArgumentParser()
        add_dry_run_argument(parser)

        args = parser.parse_args([])
        assert args.dry_run is False


class TestAddQuietArgument:
    """Test suite for add_quiet_argument."""

    def test_adds_quiet_flag(self):
        """Should add --quiet/-q argument."""
        parser = argparse.ArgumentParser()
        add_quiet_argument(parser)

        args = parser.parse_args(["--quiet"])
        assert args.quiet is True

        args = parser.parse_args(["-q"])
        assert args.quiet is True


class TestAddNoHardwareArgument:
    """Test suite for add_no_hardware_argument."""

    def test_adds_no_hardware_flag(self):
        """Should add --no-hardware argument."""
        parser = argparse.ArgumentParser()
        add_no_hardware_argument(parser)

        args = parser.parse_args(["--no-hardware"])
        assert args.no_hardware is True


class TestValidatePathExists:
    """Test suite for validate_path_exists."""

    def test_passes_for_existing_path(self, tmp_path):
        """Should not raise for existing path."""
        existing = tmp_path / "exists.txt"
        existing.write_text("test")

        # Should not raise
        validate_path_exists(existing)

    def test_exits_for_missing_path(self, tmp_path, capsys):
        """Should exit with error for missing path."""
        missing = tmp_path / "missing.txt"

        with pytest.raises(SystemExit) as exc_info:
            validate_path_exists(missing)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_uses_custom_name_in_error(self, tmp_path, capsys):
        """Should use custom name in error message."""
        missing = tmp_path / "missing.txt"

        with pytest.raises(SystemExit):
            validate_path_exists(missing, name="Source file")

        captured = capsys.readouterr()
        assert "Source file does not exist" in captured.err
