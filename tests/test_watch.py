"""Behavior tests for the isolated durable watch runner."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from audiobook_pipeline.models.watch import (
    ClaimedCandidate,
    WatchCandidate,
    WatchClaim,
    WatchOptions,
    WebhookPayload,
)
from audiobook_pipeline.services.watch import SqliteClaimStore, WatchRunner


class Clock:
    """A deterministic monotonic clock for stability tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _options(tmp_path: Path, **changes: object) -> WatchOptions:
    values: dict[str, object] = {
        "inbox_dir": tmp_path / "inbox",
        "state_db": tmp_path / "state" / "watch.db",
        "quarantine_dir": tmp_path / "failed",
        "stability_seconds": 5,
        "max_retries": 1,
        "poll_interval_seconds": 0.01,
    }
    values.update(changes)
    options = WatchOptions.model_validate(values)
    options.inbox_dir.mkdir()
    return options


def _write_book(options: WatchOptions, name: str = "book.mp3") -> Path:
    source = options.inbox_dir / name
    source.write_bytes(b"audio")
    return source


def _runner(
    options: WatchOptions,
    clock: Clock,
    factory: Callable[[ClaimedCandidate], Callable[[], bool]],
) -> WatchRunner:
    return WatchRunner(options, factory, clock=clock)


def test_supported_candidate_requires_two_stable_observations(tmp_path: Path) -> None:
    options = _options(tmp_path)
    clock = Clock()
    calls: list[str] = []
    _write_book(options)

    def factory(claim: ClaimedCandidate) -> Callable[[], bool]:
        def process() -> bool:
            calls.append(claim.candidate_id)
            return True

        return process

    runner = _runner(options, clock, factory)

    first = runner.poll()
    clock.now = 5
    second = runner.poll()

    assert first.unstable == 1
    assert first.claimed == 0
    assert second.completed == 1
    assert len(calls) == 1


def test_multipart_directory_is_a_stable_non_recursive_candidate(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path, stability_seconds=0)
    clock = Clock()
    book_dir = options.inbox_dir / "multipart"
    book_dir.mkdir()
    (book_dir / "01.mp3").write_bytes(b"one")
    (book_dir / "02.wma").write_bytes(b"two")
    nested = book_dir / "nested"
    nested.mkdir()
    (nested / "ignored.mp3").write_bytes(b"three")
    claimed_sources: list[Path] = []

    def factory(claim: ClaimedCandidate) -> Callable[[], bool]:
        def process() -> bool:
            claimed_sources.append(claim.source)
            return True

        return process

    runner = _runner(options, clock, factory)
    runner.poll()
    result = runner.poll()

    assert result.completed == 1
    assert claimed_sources == [book_dir]


def test_nested_audio_without_direct_members_is_not_a_watch_candidate(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path, stability_seconds=0)
    clock = Clock()
    container = options.inbox_dir / "collection"
    nested = container / "book"
    nested.mkdir(parents=True)
    (nested / "01.mp3").write_bytes(b"audio")
    calls: list[Path] = []

    def factory(claim: ClaimedCandidate) -> Callable[[], bool]:
        def process() -> bool:
            calls.append(claim.source)
            return True

        return process

    result = _runner(options, clock, factory).poll()

    assert result.observed == 0
    assert calls == []


def test_symlink_source_is_rejected_without_reading_its_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = _options(tmp_path, stability_seconds=0)
    target = tmp_path / "external.mp3"
    target.write_bytes(b"private audio")
    (options.inbox_dir / "linked.mp3").symlink_to(target)
    original_stat = Path.stat

    def guarded_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == target:
            msg = "external target must not be read"
            raise AssertionError(msg)
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", guarded_stat)
    result = _runner(options, Clock(), lambda _claim: lambda: True).poll()

    assert result.observed == 0
    assert target.read_bytes() == b"private audio"


def test_directory_with_a_symlink_member_is_rejected_without_target_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = _options(tmp_path, stability_seconds=0)
    target = tmp_path / "external.mp3"
    target.write_bytes(b"private audio")
    book_dir = options.inbox_dir / "book"
    book_dir.mkdir()
    (book_dir / "01.mp3").write_bytes(b"audio")
    (book_dir / "02.mp3").symlink_to(target)
    original_stat = Path.stat

    def guarded_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if path == target:
            msg = "external target must not be read"
            raise AssertionError(msg)
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", guarded_stat)
    result = _runner(options, Clock(), lambda _claim: lambda: True).poll()

    assert result.observed == 0
    assert target.read_bytes() == b"private audio"


