"""File locking utilities for preventing concurrent operations."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Optional


class LockAcquisitionError(RuntimeError):
    """Raised when lock acquisition fails."""

    pass


@contextmanager
def acquire_lock(lock_file: str | Path) -> Generator[Optional[IO], None, None]:
    """Acquire exclusive file lock using context manager.

    Args:
        lock_file: Path to the lock file.

    Yields:
        File handle if lock acquired successfully.

    Raises:
        LockAcquisitionError: If lock cannot be acquired.

    Example:
        >>> with acquire_lock("/var/run/myapp.lock"):
        ...     # Critical section - only one process at a time
        ...     process_files()
    """
    fd: Optional[IO] = None
    acquired = False
    lock_path = Path(lock_file)

    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Keep a stable inode for flock. The pathname may remain after release;
        # its existence is not evidence that the lock is currently held.
        fd = open(lock_file, "a+")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        fd.seek(0)
        fd.truncate()
        fd.write(str(os.getpid()))
        fd.flush()
    except OSError as e:
        if acquired and fd:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        if fd:
            fd.close()
        raise LockAcquisitionError(f"Failed to acquire lock: {e}") from e

    try:
        yield fd
    finally:
        if acquired and fd:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
