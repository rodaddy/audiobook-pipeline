"""Focused behavior tests for isolated batch admission."""

from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable
from multiprocessing.synchronize import Event
from pathlib import Path

import pytest
from pydantic import ValidationError

from audiobook_pipeline.config import PathSettings
from audiobook_pipeline.models.admission import AdmissionOptions, DiskCapacity
from audiobook_pipeline.services.admission import (
    AdmissionIOError,
    BatchAdmission,
    InsufficientDiskSpaceError,
    PipelineLeaseError,
)


def _paths(root: Path) -> PathSettings:
    """Build one isolated work and lock location."""
    work_dir = root / "work"
    work_dir.mkdir(exist_ok=True)
    return PathSettings(work_dir=work_dir, lock_dir=root / "locks")


def _source(root: Path, size: int = 100) -> Path:
    """Create a one-file source with a deterministic byte count."""
    source = root / "source.m4b"
    source.write_bytes(b"x" * size)
    return source


def _hold_lease(root: str, source: str, ready: Event, release: Event) -> None:
    """Hold a real file lock in a separate process until the parent releases it."""
    paths = _paths(Path(root))
    lease = BatchAdmission(paths).admit(Path(source))
    ready.set()
    release.wait(5)
    lease.close()


def _crash_with_lease(root: str, source: str, ready: Event) -> None:
    """Exit without releasing, proving the OS-backed maintained lock recovers."""
    paths = _paths(Path(root))
    lease = BatchAdmission(paths).admit(Path(source))
    ready.set()
    assert lease.is_leased
    os._exit(0)


def test_second_process_cannot_acquire_the_global_pipeline_lease(
    tmp_path: Path,
) -> None:
    """FileLock refuses the parent while a child process owns the lease."""
    source = _source(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_lease,
        args=(str(tmp_path), str(source), ready, release),
    )
    process.start()
    assert ready.wait(5)
    try:
        with pytest.raises(PipelineLeaseError):
            BatchAdmission(_paths(tmp_path)).admit(source)
    finally:
        release.set()
        process.join(5)
    assert process.exitcode == 0


def test_close_and_context_exit_release_the_global_pipeline_lease(
    tmp_path: Path,
) -> None:
    """Controlled callers can release and then reacquire immediately."""
    paths = _paths(tmp_path)
    source = _source(tmp_path)
    first = BatchAdmission(paths).admit(source)
    first.close()
    with BatchAdmission(paths).admit(source) as second:
        assert second.is_leased
    with BatchAdmission(paths).admit(source) as third:
        assert third.is_leased


def test_crashed_owner_releases_the_maintained_os_lock(tmp_path: Path) -> None:
    """A process crash does not leave the next batch permanently blocked."""
    source = _source(tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    process = context.Process(
        target=_crash_with_lease,
        args=(str(tmp_path), str(source), ready),
    )
    process.start()
    assert ready.wait(5)
    process.join(5)
    assert process.exitcode == 0
    with BatchAdmission(_paths(tmp_path)).admit(source) as recovered:
        assert recovered.is_leased


def test_controlled_skip_does_not_take_the_global_lease(tmp_path: Path) -> None:
    """Explicit opt-out leaves the real lease available to another caller."""
    paths = _paths(tmp_path)
    source = _source(tmp_path)
    skipped = BatchAdmission(paths).admit(source, AdmissionOptions(skip_lease=True))

    assert not skipped.is_leased
    with BatchAdmission(paths).admit(source) as acquired:
        assert acquired.is_leased


def test_assess_counts_file_and_directory_sources(tmp_path: Path) -> None:
    """Input sizing follows the legacy file-or-recursive-directory contract."""
    paths = _paths(tmp_path)
    source = _source(tmp_path, 100)
    directory = tmp_path / "book"
    directory.mkdir()
    (directory / "one.mp3").write_bytes(b"x" * 50)
    (directory / "two.mp3").write_bytes(b"x" * 75)
    admission = BatchAdmission(
        paths, disk_usage=lambda _: DiskCapacity(total_bytes=1_000, free_bytes=1_000)
    )

    file_result = admission.assess(source, multiplier=2)
    directory_result = admission.assess(directory, multiplier=2)

    assert (file_result.source_bytes, file_result.required_bytes) == (100, 200)
    assert (directory_result.source_bytes, directory_result.required_bytes) == (
        125,
        250,
    )


@pytest.mark.parametrize(
    ("free_bytes", "sufficient"),
    [(201, True), (199, False), (200, True)],
)
def test_disk_admission_has_sufficient_insufficient_and_exact_boundaries(
    tmp_path: Path, free_bytes: int, sufficient: bool
) -> None:
    """The boundary is inclusive: exactly required capacity is sufficient."""
    source = _source(tmp_path, 100)
    admission = BatchAdmission(
        _paths(tmp_path),
        disk_usage=lambda _: DiskCapacity(total_bytes=1_000, free_bytes=free_bytes),
    )

    result = admission.assess(source, multiplier=2)

    assert result.sufficient is sufficient


def test_admit_refuses_insufficient_disk_without_taking_a_lease(tmp_path: Path) -> None:
    """Capacity failure is explicit and does not leave a blocked lock behind."""
    paths = _paths(tmp_path)
    source = _source(tmp_path, 100)
    admission = BatchAdmission(
        paths, disk_usage=lambda _: DiskCapacity(total_bytes=1_000, free_bytes=199)
    )

    with pytest.raises(InsufficientDiskSpaceError):
        admission.admit(source, AdmissionOptions(space_multiplier=2))
    with BatchAdmission(paths).admit(source) as acquired:
        assert acquired.is_leased


def test_invalid_multiplier_is_rejected_by_the_pydantic_contract() -> None:
    """Zero and negative multipliers cannot weaken admission silently."""
    with pytest.raises(ValidationError):
        AdmissionOptions(space_multiplier=0)


def _failing_size(_: Path) -> int:
    """Provide an injectable source-size boundary that fails deterministically."""
    raise OSError("unavailable")


def _failing_disk(_: Path) -> DiskCapacity:
    """Provide an injectable disk-usage boundary that fails deterministically."""
    raise OSError("unavailable")


@pytest.mark.parametrize(
    "source_size,disk_usage",
    [
        (_failing_size, lambda _: DiskCapacity(total_bytes=1_000, free_bytes=1_000)),
        (lambda _: 100, _failing_disk),
    ],
)
def test_io_failure_is_a_stable_admission_error(
    tmp_path: Path,
    source_size: Callable[[Path], int],
    disk_usage: Callable[[Path], DiskCapacity],
) -> None:
    """Size and disk I/O faults reject admission rather than guessing capacity."""
    source = _source(tmp_path)
    paths = _paths(tmp_path)
    admission = BatchAdmission(paths, disk_usage=disk_usage, source_size=source_size)

    with pytest.raises(AdmissionIOError, match="batch admission I/O check failed"):
        admission.assess(source)