def test_claim_is_exclusive_across_restart_and_store_instances(tmp_path: Path) -> None:
    options = _options(tmp_path, stability_seconds=0)
    clock = Clock()
    calls: list[str] = []
    _write_book(options)

    def factory(claim: ClaimedCandidate) -> Callable[[], bool]:
        def process() -> bool:
            calls.append(claim.candidate_id)
            return True

        return process

    first = _runner(options, clock, factory)
    first.poll()
    first.poll()
    restarted = _runner(options, clock, factory)
    restarted.poll()
    restarted.poll()

    assert len(calls) == 1


def test_failed_candidates_retry_then_quarantine_after_exhaustion(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path, stability_seconds=0, max_retries=1)
    clock = Clock()
    source = _write_book(options)
    runner = _runner(options, clock, lambda _claim: lambda: False)

    runner.poll()
    first_failure = runner.poll()
    final_failure = runner.poll()

    assert first_failure.retried == 1
    assert final_failure.quarantined == 1
    assert not source.exists()
    assert len(tuple(options.quarantine_dir.iterdir())) == 1


def test_quarantine_collision_never_overwrites_an_existing_regular_file(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path, stability_seconds=0, max_retries=0)
    clock = Clock()
    source = _write_book(options)
    candidate_id = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
    options.quarantine_dir.mkdir()
    occupied = options.quarantine_dir / candidate_id
    occupied.write_bytes(b"user data")
    runner = _runner(options, clock, lambda _claim: lambda: False)

    runner.poll()
    result = runner.poll()

    created = tuple(
        path for path in options.quarantine_dir.iterdir() if path != occupied
    )
    assert result.quarantined == 1
    assert occupied.read_bytes() == b"user data"
    assert len(created) == 1
    assert (created[0] / source.name).read_bytes() == b"audio"


def test_move_failure_releases_the_durable_transition_for_retry(tmp_path: Path) -> None:
    options = _options(tmp_path, stability_seconds=0, max_retries=0)
    clock = Clock()
    source = _write_book(options)
    runner = _runner(options, clock, lambda _claim: lambda: False)

    def failing_mover(_source: Path, _destination: Path) -> None:
        msg = "move failed"
        raise OSError(msg)

    runner.poll()
    failed_move = runner.poll(mover=failing_mover)
    assert source.exists()
    recovered = runner.poll()

    assert failed_move.retried == 1
    assert recovered.quarantined == 1


def test_restart_reconciles_partial_directory_move_idempotently(tmp_path: Path) -> None:
    options = _options(tmp_path, stability_seconds=0, max_retries=0)
    clock = Clock()
    source = options.inbox_dir / "multipart"
    source.mkdir()
    (source / "01.mp3").write_bytes(b"first")
    (source / "02.mp3").write_bytes(b"second")
    runner = _runner(options, clock, lambda _claim: lambda: False)

    def partial_mover(directory: Path, destination: Path) -> None:
        (directory / "01.mp3").rename(destination / "01.mp3")
        msg = "move interrupted"
        raise OSError(msg)

    runner.poll()
    interrupted = runner.poll(mover=partial_mover)
    restarted = _runner(options, clock, lambda _claim: lambda: False)
    recovered = restarted.poll()
    repeated = _runner(options, clock, lambda _claim: lambda: False).poll()
    quarantine = next(
        path for path in options.quarantine_dir.iterdir() if (path / "01.mp3").is_file()
    )
    residual = next(
        path
        for path in options.quarantine_dir.iterdir()
        if (path / "multipart" / "02.mp3").is_file()
    )

    assert interrupted.quarantined == 0
    assert recovered.quarantined == 1
    assert repeated.quarantined == 0
    assert not source.exists()
    assert (quarantine / "01.mp3").read_bytes() == b"first"
    assert (residual / "multipart" / "02.mp3").read_bytes() == b"second"


def test_restart_finalizes_a_source_moved_before_its_db_mark(tmp_path: Path) -> None:
    class FailOnceFinalizationStore(SqliteClaimStore):
        def __init__(self, state_db: Path) -> None:
            super().__init__(state_db)
            self.fail_finalization = True

        def finish_quarantine(self, claim: WatchClaim) -> WatchClaim:
            if self.fail_finalization:
                self.fail_finalization = False
                msg = "state database unavailable"
                raise RuntimeError(msg)
            return super().finish_quarantine(claim)

    options = _options(tmp_path, stability_seconds=0, max_retries=0)
    clock = Clock()
    source = _write_book(options)
    store = FailOnceFinalizationStore(options.state_db)
    runner = WatchRunner(
        options, lambda _claim: lambda: False, store=store, clock=clock
    )

    runner.poll()
    interrupted = runner.poll()
    recovered = WatchRunner(
        options, lambda _claim: lambda: False, store=store, clock=clock
    ).poll()

    assert interrupted.quarantined == 0
    assert not source.exists()
    assert recovered.quarantined == 1
    assert store.quarantining() == ()


