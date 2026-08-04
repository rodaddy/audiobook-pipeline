"""Validated contracts for unattended inbox polling and durable claims."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClaimStatus(StrEnum):
    """The durable lifecycle state of one inbox candidate."""

    CLAIMED = "claimed"
    RETRYABLE = "retryable"
    QUARANTINING = "quarantining"
    COMPLETED = "completed"
    QUARANTINED = "quarantined"


class WatchOptions(BaseModel):
    """Explicit paths and bounded policy for one watch runner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    inbox_dir: Path
    state_db: Path
    quarantine_dir: Path
    stability_seconds: float = Field(ge=0)
    max_retries: int = Field(ge=0, le=10)
    poll_interval_seconds: float = Field(gt=0)
    failure_webhook_url: str = ""
    webhook_timeout_seconds: float = Field(default=10.0, gt=0, le=600)

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        """Keep state and quarantine writes outside the watched inbox."""
        locations = {self.inbox_dir, self.state_db, self.quarantine_dir}
        if len(locations) != 3:
            msg = "inbox, state database, and quarantine paths must differ"
            raise ValueError(msg)
        inbox = self.inbox_dir.resolve()
        if self.state_db.resolve().is_relative_to(inbox):
            msg = "state database must not be stored inside the inbox"
            raise ValueError(msg)
        if self.quarantine_dir.resolve().is_relative_to(inbox):
            msg = "quarantine directory must not be stored inside the inbox"
            raise ValueError(msg)
        return self


class ObservationEntry(BaseModel):
    """One direct audio member included in an aggregate stability signature."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    modified_ns: int = Field(ge=0)


class FileObservation(BaseModel):
    """One non-mutating aggregate observation used to establish stability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: Path
    entries: tuple[ObservationEntry, ...] = Field(min_length=1)
    first_seen_at: float


class WatchCandidate(BaseModel):
    """A supported inbox file or direct-audio directory observed as stable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1)
    source: Path


class ClaimedCandidate(WatchCandidate):
    """A candidate exclusively claimed for one processing attempt."""

    attempt: int = Field(ge=1)


class WatchClaim(BaseModel):
    """The persisted state that prevents duplicate processing after restart."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1)
    source: Path
    attempt: int = Field(ge=1)
    status: ClaimStatus
    quarantine_dir: Path | None = None

    @model_validator(mode="after")
    def validate_quarantine_transition(self) -> Self:
        """Require a durable destination for each quarantine transition state."""
        needs_destination = self.status in {
            ClaimStatus.QUARANTINING,
            ClaimStatus.QUARANTINED,
        }
        if needs_destination and self.quarantine_dir is None:
            msg = "quarantine states require a destination directory"
            raise ValueError(msg)
        if not needs_destination and self.quarantine_dir is not None:
            msg = "non-quarantine states must not carry a destination directory"
            raise ValueError(msg)
        return self


class WebhookPayload(BaseModel):
    """The redacted failure notification sent after a quarantine succeeds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    status: Literal[ClaimStatus.QUARANTINED] = ClaimStatus.QUARANTINED


class WatchPollResult(BaseModel):
    """A counted outcome from one non-blocking inbox poll."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observed: int = Field(ge=0)
    unsupported: int = Field(ge=0)
    unstable: int = Field(ge=0)
    claimed: int = Field(ge=0)
    completed: int = Field(ge=0)
    retried: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    stopped: bool


class WatchPollCounters(WatchPollResult):
    """Mutable local counters turned into one immutable poll result."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    observed: int = 0
    unsupported: int = 0
    unstable: int = 0
    claimed: int = 0
    completed: int = 0
    retried: int = 0
    quarantined: int = 0
    stopped: bool = False

    def result(self, *, stopped: bool) -> WatchPollResult:
        """Build the immutable, validated scan outcome."""
        return WatchPollResult.model_validate(self.model_dump() | {"stopped": stopped})


type Clock = Callable[[], float]
type Sleeper = Callable[[float], None]
type Processor = Callable[[], bool]
type ProcessFactory = Callable[[ClaimedCandidate], Processor]
type StopRequested = Callable[[], bool]
type WebhookNotifier = Callable[[str, WebhookPayload, float], bool]
type QuarantineMover = Callable[[Path, Path], None]
