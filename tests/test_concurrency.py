"""Tests for file locking and disk space checks."""

from unittest.mock import patch

import pytest

from audiobook_pipeline.concurrency import (
    LockError,
    acquire_global_lock,
    check_disk_space,
)


class TestAcquireGlobalLock:
    def test_skip_returns_none(self, tmp_path):
        assert acquire_global_lock(tmp_path, skip=True) is None

    def test_creates_lock_file(self, tmp_path):
        lock_dir = tmp_path / "locks"
        fh = acquire_global_lock(lock_dir)
        assert fh is not None
        assert (lock_dir / "pipeline.lock").exists()
        fh.close()

    def test_second_lock_raises(self, tmp_path):
        lock_dir = tmp_path / "locks"
        fh1 = acquire_global_lock(lock_dir)
        with pytest.raises(LockError, match="Another pipeline instance"):
            acquire_global_lock(lock_dir)
        fh1.close()

    def test_lock_released_after_close(self, tmp_path):
        lock_dir = tmp_path / "locks"
        fh1 = acquire_global_lock(lock_dir)
        fh1.close()
        # Should be able to acquire again after close
        fh2 = acquire_global_lock(lock_dir)
        assert fh2 is not None
        fh2.close()


class TestCheckDiskSpace:
    def test_sufficient_space(self, tmp_path):
        source = tmp_path / "source.mp3"
        source.write_bytes(b"x" * 1000)
        # tmp_path should have plenty of space
        assert check_disk_space(source, tmp_path) is True

    def test_insufficient_space(self, tmp_path):
        source = tmp_path / "source.mp3"
        source.write_bytes(b"x" * 1000)
        # Mock disk_usage to return very little free space
        fake_usage = type("Usage", (), {"free": 100, "total": 1000, "used": 900})()
        with patch("audiobook_pipeline.concurrency.shutil.disk_usage", return_value=fake_usage):
            assert check_disk_space(source, tmp_path) is False

    def test_directory_source(self, tmp_path):
        src_dir = tmp_path / "book"
        src_dir.mkdir()
        (src_dir / "ch1.mp3").write_bytes(b"x" * 500)
        (src_dir / "ch2.mp3").write_bytes(b"x" * 500)
        assert check_disk_space(src_dir, tmp_path) is True

    def test_custom_multiplier(self, tmp_path):
        source = tmp_path / "source.mp3"
        source.write_bytes(b"x" * 1000)
        # With multiplier=1, need less space
        assert check_disk_space(source, tmp_path, multiplier=1) is True
