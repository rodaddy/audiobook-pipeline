"""Persisted metadata and optional cover-art handoff for the tag stage."""

from __future__ import annotations

import json
import sqlite3

import httpx
from loguru import logger

from audiobook_pipeline.db import queries
from audiobook_pipeline.db.rows import BookRow, StageRow
from audiobook_pipeline.models.book import BookDirectory
from audiobook_pipeline.models.chapter import ChapterSet
from audiobook_pipeline.models.metadata import BookMetadata, CoverArt
from audiobook_pipeline.models.stage import (
    PipelineLevel,
    PipelineMode,
    Stage,
    StageStatus,
    stages_for,
)
from audiobook_pipeline.services.cover import fetch_cover

log = logger.bind(stage="metadata")

_IDENTITY_RECEIPTS = (
    Stage.CONVERT,
    Stage.ASIN,
    Stage.METADATA,
    Stage.ORGANIZE,
    Stage.ARCHIVE,
    Stage.CLEANUP,
)


class ResumeError(ValueError):
    """A completed stage lacks the durable handoff needed to resume it."""

    def __init__(self, stage: Stage) -> None:
        """Build an error identifying the stage that cannot resume."""
        super().__init__(f"completed {stage.value} stage lacks its durable handoff")


def skipped_row(
    book: BookDirectory, mode: PipelineMode, level: PipelineLevel, hash_: str
) -> BookRow:
    """Describe a dry-run book without opening a database or running a stage."""
    return BookRow(
        book_hash=hash_,
        source_path=str(book.identity_path),
        mode=mode.value,
        level=level.value,
        status=StageStatus.SKIPPED.value,
        file_count=len(book.files),
        total_duration=book.total_duration_ms / 1000,
    )


def should_reset_for_level(conn: sqlite3.Connection, row: BookRow) -> bool:
    """Whether a changed level can safely reopen identity-dependent work."""
    if row.status == "completed":
        return False
    done = {
        Stage(receipt.stage)
        for receipt in queries.get_stages(conn, row.book_hash)
        if receipt.status in {StageStatus.COMPLETED.value, StageStatus.SKIPPED.value}
    }
    todo = tuple(
        stage
        for stage in stages_for(PipelineMode(row.mode), PipelineLevel(row.level))
        if stage not in done
    )
    return bool(todo) and not set(todo) <= {Stage.ARCHIVE, Stage.CLEANUP}


def reset_identity_receipts(conn: sqlite3.Connection, book_hash: str) -> None:
    """Reopen all outcomes that depend on the selected metadata level."""
    for stage in _IDENTITY_RECEIPTS:
        queries.set_stage(
            conn,
            StageRow(
                book_hash=book_hash, stage=stage.value, status=StageStatus.PENDING.value
            ),
        )


def reset_stage_plan(conn: sqlite3.Connection, book_hash: str) -> None:
    """Discard incompatible receipts before a requested mode changes."""
    for stage in Stage:
        queries.set_stage(
            conn,
            StageRow(
                book_hash=book_hash, stage=stage.value, status=StageStatus.PENDING.value
            ),
        )


def _genres_from_row(value: str | None) -> tuple[str, ...]:
    """Decode current JSON genres and tolerate pre-migration comma text."""
    if not value:
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        log.warning("metadata genres rejected: encoding")
        return tuple(item.strip() for item in value.split(",") if item.strip())
    if isinstance(decoded, list) and all(isinstance(item, str) for item in decoded):
        return tuple(decoded)
    return ()


def _release_year(value: str | None) -> int | None:
    """Return an integer release year only when the stored value represents one."""
    return int(value) if value is not None and value.isdecimal() else None


def metadata_from_row(row: BookRow) -> BookMetadata:
    """Reconstruct a completed ASIN decision for a retry."""
    if row.parsed_title is None:
        raise ResumeError(Stage.ASIN)
    return BookMetadata(
        title=row.parsed_title,
        author=row.parsed_author or "",
        narrator=row.parsed_narrator or "",
        asin=row.parsed_asin or "",
        series=row.parsed_series or "",
        series_position=row.parsed_position or "",
        release_year=_release_year(row.parsed_year),
        publisher=row.parsed_publisher or "",
        summary=row.parsed_description or "",
        copyright=row.parsed_copyright or "",
        genres=_genres_from_row(row.parsed_genre),
        cover_url=row.cover_url or "",
    )


def completed_row(
    row: BookRow, metadata: BookMetadata, chapters: ChapterSet
) -> BookRow:
    """Persist every tag-relevant metadata field with the completed chapter table."""
    return row.model_copy(
        update={
            "status": "completed",
            "parsed_title": metadata.title,
            "parsed_author": metadata.author,
            "parsed_narrator": metadata.narrator,
            "parsed_asin": metadata.asin,
            "parsed_series": metadata.series,
            "parsed_position": metadata.series_position,
            "parsed_year": (
                str(metadata.release_year)
                if metadata.release_year is not None
                else None
            ),
            "parsed_publisher": metadata.publisher or None,
            "parsed_description": metadata.summary or None,
            "parsed_copyright": metadata.copyright or None,
            "parsed_genre": json.dumps(metadata.genres),
            "cover_url": metadata.cover_url or None,
            "chapter_count": len(chapters.chapters),
            "chapter_source": chapters.source,
        }
    )


def _cover_from_bytes(image: bytes | None) -> CoverArt | None:
    """Recognise cached JPEG or PNG bytes without trusting their old source."""
    if image is None:
        return None
    if image.startswith(b"\xff\xd8\xff"):
        return CoverArt(content_type="image/jpeg", data=image)
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return CoverArt(content_type="image/png", data=image)
    log.warning("cover cache rejected: image_magic")
    return None


def cover_for_metadata(
    conn: sqlite3.Connection,
    client: httpx.Client,
    book_hash: str,
    metadata: BookMetadata,
) -> CoverArt | None:
    """Load valid cached art or fetch and cache it before a tag write."""
    cached = _cover_from_bytes(queries.get_cover(conn, book_hash))
    if cached is not None:
        return cached
    if not metadata.cover_url:
        return None
    fetched = fetch_cover(client, metadata.cover_url)
    if fetched is not None:
        queries.store_cover(conn, book_hash, fetched.data)
    return fetched
