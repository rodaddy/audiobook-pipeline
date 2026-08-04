"""Running one book through every stage, in order, resumably.

Purpose:
    The spine. Everything else in ``services`` does one job and knows nothing
    about what runs next; this module owns the order, the database writes, and
    the decision to skip work already done.

WHY A COMPLETED STAGE IS SKIPPED RATHER THAN REPEATED
    A 700-book run takes days and something will interrupt it. Re-running must
    pick up where it stopped, not re-encode 400 books that are already correct
    -- and the record of what finished is in the database, because a file's
    existence on disk does not say whether it was tagged.

WHY A FAILED BOOK DOES NOT STOP THE RUN
    One unreadable source out of 700 is normal. The failure is recorded against
    that book with its category, and the run continues; a batch that aborts on
    the first bad file needs a human at 3am to make any progress at all.

Example:
    >>> from audiobook_pipeline.models.stage import Stage
    >>> _remaining({Stage.VALIDATE}, (Stage.VALIDATE, Stage.CONCAT))
    (<Stage.CONCAT: 'concat'>,)

See Also:
    - audiobook_pipeline.models.stage: STAGE_ORDER and PRE_COMPLETED_STAGES
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import httpx
from loguru import logger
from mutagen import MutagenError
from pydantic import BaseModel, ConfigDict

from audiobook_pipeline.config import Settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.rows import BookRow, StageRow
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import ChapterSet
from audiobook_pipeline.models.lifecycle import StagePlan
from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.models.stage import (
    PRE_COMPLETED_STAGES,
    PipelineMode,
    Stage,
    StageStatus,
    stages_for,
)
from audiobook_pipeline.services import (
    archive,
    audible,
    cleanup,
    concat,
    convert,
    identify,
    organize,
    parse,
    validate,
)
from audiobook_pipeline.utils.ffmpeg import FfmpegError
from audiobook_pipeline.utils.tagging import write_tags

log = logger.bind(stage="pipeline")


class ResumeError(ValueError):
    """A database row says a stage completed but its durable handoff is absent."""

    def __init__(self, stage: Stage) -> None:
        """Build an error that identifies the completed stage that cannot resume."""
        super().__init__(f"completed {stage.value} stage lacks its durable handoff")


class Identified(BaseModel):
    """What the pipeline worked out about one book.

    The metadata and the chapter table are produced together by the identify
    step and consumed together by everything after it, so they travel as one
    value rather than as two parameters threaded side by side.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: BookMetadata
    chapters: ChapterSet


