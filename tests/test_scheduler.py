"""Focused tests for CPU-aware batch scheduling."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, get_ident

import pytest
from pydantic import ValidationError

from audiobook_pipeline.models.scheduling import (
    BookOutcome,
    SchedulingOptions,
    WorkerFailure,
)
from audiobook_pipeline.services.scheduler import (
    BatchScheduler,
    calculate_max_workers,
    threads_per_worker,
)


def _scheduler(*, cpu_load: float = 0.0, max_workers: int = 0) -> BatchScheduler:
    return BatchScheduler(
        SchedulingOptions(max_workers=max_workers, poll_interval_seconds=0.001),
        cpu_count=lambda: 12,
        cpu_load=lambda: cpu_load,
        executor_factory=ThreadPoolExecutor,
    )


@pytest.mark.parametrize(
    ("cpu_count", "expected"),
    [(None, 1), (0, 1), (1, 1), (2, 1), (3, 1), (11, 3), (12, 4), (99, 4)],
)
def test_auto_worker_count_has_a_low_core_floor(
    cpu_count: int | None, expected: int
) -> None:
    assert calculate_max_workers(configured=0, cpu_count=cpu_count) == expected


def test_explicit_worker_count_has_no_hidden_cap() -> None:
    assert calculate_max_workers(configured=9, cpu_count=1) == 9


def test_thread_allocation_matches_legacy_behavior() -> None:
    assert threads_per_worker(cpu_count=12, active_count=1) == 0
    assert threads_per_worker(cpu_count=12, active_count=2) == 5
    assert threads_per_worker(cpu_count=1, active_count=2) == 1


def test_options_reject_an_unusable_cpu_ceiling() -> None:
    with pytest.raises(ValidationError):
        SchedulingOptions(cpu_ceiling_pct=0)


def test_cpu_ceiling_defers_admission_until_a_later_poll() -> None:
    loads = iter((80.0, 20.0))
    scheduler = BatchScheduler(
        SchedulingOptions(cpu_ceiling_pct=80, poll_interval_seconds=0.001),
        cpu_count=lambda: 12,
        cpu_load=lambda: next(loads),
    )
    coordinator = scheduler.start([Path("one")], lambda _book, _threads: lambda: True)

    blocked = coordinator.poll()
    admitted = coordinator.poll()
    result = coordinator.run()

    assert blocked.queued == 1
    assert blocked.active == 0
    assert admitted.queued == 0
    assert result.succeeded == 1


def test_cpu_ceiling_is_checked_before_each_new_admission() -> None:
    loads = iter((20.0, 80.0, 20.0))
    scheduler = BatchScheduler(
        SchedulingOptions(
            cpu_ceiling_pct=80, max_workers=2, poll_interval_seconds=0.001
        ),
        cpu_count=lambda: 12,
        cpu_load=lambda: next(loads),
    )
    coordinator = scheduler.start(
        [Path("first"), Path("second")], lambda _book, _threads: lambda: True
    )

    first_poll = coordinator.poll()
    result = coordinator.run()

    assert first_poll.queued == 1
    assert result.succeeded == 2


def test_coordinator_snapshots_cpu_count_and_capacity_once() -> None:
    cpu_counts: list[int] = []
    observed_capacities: list[int] = []
    release = Event()

    def cpu_count() -> int:
        cpu_counts.append(len(cpu_counts))
        return 12 if len(cpu_counts) == 1 else 1

    def executor_factory(capacity: int) -> ThreadPoolExecutor:
        observed_capacities.append(capacity)
        return ThreadPoolExecutor(max_workers=capacity)

    def factory(_book: Path, _threads: int) -> Callable[[], bool]:
        def worker() -> bool:
            release.wait(timeout=1)
            return True

        return worker

    scheduler = BatchScheduler(
        SchedulingOptions(poll_interval_seconds=0.001),
        cpu_count=cpu_count,
        cpu_load=lambda: 0.0,
        executor_factory=executor_factory,
    )
    coordinator = scheduler.start([Path(str(index)) for index in range(5)], factory)

    snapshot = coordinator.poll()
    release.set()
    result = coordinator.run()

    assert cpu_counts == [0]
    assert observed_capacities == [4]
    assert snapshot.active == 4
    assert snapshot.queued == 1
    assert result.succeeded == 5


def test_empty_batch_is_already_complete() -> None:
    result = _scheduler().start([], lambda _book, _threads: lambda: True).run()

    assert result.total == 0
    assert result.results == ()


def test_factory_is_created_inside_the_executor_task() -> None:
    caller_thread = get_ident()
    factory_threads: list[int] = []

    def factory(_book: Path, _threads: int) -> Callable[[], bool]:
        factory_threads.append(get_ident())
        return lambda: True

    result = _scheduler().start([Path("one")], factory).run()

    assert result.succeeded == 1
    assert factory_threads != [caller_thread]


def test_failure_does_not_lose_other_results_and_input_order_is_stable() -> None:
    def factory(book: Path, _threads: int) -> Callable[[], bool]:
        if book.name == "bad":

            def failing() -> bool:
                msg = "conversion failed with secret-token=do-not-return"
                raise RuntimeError(msg)

            return failing
        return lambda: True

    result = (
        _scheduler(max_workers=3)
        .start([Path("first"), Path("bad"), Path("last")], factory)
        .run()
    )

    assert [item.source.name for item in result.results] == ["first", "bad", "last"]
    assert [item.outcome for item in result.results] == [
        BookOutcome.SUCCESS,
        BookOutcome.FAILURE,
        BookOutcome.SUCCESS,
    ]
    assert result.succeeded == 2
    assert result.failed == 1
    assert result.results[1].error is WorkerFailure.EXCEPTION
    assert "secret-token" not in result.model_dump_json()


def test_false_worker_result_has_a_stable_failure_category() -> None:
    result = (
        _scheduler()
        .start([Path("reported-failure")], lambda _book, _threads: lambda: False)
        .run()
    )

    assert result.failed == 1
    assert result.results[0].error is WorkerFailure.REPORTED_FAILURE


def test_cancel_accounts_for_queued_and_running_tasks() -> None:
    started = Event()
    release = Event()

    def factory(_book: Path, _threads: int) -> Callable[[], bool]:
        def worker() -> bool:
            started.set()
            release.wait(timeout=1)
            return True

        return worker

    coordinator = _scheduler(max_workers=1).start(
        [Path("running"), Path("queued")], factory
    )
    coordinator.poll()
    assert started.wait(timeout=1)
    coordinator.cancel()
    release.set()
    result = coordinator.shutdown()

    assert [item.outcome for item in result.results] == [
        BookOutcome.SUCCESS,
        BookOutcome.CANCELLED,
    ]
    assert result.cancelled == 1
