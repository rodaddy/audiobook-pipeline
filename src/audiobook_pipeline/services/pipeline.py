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
from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger

from audiobook_pipeline.config import Settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.rows import BookRow, StageRow
from audiobook_pipeline.models.book import BookDirectory
from audiobook_pipeline.models.chapter import ChapterSet
from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.models.stage import (
    PRE_COMPLETED_STAGES,
    STAGE_ORDER,
    PipelineMode,
    Stage,
    StageStatus,
)
from audiobook_pipeline.services import (
    audible,
    concat,
    convert,
    identify,
    organize,
    parse,
)
from audiobook_pipeline.utils.ffmpeg import FfmpegError
from audiobook_pipeline.utils.tagging import write_tags

log = logger.bind(stage="pipeline")


@dataclass(frozen=True)
class Identified:
    """What the pipeline worked out about one book.

    The metadata and the chapter table are produced together by the identify
    step and consumed together by everything after it, so they travel as one
    value rather than as two parameters threaded side by side.
    """

    metadata: BookMetadata
    chapters: ChapterSet


@dataclass(frozen=True)
class RunContext:
    """The three long-lived objects every stage needs.

    Grouped rather than threaded through as separate parameters: they have the
    same lifetime (one run), they are always passed together, and separately
    they push every stage function past the five-argument ceiling for no
    expressive gain.
    """

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


def _remaining(done: set[Stage], order: tuple[Stage, ...]) -> tuple[Stage, ...]:
    """The stages still to run.

    Args:
        done: Stages already completed.
        order: The full order for this mode.

    Returns:
        Stages not yet done, in order.
    """
    return tuple(stage for stage in order if stage not in done)


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
    conn: sqlite3.Connection, hash_: str, stage: Stage, status: StageStatus
) -> None:
    """Write one stage's outcome.

    Args:
        conn: Open connection.
        hash_: The book's identity.
        stage: The stage that ran.
        status: What happened.
    """
    queries.set_stage(
        conn, StageRow(book_hash=hash_, stage=stage.value, status=status.value)
    )


def _record_bookkeeping_stages(conn: sqlite3.Connection, hash_: str) -> None:
    """Record the stages that have no work of their own in this mode.

    Without this the early "already complete" return in ``process_book`` is
    UNREACHABLE: these three are in STAGE_ORDER, nothing ever marks them done,
    so `todo` never empties and every re-run redoes the whole book.

    Args:
        conn: Open connection.
        hash_: The book's identity.
    """
    for stage in (Stage.VALIDATE, Stage.ARCHIVE, Stage.CLEANUP):
        _record_stage(conn, hash_, stage, StageStatus.COMPLETED)


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
    hash_ = book_hash(book)
    row = queries.get_book(conn, hash_) or BookRow(
        book_hash=hash_,
        source_path=str(book.identity_path),
        mode=mode.value,
        file_count=len(book.files),
        total_duration=book.total_duration_ms / 1000,
    )
    queries.upsert_book(conn, row)

    for stage in PRE_COMPLETED_STAGES.get(mode, ()):
        _record_stage(conn, hash_, stage, StageStatus.COMPLETED)

    todo = _remaining(
        {Stage(s) for s in queries.completed_stages(conn, hash_)}, STAGE_ORDER[mode]
    )
    if not todo:
        log.info("{} is already complete", book.identity_path.name)
        return row

    try:
        return _run_stages(book, context, row=row, todo=todo)
    except (FfmpegError, OSError) as exc:
        log.exception("{} failed", book.identity_path.name)
        failed = row.model_copy(update={"status": "failed", "error_message": str(exc)})
        queries.update_book(conn, failed)
        return failed


def _encode_and_tag(
    context: RunContext,
    source: Path,
    identified: Identified,
    *,
    todo: tuple[Stage, ...],
    hash_: str,
) -> Path:
    """Encode the audio and write its tags.

    Args:
        context: Configuration, database, and HTTP client for this run.
        source: The joined or single input file.
        identified: The metadata to tag with and the chapters to embed.
        todo: Stages still to run.
        hash_: The book's identity, which names its scratch directory.

    Returns:
        The converted file's path.
    """
    conn = context.conn
    work_dir = context.config.paths.work_dir / hash_
    converted = work_dir / "converted.m4b"

    if Stage.CONVERT in todo:
        convert.convert_to_m4b(
            source,
            converted,
            identified.chapters,
            context.config.encoding,
            work_dir=work_dir,
        )
    _record_stage(conn, hash_, Stage.CONVERT, StageStatus.COMPLETED)

    if Stage.METADATA in todo:
        write_tags(converted, identified.metadata)
    _record_stage(conn, hash_, Stage.METADATA, StageStatus.COMPLETED)

    return converted


def _run_stages(
    book: BookDirectory,
    context: RunContext,
    *,
    row: BookRow,
    todo: tuple[Stage, ...],
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

    Returns:
        The updated record.
    """
    config, conn, client = context.config, context.conn, context.client
    hash_ = row.book_hash
    work_dir = config.paths.work_dir / hash_
    source = book.files[0].path

    chapters = concat.build_chapters(book)
    if book.is_multi_file_book and Stage.CONCAT in todo:
        source = concat.concat_files(book, work_dir / f"joined{source.suffix}")
    _record_stage(conn, hash_, Stage.CONCAT, StageStatus.COMPLETED)

    parsed = (
        parse.parse_path(book.identity_path, context.source_root)
        if context.source_root
        else None
    )
    metadata, chapters = _identify(client, book, chapters, parsed)
    _record_stage(conn, hash_, Stage.ASIN, StageStatus.COMPLETED)

    identified = Identified(metadata, chapters)
    converted = _encode_and_tag(context, source, identified, todo=todo, hash_=hash_)

    # EVERY stage is guarded, not just the expensive ones. Re-running organize
    # on a book already in the library does not overwrite -- place_book refuses
    # to -- it writes "Title (2).m4b", so an unguarded re-run DUPLICATES the
    # whole library rather than failing. Observed 2026-08-02 on the real
    # end-to-end run, which is the only place this could have shown up.
    final = organize.build_library_path(config.paths.library_dir, metadata)
    if Stage.ORGANIZE in todo:
        final = organize.place_book(converted, final)
    _record_stage(conn, hash_, Stage.ORGANIZE, StageStatus.COMPLETED)

    _record_bookkeeping_stages(conn, hash_)

    completed = _completed_row(row, metadata, chapters)
    queries.update_book(conn, completed)
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