class RunContext(BaseModel):
    """The three long-lived objects every stage needs.

    Grouped rather than threaded through as separate parameters: they have the
    same lifetime (one run), they are always passed together, and separately
    they push every stage function past the five-argument ceiling for no
    expressive gain.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    config: Settings
    conn: sqlite3.Connection
    client: httpx.Client

    #: The directory this run was pointed at. Carried so a book whose catalogue
    #: lookup fails can still be filed under the author its own source tree
    #: names, instead of landing on the "Unknown Author" shelf. Defaults to
    #: unset, which simply means no author can be inferred from the layout.
    source_root: Path | None = None


def book_hash(book: BookDirectory) -> str:
    """A stable identity for one book.

    Args:
        book: The discovered book.

    Returns:
        A short hex digest of the identity path and total duration. Duration is
        included so the same filename holding DIFFERENT audio -- a re-rip, a
        corrected source -- is a different book rather than one silently
        reported as already converted.
    """
    material = f"{book.identity_path}:{book.total_duration_ms}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def book_from_row(row: BookRow) -> BookDirectory:
    """Make a minimal book handle for a database-only lifecycle retry."""
    source = Path(row.source_path)
    duration_ms = max(round((row.total_duration or 0.001) * 1000), 1)
    return BookDirectory(
        path=source if source.is_dir() else source.parent,
        files=(AudioFile(path=source, duration_ms=duration_ms),),
    )


def _remaining(done: set[Stage], order: tuple[Stage, ...]) -> tuple[Stage, ...]:
    """The stages still to run.

    Args:
        done: Stages already completed.
        order: The full order for this mode.

    Returns:
        Stages not yet done, in order.
    """
    return tuple(stage for stage in order if stage not in done)


def _done_stages(conn: sqlite3.Connection, hash_: str) -> set[Stage]:
    """Return stages that have completed or were explicitly skipped."""
    return {
        Stage(row.stage)
        for row in queries.get_stages(conn, hash_)
        if row.status in {StageStatus.COMPLETED.value, StageStatus.SKIPPED.value}
    }


def _reset_stage_plan(conn: sqlite3.Connection, hash_: str) -> None:
    """Discard prior-mode stage receipts before applying a new requested mode."""
    for stage in Stage:
        _record_stage(conn, hash_, stage, StageStatus.PENDING)


def _requested_row(
    book: BookDirectory, conn: sqlite3.Connection, mode: PipelineMode
) -> BookRow:
    """Create or reconcile the durable row with the caller's requested mode."""
    hash_ = book_hash(book)
    existing = queries.get_book(conn, hash_)
    if existing is None:
        return BookRow(
            book_hash=hash_,
            source_path=str(book.identity_path),
            mode=mode.value,
            file_count=len(book.files),
            total_duration=book.total_duration_ms / 1000,
        )
    if existing.mode == mode.value:
        return existing
    _reset_stage_plan(conn, hash_)
    return existing.model_copy(
        update={"mode": mode.value, "status": "pending", "error_message": None}
    )


def _title_hint(book: BookDirectory) -> str:
    """A searchable title guess from the source layout.

    Args:
        book: The discovered book.

    Returns:
        The directory name for a multi-file book, the file's stem otherwise --
        which is the level that actually names the work in both layouts.
    """
    if book.is_multi_file_book:
        return book.path.name
    return book.files[0].path.stem


def _record_stage(
    conn: sqlite3.Connection,
    hash_: str,
    stage: Stage,
    status: StageStatus,
    *,
    output_file: Path | None = None,
) -> None:
    """Write one stage's outcome.

    Args:
        conn: Open connection.
        hash_: The book's identity.
        stage: The stage that ran.
        status: What happened.
        output_file: Durable file produced by the completed stage, if any.
    """
    queries.set_stage(
        conn,
        StageRow(
            book_hash=hash_,
            stage=stage.value,
            status=status.value,
            output_file=str(output_file) if output_file is not None else None,
        ),
    )


def _fallback_metadata(claim: ParsedPath, hint: str) -> BookMetadata:
    """What to write when the catalogue cannot identify a book.

    Args:
        claim: What the source path says.
        hint: The title to use when the path named none.

    Returns:
        Metadata carrying everything the PATH knew. Audible genuinely does not
        list every novella, and a book it cannot name is still worth filing
        under its author rather than on the "Unknown Author" shelf.
    """
    return BookMetadata(
        title=claim.title or hint,
        author=claim.author,
        series=claim.series,
        series_position=claim.position,
    )


