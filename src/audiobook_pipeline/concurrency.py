"""File locking and disk space checks."""

import shutil
import sys
from pathlib import Path

from loguru import logger

log = logger.bind(stage="concurrency")


class LockError(Exception):
    """Raised when lock cannot be acquired."""


def acquire_global_lock(lock_dir: Path, skip: bool = False) -> object | None:
    """Acquire a global file lock for singleton pipeline execution.

    Returns the lock file handle (keep reference to maintain lock),
    or None if locking was skipped.
    Raises LockError if another instance holds the lock.
    """
    log.debug(f"acquire_global_lock(lock_dir={lock_dir}, skip={skip})")

    if skip:
        log.debug("Skipping lock acquisition")
        return None

    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / "pipeline.lock"

    if sys.platform == "win32":
        import msvcrt
        fh = open(lock_file, "w")
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            log.warning(f"Failed to acquire lock at {lock_file}")
            raise LockError("Another pipeline instance is running")
        log.info(f"Lock acquired at {lock_file}")
        return fh
    else:
        import fcntl
        fh = open(lock_file, "w")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            log.warning(f"Failed to acquire lock at {lock_file}")
            raise LockError("Another pipeline instance is running")
        log.info(f"Lock acquired at {lock_file}")
        return fh


def check_disk_space(source_path: Path, work_dir: Path, multiplier: int = 3) -> bool:
    """Check that work_dir has enough free space.

    Requires at least multiplier * source_size available.
    Returns True if sufficient, False otherwise.
    """
    log.debug(f"check_disk_space(source_path={source_path}, work_dir={work_dir}, multiplier={multiplier})")

    if source_path.is_file():
        source_size = source_path.stat().st_size
    else:
        source_size = sum(
            f.stat().st_size for f in source_path.rglob("*") if f.is_file()
        )

    required = source_size * multiplier
    usage = shutil.disk_usage(work_dir)
    result = usage.free >= required

    log.debug(f"Disk space check: required={required:,} bytes, free={usage.free:,} bytes, sufficient={result}")

    return result
