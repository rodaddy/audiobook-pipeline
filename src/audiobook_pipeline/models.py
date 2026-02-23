"""Core enums, constants, and type definitions for the audiobook pipeline.

Enums:
    PipelineMode   -- Pipeline operation mode (convert, enrich, metadata, organize).
    PipelineLevel  -- Intelligence tier (simple, normal, ai, full). Controls AI
                      availability and stage filtering. Simple/normal never use LLM;
                      ai/full enable LLM disambiguation and resolution.
    Stage          -- Individual pipeline stage (validate through cleanup).
    StageStatus    -- Stage execution state (pending, running, completed, failed).
    ErrorCategory  -- Error classification for retry logic (transient, permanent).
"""

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


class PipelineLevel(StrEnum):
    """Intelligence tier controlling AI usage and stage filtering.

    simple  -- convert + tag only, output stays in source dir (no AI)
    normal  -- convert + tag + best-effort organize (no AI)
    ai      -- full pipeline with LLM-assisted metadata resolution
    full    -- ai + interactive agent guidance (see docs/install.md)
    """

    SIMPLE = "simple"
    NORMAL = "normal"
    AI = "ai"
    FULL = "full"


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
        Stage.ASIN,
        Stage.METADATA,
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

# Extensions that need conversion (excludes .m4b -- already converted)
CONVERTIBLE_EXTENSIONS: frozenset[str] = AUDIO_EXTENSIONS - {".m4b"}


@dataclass
class BatchResult:
    """Result summary from a parallel batch conversion run."""

    completed: int = 0
    failed: int = 0
    total: int = 0
