"""File locking utilities for preventing concurrent operations."""

from __future__ import annotations

import contextlib
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Generator, Optional


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
    lock_path = Path(lock_file)

    try:
        # Ensure parent directory exists
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        fd = open(lock_file, "w")
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        yield fd
    except OSError as e:
        raise LockAcquisitionError(f"Failed to acquire lock: {e}") from e
    finally:
        if fd:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            fd.close()
            with contextlib.suppress(OSError):
                lock_path.unlink()
