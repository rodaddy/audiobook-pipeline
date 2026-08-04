"""Adversarial recovery tests for durable watch quarantine transitions."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from audiobook_pipeline.models.watch import ClaimedCandidate, WatchClaim, WatchOptions
from audiobook_pipeline.services.watch import SqliteClaimStore, WatchRunner
from audiobook_pipeline.services.watch_recovery import safe_recovery_reservation


def _interrupted_partial_claim(
    tmp_path: Path,
) -> tuple[WatchOptions, SqliteClaimStore, Path, WatchClaim]:
    options = WatchOptions(
        inbox_dir=tmp_path / "inbox",
        state_db=tmp_path / "state" / "watch.db",
        quarantine_dir=tmp_path / "failed",
        stability_seconds=0,
        max_retries=0,
        poll_interval_seconds=0.01,
    )
    options.inbox_dir.mkdir()
    source = options.inbox_dir / "multipart"
    source.mkdir()
    (source / "01.mp3").write_bytes(b"first")
    (source / "02.mp3").write_bytes(b"second")

    def partial_mover(directory: Path, destination: Path) -> None:
        (directory / "01.mp3").rename(destination / "01.mp3")
        raise OSError("move interrupted")

    store = SqliteClaimStore(options.state_db)
    runner = WatchRunner(options, lambda _claim: lambda: False, store=store)
    runner.poll()
    runner.poll(mover=partial_mover)
    return options, store, source, store.quarantining()[0]


def _swap_targets(options: WatchOptions, source: Path) -> tuple[Path, Path, Path]:
    external = source.parent.parent / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("user data")
    candidate_id = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    occupied = options.quarantine_dir / f"{candidate_id}-residual"
    occupied.write_text("user data")
    return external, sentinel, occupied


def _swap_on_second_validation(
    monkeypatch: pytest.MonkeyPatch, replacement: Callable[[], None]
) -> None:
    original_safe = safe_recovery_reservation
    validations = 0

    def guarded(quarantine_root: Path, claim: WatchClaim) -> bool:
        nonlocal validations
        validated = original_safe(quarantine_root, claim)
        validations += 1
        if validations == 2:
            replacement()
        return validated

    monkeypatch.setattr(
        "audiobook_pipeline.services.watch.safe_recovery_reservation", guarded
    )


def _failed_file_options(tmp_path: Path) -> tuple[WatchOptions, Path]:
    options = WatchOptions(
        inbox_dir=tmp_path / "inbox",
        state_db=tmp_path / "state" / "watch.db",
        quarantine_dir=tmp_path / "failed",
        stability_seconds=0,
        max_retries=0,
        poll_interval_seconds=0.01,
    )
    options.inbox_dir.mkdir()
    source = options.inbox_dir / "book.mp3"
    source.write_bytes(b"audio")
    return options, source


def test_partial_recovery_avoids_a_reservation_swapped_after_revalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options, store, source, claim = _interrupted_partial_claim(tmp_path)
    assert claim.quarantine_dir is not None
    external, sentinel, occupied = _swap_targets(options, source)
    displaced = tmp_path / "displaced"
    original_safe = safe_recovery_reservation
    validations = 0

    def swap_after_second_validation(
        quarantine_root: Path, recovered_claim: WatchClaim
    ) -> bool:
        nonlocal validations
        validated = original_safe(quarantine_root, recovered_claim)
        validations += 1
        if validations == 2:
            assert claim.quarantine_dir is not None
            claim.quarantine_dir.rename(displaced)
            claim.quarantine_dir.symlink_to(external, target_is_directory=True)
        return validated

    monkeypatch.setattr(
        "audiobook_pipeline.services.watch.safe_recovery_reservation",
        swap_after_second_validation,
    )
    recovered = WatchRunner(options, lambda _claim: lambda: False, store=store).poll()
    residual = next(
        path
        for path in options.quarantine_dir.iterdir()
        if (path / source.name / "02.mp3").is_file()
    )

    assert recovered.quarantined == 1
    assert validations == 2
    assert not source.exists()
    assert (displaced / "01.mp3").read_bytes() == b"first"
    assert (residual / source.name / "02.mp3").read_bytes() == b"second"
    assert occupied.read_text() == "user data"
    assert sentinel.read_text() == "user data"


def test_partial_recovery_rejects_a_root_swapped_before_fresh_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options, store, source, _claim = _interrupted_partial_claim(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("user data")
    displaced_root = tmp_path / "displaced-root"

    def replace_root() -> None:
        options.quarantine_dir.rename(displaced_root)
        options.quarantine_dir.symlink_to(external, target_is_directory=True)

    _swap_on_second_validation(monkeypatch, replace_root)
    result = WatchRunner(options, lambda _claim: lambda: False, store=store).poll()

    assert result.quarantined == 0
    assert source.is_dir()
    assert sentinel.read_text() == "user data"
    assert store.quarantining() != ()


def test_recovery_move_rejects_a_root_swapped_after_its_directory_fd_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options, store, source, _claim = _interrupted_partial_claim(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("user data")
    displaced_root = tmp_path / "displaced-root"
    original_open = os.open
    swapped = False

    def swap_after_open(
        path: Path, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if not swapped and path == options.quarantine_dir and flags & os.O_DIRECTORY:
            options.quarantine_dir.rename(displaced_root)
            options.quarantine_dir.symlink_to(external, target_is_directory=True)
            swapped = True
        return descriptor

    monkeypatch.setattr("audiobook_pipeline.services.watch.os.open", swap_after_open)
    result = WatchRunner(options, lambda _claim: lambda: False, store=store).poll()

    assert swapped
    assert result.quarantined == 0
    assert source.is_dir()
    assert sentinel.read_text() == "user data"
    assert store.quarantining() != ()


def test_default_move_retries_when_store_replaces_reserved_child_with_symlink(
    tmp_path: Path,
) -> None:
    options, source = _failed_file_options(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("user data")

    class SwappingStore(SqliteClaimStore):
        def begin_quarantine(
            self, claim: ClaimedCandidate, quarantine_dir: Path
        ) -> WatchClaim:
            transition = super().begin_quarantine(claim, quarantine_dir)
            quarantine_dir.rmdir()
            quarantine_dir.symlink_to(external, target_is_directory=True)
            return transition

    store = SwappingStore(options.state_db)
    runner = WatchRunner(options, lambda _claim: lambda: False, store=store)
    runner.poll()
    result = runner.poll()

    assert result.quarantined == 0
    assert result.retried == 1
    assert source.read_bytes() == b"audio"
    assert sentinel.read_text() == "user data"
    assert store.quarantining() == ()
