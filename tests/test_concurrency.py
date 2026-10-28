"""Historical process-lease and disk-admission guards at the typed boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.config import PathSettings
from audiobook_pipeline.models.admission import AdmissionOptions, DiskCapacity
from audiobook_pipeline.services.admission import (
    LOCK_NAME,
    BatchAdmission,
    InsufficientDiskSpaceError,
    PipelineLeaseError,
)


def _paths(root: Path) -> PathSettings:
    work_dir = root / "work"
    work_dir.mkdir(exist_ok=True)
    return PathSettings(work_dir=work_dir, lock_dir=root / "locks")


def _source(root: Path, size: int = 1_000) -> Path:
    source = root / "source.mp3"
    source.write_bytes(b"x" * size)
    return source


def _admission(root: Path, *, free_bytes: int) -> BatchAdmission:
    return BatchAdmission(
        _paths(root),
        disk_usage=lambda _: DiskCapacity(
            total_bytes=max(free_bytes, 1_000), free_bytes=free_bytes
        ),
    )


def test_controlled_skip_does_not_take_process_lease(tmp_path: Path) -> None:
    source = _source(tmp_path)
    paths = _paths(tmp_path)

    skipped = BatchAdmission(paths).admit(source, AdmissionOptions(skip_lease=True))

    assert not skipped.is_leased
    with BatchAdmission(paths).admit(source) as leased:
        assert leased.is_leased


def test_admission_creates_global_lock_file(tmp_path: Path) -> None:
    source = _source(tmp_path)
    paths = _paths(tmp_path)

    with BatchAdmission(paths).admit(source) as lease:
        assert lease.is_leased
        assert (paths.lock_dir / LOCK_NAME).is_file()


def test_second_lease_is_rejected(tmp_path: Path) -> None:
    source = _source(tmp_path)
    paths = _paths(tmp_path)
    first = BatchAdmission(paths).admit(source)
    try:
        with pytest.raises(PipelineLeaseError, match="pipeline lease is unavailable"):
            BatchAdmission(paths).admit(source)
    finally:
        first.close()


def test_closed_lease_can_be_reacquired(tmp_path: Path) -> None:
    source = _source(tmp_path)
    paths = _paths(tmp_path)
    first = BatchAdmission(paths).admit(source)
    first.close()

    with BatchAdmission(paths).admit(source) as second:
        assert second.is_leased


def test_sufficient_file_space_is_admitted(tmp_path: Path) -> None:
    source = _source(tmp_path)
    result = _admission(tmp_path, free_bytes=3_000).assess(source)
    assert result.sufficient
    assert (result.source_bytes, result.required_bytes) == (1_000, 3_000)


def test_insufficient_space_is_rejected_before_leasing(tmp_path: Path) -> None:
    source = _source(tmp_path)
    admission = _admission(tmp_path, free_bytes=2_999)

    with pytest.raises(
        InsufficientDiskSpaceError,
        match="insufficient disk space for batch admission",
    ):
        admission.admit(source)

    assert not (_paths(tmp_path).lock_dir / LOCK_NAME).exists()


def test_directory_source_counts_recursive_file_bytes(tmp_path: Path) -> None:
    source = tmp_path / "book"
    source.mkdir()
    (source / "chapter-1.mp3").write_bytes(b"x" * 500)
    nested = source / "disc-2"
    nested.mkdir()
    (nested / "chapter-2.mp3").write_bytes(b"x" * 500)

    result = _admission(tmp_path, free_bytes=3_000).assess(source)

    assert result.source_bytes == 1_000
    assert result.required_bytes == 3_000
    assert result.sufficient


def test_custom_multiplier_controls_required_capacity(tmp_path: Path) -> None:
    source = _source(tmp_path)
    admission = _admission(tmp_path, free_bytes=1_000)

    assert admission.assess(source, multiplier=1).sufficient
    assert not admission.assess(source, multiplier=2).sufficient