def _identify(
    client: httpx.Client,
    book: BookDirectory,
    chapters: ChapterSet,
    parsed: ParsedPath | None = None,
) -> tuple[BookMetadata, ChapterSet]:
    """Look the book up and improve its chapters if the catalogue knows better.

    Args:
        client: Shared HTTP client.
        book: The discovered book.
        chapters: The chapters derived locally.
        parsed: What the source path claims. Used to SEARCH -- a clean title
            and an author find the right book where a raw folder name does
            not -- and as the fallback when the catalogue cannot answer.

    Returns:
        The metadata to write, and the chapter table to embed. Falls back to
        what the SOURCE TREE knows when the catalogue has nothing or names a
        different work -- an unidentified book is still worth converting, just
        with less written on it.
    """
    claim = parsed or ParsedPath()
    hint = claim.title or _title_hint(book)
    fallback = _fallback_metadata(claim, hint)

    query = f"{hint} {claim.author}".strip()
    match = identify.best_match(
        audible.search(client, query), title_hint=hint, author_hint=claim.author
    )
    if match is None:
        log.warning("no catalogue match for {!r}", query)
        return fallback, chapters

    # Fetched chapters only WIN when the local table came from file boundaries.
    # Marks embedded in the source describe this exact file; the catalogue's
    # describe an edition that merely shares an ASIN.
    if chapters.source != concat.SOURCE_EMBEDDED:
        fetched = identify.fetch_chapters(
            client, match.asin, local_ms=book.total_duration_ms
        )

        # A runtime that disagrees does not merely make the CHAPTERS wrong -- it
        # says this ASIN is a different work, so its title, series, and position
        # are wrong too. Adopting the identity while rejecting its chapters is
        # how a 19-hour "Promise of Blood" was written into the library as the
        # 10.8-hour "Powder Mage Novella Collection #1" on the 2026-08-02 live
        # run: the evidence was computed, logged, and then ignored.
        if fetched.edition_mismatch:
            log.warning(
                "discarding match {!r} ({}): runtime disagrees with the audio",
                match.title,
                match.asin,
            )
            return fallback, chapters

        if not fetched.is_empty:
            return match, fetched.chapters

    return match, chapters


def process_book(
    book: BookDirectory,
    context: RunContext,
    *,
    mode: PipelineMode = PipelineMode.CONVERT,
) -> BookRow:
    """Run one book through every stage its mode requires.

    Args:
        book: The discovered book.
        context: Configuration, database, and HTTP client for this run.
        mode: What the caller asked for.

    Returns:
        The book's record, with status ``completed`` or ``failed``.
    """
    conn = context.conn
    row = _requested_row(book, conn, mode)
    hash_ = row.book_hash
    queries.upsert_book(conn, row)

    for stage in PRE_COMPLETED_STAGES.get(mode, ()):
        _record_stage(conn, hash_, stage, StageStatus.COMPLETED)

    order = stages_for(mode, context.config.level)
    todo = _remaining(_done_stages(conn, hash_), order)
    if not todo:
        log.info("{} is already complete", book.identity_path.name)
        return row

    try:
        return _run_stages(book, context, row=row, todo=todo, stages=order)
    except (FfmpegError, MutagenError, OSError, ValueError) as exc:
        logger.exception("{} failed", book.identity_path.name)
        current = queries.get_book(conn, hash_) or row
        failed = current.model_copy(
            update={"status": "failed", "error_message": str(exc)}
        )
        queries.update_book(conn, failed)
        return failed


def _stored_output(conn: sqlite3.Connection, hash_: str, stage: Stage) -> Path:
    """Read a completed stage's durable output or refuse an unsafe resume."""
    output = next(
        (
            row.output_file
            for row in queries.get_stages(conn, hash_)
            if row.stage == stage.value
        ),
        None,
    )
    if output is None or not Path(output).is_file():
        raise ResumeError(stage)
    return Path(output)


def _validated_book(
    book: BookDirectory, context: RunContext, plan: StagePlan
) -> BookDirectory:
    """Run source validation only when this mode still requires it."""
    if Stage.VALIDATE in plan.todo:
        result = validate.validate_book(
            book, context.config.paths, context.config.encoding, plan.book_hash
        )
        _record_stage(
            context.conn, plan.book_hash, Stage.VALIDATE, StageStatus.COMPLETED
        )
        return result.book
    if Stage.CONCAT in plan.todo:
        return validate.load_validated_book(book, context.config.paths, plan.book_hash)
    return book


