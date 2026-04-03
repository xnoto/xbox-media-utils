"""Tests for core.locking module."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from xbox_media_utils.core.locking import LockAcquisitionError, acquire_lock


class TestAcquireLock:
    """Test suite for acquire_lock context manager."""

    def test_acquire_lock_creates_file(self, tmp_path):
        """Lock file should be created when acquiring lock."""
        lock_file = tmp_path / "test.lock"

        with acquire_lock(lock_file):
            assert lock_file.exists()

    def test_lock_file_contains_pid(self, tmp_path):
        """Lock file should contain the process ID."""
        lock_file = tmp_path / "test.lock"

        with acquire_lock(lock_file) as fd:
            content = lock_file.read_text()
            assert content == str(os.getpid())

    def test_lock_released_after_exit(self, tmp_path):
        """Lock should be released and file removed after context exit."""
        lock_file = tmp_path / "test.lock"

        with acquire_lock(lock_file):
            pass

        assert not lock_file.exists()

    def test_lock_creates_parent_directories(self, tmp_path):
        """Parent directories should be created if they don't exist."""
        lock_file = tmp_path / "deep" / "nested" / "test.lock"

        with acquire_lock(lock_file):
            assert lock_file.parent.exists()

    def test_concurrent_access_blocks(self, tmp_path):
        """Second attempt to acquire lock should fail while first holds it."""
        lock_file = tmp_path / "test.lock"
        results = []

        def acquire_with_result(should_block):
            try:
                with acquire_lock(lock_file):
                    results.append((should_block, "acquired"))
                    time.sleep(0.1)  # Hold lock briefly
            except LockAcquisitionError:
                results.append((should_block, "blocked"))

        # First thread acquires lock
        t1 = threading.Thread(target=acquire_with_result, args=(False,))
        t1.start()
        time.sleep(0.05)  # Ensure t1 acquires first

        # Second thread should fail to acquire
        t2 = threading.Thread(target=acquire_with_result, args=(True,))
        t2.start()

        t1.join()
        t2.join()

        assert (False, "acquired") in results
        assert (True, "blocked") in results

    def test_nested_lock_same_file_fails(self, tmp_path):
        """Acquiring same lock twice in same process should fail."""
        lock_file = tmp_path / "test.lock"

        with acquire_lock(lock_file):
            with pytest.raises(LockAcquisitionError):
                with acquire_lock(lock_file):
                    pass  # Should not reach here

    def test_lock_release_on_exception(self, tmp_path):
        """Lock should be released even if exception occurs in context."""
        lock_file = tmp_path / "test.lock"

        with pytest.raises(ValueError):
            with acquire_lock(lock_file):
                raise ValueError("Test exception")

        assert not lock_file.exists()

    def test_lock_acquired_with_path_object(self, tmp_path):
        """Should work with both string and Path objects."""
        lock_file = tmp_path / "test.lock"

        # Test with Path object
        with acquire_lock(lock_file):
            assert lock_file.exists()

        # Test with string
        lock_file_str = str(tmp_path / "test2.lock")
        with acquire_lock(lock_file_str):
            assert Path(lock_file_str).exists()

    def test_sequential_locks_same_file(self, tmp_path):
        """Should be able to acquire same lock file sequentially."""
        lock_file = tmp_path / "test.lock"

        for _ in range(3):
            with acquire_lock(lock_file):
                assert lock_file.exists()
            assert not lock_file.exists()
