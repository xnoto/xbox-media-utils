"""Packaged systemd unit resources for supervised library processing."""

from importlib.resources import files
from pathlib import Path


def recode_unit_path() -> Path:
    """Return the installed xbox-recode systemd template path."""
    return Path(str(files(__package__).joinpath("xbox-recode-library@.service")))