def test_restart_releases_an_unmoved_transition_after_a_db_failure(
    tmp_path: Path,
) -> None:
    class FailAfterBeginStore(SqliteClaimStore):
        def __init__(self, state_db: Path) -> None:
            super().__init__(state_db)
            self.fail_begin = True

        def begin_quarantine(
            self, claim: ClaimedCandidate, quarantine_dir: Path
        ) -> WatchClaim:
            transition = super().begin_quarantine(claim, quarantine_dir)
            if self.fail_begin:
                self.fail_begin = False
                msg = "state database unavailable"
                raise RuntimeError(msg)
            return transition

    options = _options(tmp_path, stability_seconds=0, max_retries=0)
    clock = Clock()
    source = _write_book(options)
    store = FailAfterBeginStore(options.state_db)
    runner = WatchRunner(
        options, lambda _claim: lambda: False, store=store, clock=clock
    )

    runner.poll()
    interrupted = runner.poll()
    recovered = WatchRunner(
        options, lambda _claim: lambda: False, store=store, clock=clock
    ).poll()

    assert interrupted.quarantined == 0
    assert source.exists()
    assert recovered.retried == 1
    assert store.quarantining() == ()


def test_recovery_rejects_a_reserved_directory_replaced_by_symlink(
    tmp_path: Path,
) -> None:
    class FailAfterBeginStore(SqliteClaimStore):
        def begin_quarantine(
            self, claim: ClaimedCandidate, quarantine_dir: Path
        ) -> WatchClaim:
            super().begin_quarantine(claim, quarantine_dir)
            msg = "state database unavailable"
            raise RuntimeError(msg)

    options = _options(tmp_path, stability_seconds=0, max_retries=0)
    source = _write_book(options)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "sentinel.txt"
    sentinel.write_text("user data")
    store = FailAfterBeginStore(options.state_db)
    runner = WatchRunner(
        options, lambda _claim: lambda: False, store=store, clock=Clock()
    )

    runner.poll()
    runner.poll()
    claim = store.quarantining()[0]
    assert claim.quarantine_dir is not None
    claim.quarantine_dir.rmdir()
    claim.quarantine_dir.symlink_to(external, target_is_directory=True)
    result = WatchRunner(
        options, lambda _claim: lambda: False, store=store, clock=Clock()
    ).poll()

    assert result.quarantined == 0
    assert source.read_bytes() == b"audio"
    assert sentinel.read_text() == "user data"
    assert store.quarantining() != ()


def test_recovery_rejects_a_nested_persisted_reservation(tmp_path: Path) -> None:
    class FailAfterBeginStore(SqliteClaimStore):
        def begin_quarantine(
            self, claim: ClaimedCandidate, quarantine_dir: Path
        ) -> WatchClaim:
            super().begin_quarantine(claim, quarantine_dir)
            msg = "state database unavailable"
            raise RuntimeError(msg)

    options = _options(tmp_path, stability_seconds=0, max_retries=0)
    source = _write_book(options)
    store = FailAfterBeginStore(options.state_db)
    runner = WatchRunner(
        options, lambda _claim: lambda: False, store=store, clock=Clock()
    )

    runner.poll()
    runner.poll()
    claim = store.quarantining()[0]
    assert claim.quarantine_dir is not None
    nested = claim.quarantine_dir / "nested"
    nested.mkdir()
    with sqlite3.connect(options.state_db) as connection:
        connection.execute(
            "UPDATE watch_claims SET quarantine_dir = ? WHERE candidate_id = ?",
            (str(nested), claim.candidate_id),
        )
    result = WatchRunner(
        options, lambda _claim: lambda: False, store=store, clock=Clock()
    ).poll()

    assert result.quarantined == 0
    assert source.read_bytes() == b"audio"
    assert nested.is_dir()
    assert store.quarantining()[0].quarantine_dir == nested


def test_stop_after_claim_releases_it_without_running_the_processor(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path, stability_seconds=0)
    clock = Clock()
    source = _write_book(options)
    calls = 0

    def factory(_claim: ClaimedCandidate) -> Callable[[], bool]:
        def process() -> bool:
            nonlocal calls
            calls += 1
            return True

        return process

    runner = _runner(options, clock, factory)
    runner.poll()
    stop_checks = iter((False, False, True))
    stopped = runner.poll(lambda: next(stop_checks))
    resumed = _runner(options, clock, factory)
    resumed.poll()
    completed = resumed.poll()

    assert stopped.stopped
    assert calls == 1
    assert completed.completed == 1
    assert source.exists()