def _concat_source(book: BookDirectory, context: RunContext, plan: StagePlan) -> Path:
    """Produce or recover the single source used by conversion."""
    source = book.files[0].path
    if not book.is_multi_file_book:
        if Stage.CONCAT in plan.todo:
            _record_stage(
                context.conn,
                plan.book_hash,
                Stage.CONCAT,
                StageStatus.COMPLETED,
                output_file=source,
            )
        return source

    joined = context.config.paths.work_dir / plan.book_hash / f"joined{source.suffix}"
    if Stage.CONCAT in plan.todo:
        source = concat.concat_files(book, joined)
        _record_stage(
            context.conn,
            plan.book_hash,
            Stage.CONCAT,
            StageStatus.COMPLETED,
            output_file=source,
        )
        return source
    return _stored_output(context.conn, plan.book_hash, Stage.CONCAT)


def _metadata_from_row(row: BookRow) -> BookMetadata:
    """Reconstruct a completed ASIN stage's durable metadata for a retry."""
    if row.parsed_title is None:
        raise ResumeError(Stage.ASIN)
    return BookMetadata(
        title=row.parsed_title,
        author=row.parsed_author or "",
        series=row.parsed_series or "",
        series_position=row.parsed_position or "",
        asin=row.parsed_asin or "",
        narrator=row.parsed_narrator or "",
    )


def _identified(
    book: BookDirectory,
    context: RunContext,
    row: BookRow,
    todo: tuple[Stage, ...],
) -> tuple[BookRow, Identified]:
    """Resolve metadata once, persisting it before later stages can fail."""
    chapters = concat.build_chapters(book)
    if Stage.ASIN not in todo:
        return row, Identified(metadata=_metadata_from_row(row), chapters=chapters)
    parsed = (
        parse.parse_path(book.identity_path, context.source_root)
        if context.source_root
        else None
    )
    metadata, chapters = _identify(context.client, book, chapters, parsed)
    updated = _completed_row(row, metadata, chapters).model_copy(
        update={"status": "pending"}
    )
    queries.update_book(context.conn, updated)
    _record_stage(context.conn, row.book_hash, Stage.ASIN, StageStatus.COMPLETED)
    return updated, Identified(metadata=metadata, chapters=chapters)


def _encode_and_tag(
    context: RunContext,
    source: Path,
    identified: Identified,
    *,
    plan: StagePlan,
) -> Path:
    """Encode the audio and write its tags.

    Args:
        context: Configuration, database, and HTTP client for this run.
        source: The joined or single input file.
        identified: The metadata to tag with and the chapters to embed.
        plan: Selected stages and the book identity for this run.

    Returns:
        The converted file's path.
    """
    conn = context.conn
    work_dir = context.config.paths.work_dir / plan.book_hash
    converted = work_dir / "converted.m4b"

    if Stage.CONVERT in plan.todo:
        convert.convert_to_m4b(
            source,
            converted,
            identified.chapters,
            context.config.encoding,
            work_dir=work_dir,
        )
        _record_stage(
            conn,
            plan.book_hash,
            Stage.CONVERT,
            StageStatus.COMPLETED,
            output_file=converted,
        )
    elif Stage.CONVERT in plan.stages:
        converted = _stored_output(conn, plan.book_hash, Stage.CONVERT)
    else:
        converted = source

    if Stage.METADATA in plan.todo:
        write_tags(converted, identified.metadata)
        _record_stage(
            conn,
            plan.book_hash,
            Stage.METADATA,
            StageStatus.COMPLETED,
            output_file=converted,
        )

    return converted


def _organize_output(
    book: BookDirectory,
    converted: Path,
    identified: Identified,
    context: RunContext,
    plan: StagePlan,
) -> Path:
    """Place a completed M4B only when this level schedules library filing."""
    if Stage.ORGANIZE not in plan.stages:
        if Stage.CONVERT in plan.stages:
            if converted.parent == book.path:
                return converted
            source_output = book.path / f"{organize.book_stem(identified.metadata)}.m4b"
            final = organize.place_book(converted, source_output)
            _record_stage(
                context.conn,
                plan.book_hash,
                Stage.CONVERT,
                StageStatus.COMPLETED,
                output_file=final,
            )
            return final
        return converted
    if Stage.ORGANIZE not in plan.todo:
        return _stored_output(context.conn, plan.book_hash, Stage.ORGANIZE)
    final = organize.build_library_path(
        context.config.paths.library_dir, identified.metadata
    )
    final = organize.place_book(converted, final)
    _record_stage(
        context.conn,
        plan.book_hash,
        Stage.ORGANIZE,
        StageStatus.COMPLETED,
        output_file=final,
    )
    return final


