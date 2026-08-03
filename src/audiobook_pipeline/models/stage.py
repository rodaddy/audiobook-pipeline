"""Pipeline stages, their order per mode, and what a stage run produced.

Purpose:
    A conversion is a fixed sequence of stages, and which stages run depends on
    what the caller asked for: a full convert does all eight, while `organize`
    on an already-converted library does three. The order and the skip-list are
    declared here, once, as data.

WHY THE ORDER IS DATA AND NOT CONTROL FLOW
    Expressed as branching -- ``if mode == ORGANIZE: skip convert`` -- the same
    knowledge ends up restated at every call site that needs to know what runs
    next, and they drift. As a mapping it can be iterated, tested exhaustively
    (every mode HAS an order), and printed in a log line without re-deriving it.

Key Components:
    - Stage / StageStatus / PipelineMode / ErrorCategory: the vocabulary
    - STAGE_ORDER: which stages run, in sequence, for each mode
    - PRE_COMPLETED_STAGES: what a mode treats as already done
    - StageResult: what one stage run produced, including why it failed

Example:
    >>> STAGE_ORDER[PipelineMode.ORGANIZE]
    (<Stage.ASIN: 'asin'>, <Stage.METADATA: 'metadata'>, <Stage.ORGANIZE: 'organize'>)

See Also:
    - audiobook_pipeline.config: the level that decides whether AI stages run
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PipelineMode(StrEnum):
    """What the caller asked the pipeline to do."""

    CONVERT = "convert"
    ENRICH = "enrich"
    METADATA = "metadata"
    ORGANIZE = "organize"


class Stage(StrEnum):
    """One step of a conversion, in canonical order."""

    VALIDATE = "validate"
    CONCAT = "concat"
    CONVERT = "convert"
    ASIN = "asin"
    METADATA = "metadata"
    ORGANIZE = "organize"
    ARCHIVE = "archive"
    CLEANUP = "cleanup"


class StageStatus(StrEnum):
    """Where a stage got to."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorCategory(StrEnum):
    """Whether a failure is worth retrying.

    The distinction drives real behaviour: a transient failure (network,
    rate limit, a locked file) is retried with backoff, while a permanent one
    (no ASIN match, corrupt source) is recorded and skipped. Retrying a
    permanent failure three times just delays the report by three attempts.
    """

    TRANSIENT = "transient"
    PERMANENT = "permanent"


#: Which stages run, in order, for each mode. Every mode must appear here --
#: a missing entry means a mode that silently runs nothing, so the test
#: asserting exhaustiveness is doing real work.
STAGE_ORDER: dict[PipelineMode, tuple[Stage, ...]] = {
    PipelineMode.CONVERT: (
        Stage.VALIDATE,
        Stage.CONCAT,
        Stage.CONVERT,
        Stage.ASIN,
        Stage.METADATA,
        Stage.ORGANIZE,
        Stage.ARCHIVE,
        Stage.CLEANUP,
    ),
    PipelineMode.ENRICH: (
        Stage.ASIN,
        Stage.METADATA,
        Stage.ORGANIZE,
        Stage.ARCHIVE,
        Stage.CLEANUP,
    ),
    PipelineMode.METADATA: (Stage.ASIN, Stage.METADATA),
    PipelineMode.ORGANIZE: (Stage.ASIN, Stage.METADATA, Stage.ORGANIZE),
}

#: Stages a mode treats as already done. Recorded rather than merely skipped,
#: so a resumed run can tell "this never needed to happen" apart from "this
#: has not happened yet" -- which is the difference between reporting a book
#: complete and re-converting it.
PRE_COMPLETED_STAGES: dict[PipelineMode, tuple[Stage, ...]] = {
    PipelineMode.ENRICH: (Stage.VALIDATE, Stage.CONCAT, Stage.CONVERT),
    PipelineMode.METADATA: (Stage.VALIDATE, Stage.CONCAT, Stage.CONVERT),
    PipelineMode.ORGANIZE: (Stage.VALIDATE, Stage.CONCAT, Stage.CONVERT),
}


class StageResult(BaseModel):
    """What one stage run produced.

    A failed result carries its category and message rather than raising,
    because the pipeline decides what to do next: a transient failure goes back
    on the queue, a permanent one is recorded against the book and the run
    continues with the next.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: Stage
    status: StageStatus

    #: Empty on success. On failure this is what gets written to the database
    #: and shown in the report, so it must say what went wrong -- not "failed".
    message: str = ""

    #: Only meaningful when status is FAILED.
    error_category: ErrorCategory | None = None

    duration_ms: int = Field(default=0, ge=0)

    @property
    def succeeded(self) -> bool:
        """Whether this stage completed."""
        return self.status is StageStatus.COMPLETED

    @property
    def is_retryable(self) -> bool:
        """Whether the pipeline should try this stage again."""
        return (
            self.status is StageStatus.FAILED
            and self.error_category is ErrorCategory.TRANSIENT
        )