def test_run_stops_gracefully_after_a_bounded_injected_sleep(tmp_path: Path) -> None:
    options = _options(tmp_path)
    clock = Clock()
    sleeps: list[float] = []
    stop_checks = iter((False, False, True))
    runner = _runner(options, clock, lambda _claim: lambda: True)

    result = runner.run(lambda: next(stop_checks), sleeper=sleeps.append)

    assert result.stopped
    assert sleeps == [options.poll_interval_seconds]


def test_poll_stops_before_scanning_empty_hidden_or_unsupported_inbox(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path)
    hidden = _write_book(options, ".hidden.mp3")
    unsupported = _write_book(options, "notes.txt")

    result = _runner(options, Clock(), lambda _claim: lambda: True).poll(lambda: True)

    assert result.stopped
    assert result.observed == 0
    assert result.unsupported == 0
    assert hidden.read_bytes() == b"audio"
    assert unsupported.read_bytes() == b"audio"


def test_webhook_success_receives_redacted_payload_and_timeout(tmp_path: Path) -> None:
    options = _options(
        tmp_path,
        stability_seconds=0,
        max_retries=0,
        failure_webhook_url="https://example.test/hook",
        webhook_timeout_seconds=4,
    )
    clock = Clock()
    _write_book(options, "private-title.mp3")
    received: list[tuple[str, str, float]] = []

    def notify(url: str, payload, timeout: float) -> bool:  # type: ignore[no-untyped-def]
        received.append((url, payload.model_dump_json(), timeout))
        return True

    runner = _runner(options, clock, lambda _claim: lambda: False)
    runner.poll(notifier=notify)
    result = runner.poll(notifier=notify)

    assert result.quarantined == 1
    assert received == [("https://example.test/hook", received[0][1], 4)]
    assert "private-title" not in received[0][1]


def test_webhook_failure_does_not_undo_quarantine(tmp_path: Path) -> None:
    options = _options(
        tmp_path,
        stability_seconds=0,
        max_retries=0,
        failure_webhook_url="https://example.test/hook",
    )
    clock = Clock()
    _write_book(options)
    runner = _runner(options, clock, lambda _claim: lambda: False)

    runner.poll(notifier=lambda _url, _payload, _timeout: False)
    result = runner.poll(notifier=lambda _url, _payload, _timeout: False)

    assert result.quarantined == 1
    assert len(tuple(options.quarantine_dir.iterdir())) == 1


def test_webhook_payload_rejects_non_quarantine_status() -> None:
    with pytest.raises(ValueError):
        WebhookPayload.model_validate({
            "candidate_id": "opaque",
            "attempt": 1,
            "status": "completed",
        })


def test_unsupported_files_are_ignored_without_creating_claim_state(
    tmp_path: Path,
) -> None:
    options = _options(tmp_path)
    clock = Clock()
    _write_book(options, "notes.txt")
    calls = 0

    def factory(_claim: ClaimedCandidate) -> Callable[[], bool]:
        def process() -> bool:
            nonlocal calls
            calls += 1
            return True

        return process

    result = _runner(options, clock, factory).poll()

    assert result.unsupported == 1
    assert result.claimed == 0
    assert calls == 0


def test_options_refuse_state_or_quarantine_inside_the_inbox(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"

    with pytest.raises(ValueError, match="state database"):
        WatchOptions(
            inbox_dir=inbox,
            state_db=inbox / "watch.db",
            quarantine_dir=tmp_path / "failed",
            stability_seconds=0,
            max_retries=0,
            poll_interval_seconds=1,
        )


def test_source_is_unchanged_until_the_claimed_processor_runs(tmp_path: Path) -> None:
    options = _options(tmp_path, stability_seconds=5)
    clock = Clock()
    source = _write_book(options)

    def factory(claim: ClaimedCandidate) -> Callable[[], bool]:
        def process() -> bool:
            claim.source.write_bytes(b"processed")
            return True

        return process

    runner = _runner(options, clock, factory)
    runner.poll()
    assert source.read_bytes() == b"audio"
    clock.now = 5
    runner.poll()

    assert source.read_bytes() == b"processed"


def test_claim_store_rejects_a_second_worker_claim(tmp_path: Path) -> None:
    store = SqliteClaimStore(tmp_path / "state.db")
    candidate = WatchCandidate(candidate_id="opaque", source=tmp_path / "book.mp3")

    first = store.claim(candidate)
    second = SqliteClaimStore(tmp_path / "state.db").claim(candidate)

    assert first is not None
    assert second is None
