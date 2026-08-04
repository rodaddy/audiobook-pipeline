"""Validated contracts for CPU-aware batch scheduling."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

type CpuCountSampler = Callable[[], int | None]
type CpuLoadSampler = Callable[[], float]
type Worker = Callable[[], bool]
type WorkerFactory = Callable[[Path, int], Worker]
type ExecutorFactory = Callable[[int], Executor]


class BookOutcome(StrEnum):
    """The terminal outcome for one submitted source directory."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


class WorkerFailure(StrEnum):
    """Safe failure categories exposed from independently running workers."""

    REPORTED_FAILURE = "worker_reported_failure"
    EXCEPTION = "worker_exception"


class SchedulingOptions(BaseModel):
    """Explicit resource limits for one batch coordinator.

    ``max_workers=0`` preserves legacy automatic sizing. Any positive value is
    used as given; the scheduler never adds a separate hidden capacity cap.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_workers: int = Field(default=0, ge=0)
    cpu_ceiling_pct: float = Field(default=80.0, gt=0, le=100)
    poll_interval_seconds: float = Field(default=0.1, gt=0)


class BookScheduleResult(BaseModel):
    """The terminal, input-order-preserving result for one scheduled book."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    source: Path
    outcome: BookOutcome
    error: WorkerFailure | None = None


class QueuedBook(BaseModel):
    """A source retaining its original position for deterministic results."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    source: Path


class SchedulerDependencies(BaseModel):
    """Injected operating-system and executor boundaries for a coordinator."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    cpu_count: CpuCountSampler
    cpu_load: CpuLoadSampler
    executor_factory: ExecutorFactory


class BatchScheduleResult(BaseModel):
    """A complete, deterministically ordered accounting of a submitted batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total: int = Field(ge=0)
    succeeded: int = Field(ge=0)
    failed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    results: tuple[BookScheduleResult, ...]

    @model_validator(mode="after")
    def validate_accounting(self) -> Self:
        """Require exactly one terminal result for every submitted input."""
        counts_match = self.succeeded + self.failed + self.cancelled == self.total
        indices = tuple(result.index for result in self.results)
        if not counts_match or len(self.results) != self.total:
            msg = "batch counts must account for every result exactly once"
            raise ValueError(msg)
        if indices != tuple(range(self.total)):
            msg = "batch results must be ordered by contiguous input index"
            raise ValueError(msg)
        return self


class SchedulerSnapshot(BaseModel):
    """A non-blocking view of a coordinator after one polling cycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    queued: int = Field(ge=0)
    active: int = Field(ge=0)
    completed: int = Field(ge=0)
    cpu_load_pct: float | None = Field(default=None, ge=0, le=100)
    finished: bool