def _archive_and_cleanup(
    book: BookDirectory,
    final: Path,
    context: RunContext,
    plan: StagePlan,
) -> None:
    """Finish destructive lifecycle work strictly after a validated output exists."""
    if Stage.ARCHIVE in plan.todo and context.config.dry_run:
        _record_stage(context.conn, plan.book_hash, Stage.ARCHIVE, StageStatus.SKIPPED)
    elif Stage.ARCHIVE in plan.todo:
        archived = archive.archive_source(
            book.identity_path,
            final,
            context.config.paths.archive_dir,
            source_duration_ms=book.total_duration_ms,
        )
        _record_stage(
            context.conn,
            plan.book_hash,
            Stage.ARCHIVE,
            StageStatus.COMPLETED,
            output_file=archived.archive_path,
        )
    if Stage.CLEANUP in plan.todo:
        result = cleanup.cleanup_work_dir(
            context.config.paths.work_dir,
            plan.book_hash,
            enabled=context.config.cleanup_work_dir,
            dry_run=context.config.dry_run,
        )
        status = StageStatus.COMPLETED if result.attempted else StageStatus.SKIPPED
        _record_stage(context.conn, plan.book_hash, Stage.CLEANUP, status)


def _run_stages(
    book: BookDirectory,
    context: RunContext,
    *,
    row: BookRow,
    todo: tuple[Stage, ...],
    stages: tuple[Stage, ...],
) -> BookRow:
    """Execute the stages for one book.

    Split from ``process_book`` so the error handling there wraps one call
    rather than the whole body -- a try block around fifty lines catches
    failures from code it was never meant to guard.

    Args:
        book: The discovered book.
        context: Configuration, database, and HTTP client for this run.
        row: The book's current record.
        todo: Stages still to run.
        stages: The requested mode and level's complete stage plan.

    Returns:
        The updated record.
    """
    plan = StagePlan(book_hash=row.book_hash, todo=todo, stages=stages)
    if set(todo) <= {Stage.ARCHIVE, Stage.CLEANUP}:
        final = _stored_output(context.conn, plan.book_hash, Stage.ORGANIZE)
        _archive_and_cleanup(book, final, context, plan)
        completed = row.model_copy(update={"status": "completed"})
        queries.update_book(context.conn, completed)
        return completed

    validated = _validated_book(book, context, plan)
    source = _concat_source(validated, context, plan)
    row, identified = _identified(validated, context, row, todo)
    converted = _encode_and_tag(context, source, identified, plan=plan)
    final = _organize_output(validated, converted, identified, context, plan)
    _archive_and_cleanup(validated, final, context, plan)

    completed = _completed_row(row, identified.metadata, identified.chapters)
    queries.update_book(context.conn, completed)
    log.success("{} -> {}", book.identity_path.name, final)
    return completed


def _completed_row(
    row: BookRow, metadata: BookMetadata, chapters: ChapterSet
) -> BookRow:
    """Fold what the run learned back into the book's record.

    Args:
        row: The record as it stood before the run.
        metadata: What the catalogue said this book is.
        chapters: The table actually written into the file.

    Returns:
        The updated record.
    """
    return row.model_copy(
        update={
            "status": "completed",
            "parsed_title": metadata.title,
            "parsed_author": metadata.author,
            "parsed_series": metadata.series,
            "parsed_position": metadata.series_position,
            "parsed_asin": metadata.asin,
            "parsed_narrator": metadata.narrator,
            "chapter_count": len(chapters.chapters),
            "chapter_source": chapters.source,
        }
    )
