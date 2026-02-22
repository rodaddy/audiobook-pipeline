"""Core enums, constants, and type definitions for the audiobook pipeline."""

from dataclasses import dataclass
from enum import StrEnum


class PipelineMode(StrEnum):
    CONVERT = "convert"
    ENRICH = "enrich"
    METADATA = "metadata"
    ORGANIZE = "organize"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ErrorCategory(StrEnum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


class Stage(StrEnum):
    VALIDATE = "validate"
    CONCAT = "concat"
    CONVERT = "convert"
    ASIN = "asin"
    METADATA = "metadata"
    ORGANIZE = "organize"
    ARCHIVE = "archive"
    CLEANUP = "cleanup"


# Which stages run for each mode
STAGE_ORDER: dict[PipelineMode, list[Stage]] = {
    PipelineMode.CONVERT: [
        Stage.VALIDATE,
        Stage.CONCAT,
        Stage.CONVERT,
        Stage.ASIN,
        Stage.METADATA,
        Stage.ORGANIZE,
        Stage.ARCHIVE,
        Stage.CLEANUP,
    ],
    PipelineMode.ENRICH: [
        Stage.ASIN,
        Stage.METADATA,
        Stage.ORGANIZE,
        Stage.CLEANUP,
    ],
    PipelineMode.METADATA: [
        Stage.ASIN,
        Stage.METADATA,
        Stage.CLEANUP,
    ],
    PipelineMode.ORGANIZE: [
        Stage.ORGANIZE,
    ],
}

# Stages that are pre-completed for non-convert modes
PRE_COMPLETED_STAGES: dict[PipelineMode, list[Stage]] = {
    PipelineMode.ENRICH: [Stage.VALIDATE, Stage.CONCAT, Stage.CONVERT],
    PipelineMode.METADATA: [Stage.VALIDATE, Stage.CONCAT, Stage.CONVERT],
    PipelineMode.ORGANIZE: [
        Stage.VALIDATE,
        Stage.CONCAT,
        Stage.CONVERT,
        Stage.ASIN,
        Stage.METADATA,
    ],
}

AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".mp3",
        ".m4a",
        ".m4b",
        ".flac",
        ".ogg",
        ".wma",
    }
)


@dataclass
class BatchResult:
    """Result summary from a parallel batch conversion run."""

    completed: int = 0
    failed: int = 0
    total: int = 0
