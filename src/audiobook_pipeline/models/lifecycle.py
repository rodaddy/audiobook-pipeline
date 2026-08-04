"""Validated values passed between lifecycle services."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from audiobook_pipeline.models.book import BookDirectory
from audiobook_pipeline.models.stage import Stage


class ValidatedBook(BaseModel):
    """A source book whose current files have passed validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    book: BookDirectory
    file_list: Path
    target_bitrate_kbps: int = Field(gt=0)


class ArchivedSource(BaseModel):
    """The original source after it has moved under the archive root."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_path: Path
    archive_path: Path
    original_count: int = Field(gt=0)


class CleanupResult(BaseModel):
    """The isolated scratch-directory cleanup outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    work_dir: Path
    removed: bool
    attempted: bool


class StagePlan(BaseModel):
    """The selected and incomplete stages for one resumable book run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    book_hash: str = Field(min_length=1)
    todo: tuple[Stage, ...]
    stages: tuple[Stage, ...]
