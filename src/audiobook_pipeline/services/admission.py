"""Exclusive batch admission and disk-capacity checks for future callers."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Self

from filelock import FileLock, Timeout
from loguru import logger

from audiobook_pipeline.config import PathSettings
from audiobook_pipeline.models.admission import (
    AdmissionOptions,
    DiskAdmission,
    DiskCapacity,
)

type DiskUsageReader = Callable[[Path], DiskCapacity]
type SourceSizeReader = Callable[[Path], int]

LOCK_NAME = "pipeline.lock"


class AdmissionError(RuntimeError):
    """A batch cannot begin safely before its pipeline work starts."""


class PipelineLeaseError(AdmissionError):
    """Another process currently holds the global pipeline lease."""

    def __init__(self) -> None:
        """Build a stable, path-free lease error."""
        super().__init__("pipeline lease is unavailable")


class InsufficientDiskSpaceError(AdmissionError):
    """The configured work location cannot accommodate the source."""

    def __init__(self) -> None:
        """Build a stable, path-free capacity error."""
        super().__init__("insufficient disk space for batch admission")


class AdmissionIOError(AdmissionError):
    """An I/O boundary needed to make an admission decision failed."""

    def __init__(self) -> None:
        """Build a stable, path-free I/O error."""
        super().__init__("batch admission I/O check failed")


class SourceUnavailableError(AdmissionError):
    """The requested source is neither a readable file nor a directory."""

    def __init__(self) -> None:
        """Build a stable, path-free source error."""
        super().__init__("batch admission source is unavailable")


class AdmissionLease:
    """A closeable global pipeline lease with its disk admission receipt."""

    def __init__(self, lock: FileLock | None, disk: DiskAdmission) -> None:
        """Keep the lock object alive until the caller closes this lease."""
        self._lock = lock
        self.disk = disk

    @property
    def is_leased(self) -> bool:
        """Whether this lease owns the global file lock."""
        return self._lock is not None

    def close(self) -> None:
        """Release the global lease exactly once."""
        if self._lock is None:
            return
        self._lock.release()
        self._lock = None
        logger.info("batch_admission_lease_released")

    def __enter__(self) -> Self:
        """Return this lease for ``with`` callers."""
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the lease even when the caller raises."""
        self.close()


def _disk_capacity(path: Path) -> DiskCapacity:
    """Read operating-system capacity into the typed admission boundary."""
    usage = shutil.disk_usage(path)
    return DiskCapacity(total_bytes=usage.total, free_bytes=usage.free)


def _source_bytes(source: Path) -> int:
    """Return file bytes or the recursive total for one directory source."""
    if source.is_file():
        return source.stat().st_size
    if source.is_dir():
        return sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
    raise SourceUnavailableError


class BatchAdmission:
    """Check capacity and acquire a cross-process lease for one batch."""

    def __init__(
        self,
        paths: PathSettings,
        disk_usage: DiskUsageReader = _disk_capacity,
        source_size: SourceSizeReader = _source_bytes,
    ) -> None:
        """Store injectable filesystem boundaries without acquiring a lease."""
        self._paths = paths
        self._disk_usage = disk_usage
        self._source_size = source_size

    def assess(self, source: Path, *, multiplier: int = 3) -> DiskAdmission:
        """Calculate disk admission for a file or directory without leasing."""
        options = AdmissionOptions(space_multiplier=multiplier)
        return self._assess(source, options)

    def admit(
        self, source: Path, options: AdmissionOptions | None = None
    ) -> AdmissionLease:
        """Reject insufficient capacity or return the acquired pipeline lease."""
        actual = options or AdmissionOptions()
        disk = self._assess(source, actual)
        if not disk.sufficient:
            logger.warning("batch_admission_refused: insufficient_disk")
            raise InsufficientDiskSpaceError
        if actual.skip_lease:
            logger.info("batch_admission_lease_skipped")
            return AdmissionLease(None, disk)
        return self._acquire(disk)

    def _assess(self, source: Path, options: AdmissionOptions) -> DiskAdmission:
        try:
            source_bytes = self._source_size(source)
            capacity = self._disk_usage(self._paths.work_dir)
        except OSError as exc:
            logger.warning("batch_admission_refused: io_error")
            raise AdmissionIOError from exc
        required = source_bytes * options.space_multiplier
        return DiskAdmission(
            source_bytes=source_bytes,
            required_bytes=required,
            free_bytes=capacity.free_bytes,
            sufficient=capacity.free_bytes >= required,
        )

    def _acquire(self, disk: DiskAdmission) -> AdmissionLease:
        try:
            self._paths.lock_dir.mkdir(parents=True, exist_ok=True)
            lock = FileLock(self._paths.lock_dir / LOCK_NAME)
            lock.acquire(timeout=0)
        except Timeout as exc:
            logger.warning("batch_admission_refused: pipeline_lease")
            raise PipelineLeaseError from exc
        except OSError as exc:
            logger.warning("batch_admission_refused: io_error")
            raise AdmissionIOError from exc
        logger.info("batch_admission_granted")
        return AdmissionLease(lock, disk)
