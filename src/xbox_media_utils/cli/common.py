"""Shared CLI utilities."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def add_dry_run_argument(parser: argparse.ArgumentParser) -> None:
    """Add --dry-run argument to parser."""
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )


def add_quiet_argument(parser: argparse.ArgumentParser) -> None:
    """Add --quiet argument to parser."""
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Minimal output",
    )


def add_no_hardware_argument(parser: argparse.ArgumentParser) -> None:
    """Add --no-hardware argument to parser."""
    parser.add_argument(
        "--no-hardware",
        action="store_true",
        help="Disable VAAPI hardware acceleration",
    )


def validate_path_exists(path: Path, name: str = "Path") -> None:
    """Validate that a path exists, exit with error if not.

    Args:
        path: Path to validate.
        name: Name to use in error message.

    Raises:
        SystemExit: If path does not exist.
    """
    if not path.exists():
        print(f"Error: {name} does not exist: {path}", file=sys.stderr)
        raise SystemExit(1)