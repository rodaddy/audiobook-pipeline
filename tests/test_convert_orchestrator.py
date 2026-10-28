"""Regression coverage for the current bounded batch scheduler."""

from __future__ import annotations

from pathlib import Path

from audiobook_pipeline.models.scheduling import SchedulingOptions
from audiobook_pipeline.services.scheduler import (
    BatchScheduler,
    calculate_max_workers,
    threads_per_worker,
)


def test_automatic_worker_capacity_preserves_legacy_cpu_bound() -> None:
    assert calculate_max_workers(configured=0, cpu_count=12) == 4
    assert calculate_max_workers(configured=0, cpu_count=3) == 1


def test_configured_capacity_is_an_explicit_upper_bound() -> None:
    assert calculate_max_workers(configured=3, cpu_count=64) == 3


def test_thread_budget_preserves_single_and_parallel_worker_behavior() -> None:
    assert threads_per_worker(cpu_count=12, active_count=1) == 0
    assert threads_per_worker(cpu_count=12, active_count=4) == 2


def test_cpu_ceiling_defers_scheduling_without_losing_queued_books() -> None:
    loads = iter((80.0, 20.0))
    scheduler = BatchScheduler(
        SchedulingOptions(cpu_ceiling_pct=80, poll_interval_seconds=0.001),
        cpu_count=lambda: 12,
        cpu_load=lambda: next(loads),
    )
    coordinator = scheduler.start([Path("book")], lambda *_: lambda: True)

    blocked = coordinator.poll()
    result = coordinator.run()

    assert blocked.queued == 1
    assert blocked.active == 0
    assert result.succeeded == 1
    assert result.failed == 0


def test_empty_batch_has_a_complete_typed_result() -> None:
    result = (
        BatchScheduler(SchedulingOptions()).start([], lambda *_: lambda: True).run()
    )

    assert result.total == 0
    assert result.results == ()
