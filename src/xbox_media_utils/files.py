"""File system and ownership utilities."""

import grp
import os
import pwd
from pathlib import Path
from typing import Optional


def set_ownership(filepath: Path, user: str, group: str) -> tuple[bool, Optional[str]]:
    """Set file ownership to specified user:group."""
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
        os.chown(filepath, uid, gid)
        return True, None
    except Exception as e:
        return False, str(e)


def collect_media_files(source: Path, extensions: set[str]) -> list[Path]:
    """Recursively collect all media files from source."""
    from .media import is_sample_file

    files = []
    if source.is_file():
        if source.suffix.lower() in extensions and not is_sample_file(source):
            files.append(source)
    else:
        for ext in extensions:
            for f in source.rglob(f"*{ext}"):
                if not is_sample_file(f):
                    files.append(f)
            for f in source.rglob(f"*{ext.upper()}"):
                if not is_sample_file(f):
                    files.append(f)
    return sorted(set(files))
