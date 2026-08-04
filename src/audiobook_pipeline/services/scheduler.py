"""Pollable CPU-aware scheduling without pipeline or database ownership."""

from __future__ import annotations

import os
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path

import psutil
from loguru import logger

from audiobook_pipeline.models.scheduling import (
    BatchScheduleResult,
    BookOutcome,
    BookScheduleResult,
    CpuCountSampler,
    CpuLoadSampler,
    ExecutorFactory,
    QueuedBook,
    SchedulerDependencies,
    SchedulerSnapshot,
    SchedulingOptions,
    WorkerFactory,
    WorkerFailure,
)

log = logger.bind(component="scheduler")

type CancelRequested = Callable[[], bool]


def calculate_max_workers(*, configured: int, cpu_count: int | None) -> int:
    """Resolve configured capacity or the legacy CPU-derived worker count."""
    if configured > 0:
        return configured
    usable_cpus = max(cpu_count or 1, 1)
    return max(1, min(4, usable_cpus // 3))


def threads_per_worker(*, cpu_count: int | None, active_count: int) -> int:
    """Return legacy FFmpeg thread allocation for the current active count."""
    if active_count <= 0:
        msg = "active_count must be positive"
        raise ValueError(msg)
    if active_count == 1:
        return 0
    usable_cpus = max(cpu_count or 1, 1)
    return max(1, (usable_cpus - 1) // active_count)


class BatchScheduler:
    """Build isolated coordinators with injected operating-system dependencies."""

    def __init__(
        self,
        options: SchedulingOptions,
        cpu_count: CpuCountSampler = os.cpu_count,
        cpu_load: CpuLoadSampler = lambda: psutil.cpu_percent(interval=None),
        executor_factory: ExecutorFactory = ThreadPoolExecutor,
    ) -> None:
        """Store factories; creating a coordinator allocates no worker state."""
        self._options = options
        self._dependencies = SchedulerDependencies(
            cpu_count=cpu_count,
            cpu_load=cpu_load,
            executor_factory=executor_factory,
        )

    def start(
        self, books: Sequence[Path], worker_factory: WorkerFactory
    ) -> BatchCoordinator:
        """Create a coordinator whose workers will be built inside executor tasks."""
        return BatchCoordinator(
            books=books,
            worker_factory=worker_factory,
            options=self._options,
            dependencies=self._dependencies,
        )


class BatchCoordinator:
    """Coordinate one batch through non-blocking polls and bounded waits."""

    def __init__(
        self,
        books: Sequence[Path],
        worker_factory: WorkerFactory,
        options: SchedulingOptions,
        dependencies: SchedulerDependencies,
    ) -> None:
        """Initialize queued sources without creating shared worker resources."""
        self._options = options
        self._worker_factory = worker_factory
        self._dependencies = dependencies
        self._queue = deque(
            QueuedBook(index=index, source=source) for index, source in enumerate(books)
        )
        self._results: dict[int, BookScheduleResult] = {}
        self._active: dict[Future[bool], QueuedBook] = {}
        self._cancelled = False
        self._closed = False
        self._cpu_count = max(dependencies.cpu_count() or 1, 1)
        self._worker_capacity = calculate_max_workers(
            configured=options.max_workers, cpu_count=self._cpu_count
        )
        self._executor = dependencies.executor_factory(self._worker_capacity)
        self._close_if_finished()

    @property
    def is_finished(self) -> bool:
        """Whether every input has a recorded terminal result."""
        return not self._queue and not self._active

    @property
    def result(self) -> BatchScheduleResult | None:
        """The complete ordered result once all work is accounted for."""
        if not self.is_finished:
            return None
        results = tuple(self._results[index] for index in range(len(self._results)))
        return BatchScheduleResult(
            total=len(results),
            succeeded=sum(item.outcome is BookOutcome.SUCCESS for item in results),
            failed=sum(item.outcome is BookOutcome.FAILURE for item in results),
            cancelled=sum(item.outcome is BookOutcome.CANCELLED for item in results),
            results=results,
        )

    def poll(
        self, cancel_requested: CancelRequested | None = None
    ) -> SchedulerSnapshot:
        """Collect finished work and admit capacity without waiting for a future."""
        self._collect_finished()
        if cancel_requested is not None and cancel_requested():
            self.cancel()
        cpu_load = self._admit_queued_work()
        self._collect_finished()
        self._close_if_finished()
        return self._snapshot(cpu_load)

    def wait_for_progress(self) -> None:
        """Wait at most one configured interval, then leave result collection to poll."""
        if self._active:
            wait(self._active, timeout=self._options.poll_interval_seconds)
            return
        if not self.is_finished:
            time.sleep(self._options.poll_interval_seconds)

    def run(
        self, cancel_requested: CancelRequested | None = None
    ) -> BatchScheduleResult:
        """Drive polling to completion while each wait remains bounded and cancellable."""
        while not self.is_finished:
            self.poll(cancel_requested)
            if not self.is_finished:
                self.wait_for_progress()
        result = self.result
        if result is None:
            msg = "finished coordinator did not produce a result"
            raise RuntimeError(msg)
        return result

    def cancel(self) -> None:
        """Stop admitting work and account for unscheduled or cancellable books."""
        if self._cancelled:
            return
        self._cancelled = True
        while self._queue:
            queued = self._queue.popleft()
            self._record(queued, BookOutcome.CANCELLED)
        for future in self._active:
            future.cancel()
        log.info("Batch cancellation requested")

    def shutdown(self) -> BatchScheduleResult:
        """Request cancellation and wait through bounded polls for active workers."""
        self.cancel()
        return self.run()

    def _admit_queued_work(self) -> float | None:
        if self._cancelled or not self._queue or self._closed:
            return None
        cpu_load: float | None = None
        while self._queue and len(self._active) < self._worker_capacity:
            cpu_load = self._dependencies.cpu_load()
            if cpu_load >= self._options.cpu_ceiling_pct:
                log.debug("CPU ceiling reached at {:.1f}%", cpu_load)
                break
            self._submit_next_book()
        return cpu_load

    def _submit_next_book(self) -> None:
        queued = self._queue.popleft()
        threads = threads_per_worker(
            cpu_count=self._cpu_count,
            active_count=len(self._active) + 1,
        )
        future = self._executor.submit(
            _run_worker, queued.source, threads, self._worker_factory
        )
        self._active[future] = queued
        log.debug("Submitted worker with {} FFmpeg thread(s)", threads)

    def _collect_finished(self) -> None:
        done = [future for future in self._active if future.done()]
        for future in done:
            queued = self._active.pop(future)
            self._record_future(queued, future)

    def _record_future(self, queued: QueuedBook, future: Future[bool]) -> None:
        if future.cancelled():
            self._record(queued, BookOutcome.CANCELLED)
            return
        try:
            succeeded = future.result()
        except Exception:  # noqa: BLE001 -- worker failures are terminal results.
            self._record(queued, BookOutcome.FAILURE, WorkerFailure.EXCEPTION)
            log.warning("Scheduler worker failure recorded: worker_exception")
            return
        if succeeded:
            self._record(queued, BookOutcome.SUCCESS)
            return
        self._record(queued, BookOutcome.FAILURE, WorkerFailure.REPORTED_FAILURE)

    def _record(
        self,
        queued: QueuedBook,
        outcome: BookOutcome,
        error: WorkerFailure | None = None,
    ) -> None:
        self._results[queued.index] = BookScheduleResult(
            index=queued.index, source=queued.source, outcome=outcome, error=error
        )

    def _close_if_finished(self) -> None:
        if not self.is_finished or self._closed:
            return
        self._executor.shutdown(wait=False, cancel_futures=False)
        self._closed = True

    def _snapshot(self, cpu_load: float | None) -> SchedulerSnapshot:
        return SchedulerSnapshot(
            queued=len(self._queue),
            active=len(self._active),
            completed=len(self._results),
            cpu_load_pct=cpu_load,
            finished=self.is_finished,
        )


def _run_worker(source: Path, threads: int, factory: WorkerFactory) -> bool:
    """Create and run one worker within its executor thread.

    The factory belongs inside this task so future pipeline wiring can create a
    fresh SQLite connection and API clients per worker rather than sharing one
    thread-affine object across the batch.
    """
    return factory(source, threads)()
