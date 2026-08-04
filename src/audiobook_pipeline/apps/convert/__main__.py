"""``audiobook-convert`` -- turn a directory of audio into library M4Bs.

Purpose:
    The entry point a user actually runs. Discovers books under a source
    directory, runs each through the pipeline, and prints a summary.

WHY --DRY-RUN EXISTS AND IS WORTH USING FIRST
    Discovery's verdict decides everything downstream: whether 19 files are one
    book or nineteen. Getting that wrong is expensive and slow to undo, and it
    is the ONE thing a user can check in a second before committing hours of
    CPU. So the classification is printable on its own.

Example:
    $ audiobook-convert /path/to/audio --dry-run
    $ audiobook-convert /path/to/audio

See Also:
    - audiobook_pipeline.services.pipeline: the spine this drives
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import click
from loguru import logger

from audiobook_pipeline.config import load_settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.models.book import BookDirectory
from audiobook_pipeline.models.stage import PipelineMode, Stage, StageStatus
from audiobook_pipeline.services.discovery import discover_books
from audiobook_pipeline.services.pipeline import (
    RunContext,
    book_from_row,
    book_hash,
    process_book,
)
from audiobook_pipeline.utils.http import build_client

log = logger.bind(stage="convert-cli")


def _describe(book: BookDirectory) -> str:
    """One line describing what discovery decided about a book.

    Args:
        book: The discovered book.

    Returns:
        A line naming the verdict, the runtime, and the path.
    """
    verdict = "concat" if book.is_multi_file_book else "single"
    hours = book.total_duration_ms / 3_600_000
    return f"  {verdict:6}  {hours:6.2f}h  {len(book.files):3} file(s)  {book.path}"


def _report(results: list[str]) -> int:
    """Print the run summary and choose an exit code.

    Args:
        results: One status string per book processed.

    Returns:
        0 when every book completed, 1 when any failed. A batch that half
        worked must NOT exit 0 -- a caller scripting this needs to know
        something needs attention without reading the log.
    """
    failed = [status for status in results if status != "completed"]
    click.echo(f"\n{len(results) - len(failed)} completed, {len(failed)} failed")
    return 1 if failed else 0


def _recovery_books(
    conn: sqlite3.Connection, books: list[BookDirectory], mode: PipelineMode
) -> list[BookDirectory]:
    """Return failed database rows no longer visible to filesystem discovery."""
    known = {book_hash(book) for book in books}
    rows = queries.list_books(conn, mode=mode.value)
    return [
        book_from_row(row)
        for row in rows
        if row.status != "completed" and row.book_hash not in known
    ]


def _simple_outputs(conn: sqlite3.Connection, source: Path) -> frozenset[Path]:
    """Read completed simple-mode outputs so discovery does not consume them."""
    outputs = {
        Path(stage.output_file).resolve()
        for row in queries.list_books(conn)
        for stage in queries.get_stages(conn, row.book_hash)
        if stage.stage == Stage.CONVERT.value
        and stage.status == StageStatus.COMPLETED.value
        and stage.output_file is not None
        and Path(stage.output_file).is_file()
        and Path(stage.output_file).resolve().is_relative_to(source.resolve())
    }
    return frozenset(outputs)


@click.command()
@click.argument(
    "source",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what discovery found and stop, without converting anything.",
)
@click.option(
    "--profile",
    default="default",
    help="Named config layer to load from config/config.{profile}.json.",
)
@click.option(
    "--mode",
    type=click.Choice([m.value for m in PipelineMode]),
    default=PipelineMode.CONVERT.value,
    help="Which stages to run.",
)
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    default=None,
    help="Process at most this many books. Useful for a first real run.",
)
def main(
    source: Path, *, dry_run: bool, profile: str, mode: str, limit: int | None
) -> None:
    """Convert the audiobooks under SOURCE into the configured library."""
    config = load_settings(profile=profile)

    with connect(config.paths.db_path) as conn:
        books = discover_books(source, excluded=_simple_outputs(conn, source))
        if limit is not None:
            click.echo(f"Limiting to {limit} of {len(books)} discovered book(s).")
            books = books[:limit]

        if dry_run:
            click.echo(f"{len(books)} book(s) under {source}:")
            for book in books:
                click.echo(_describe(book))
            return

        with build_client() as client:
            context = RunContext(
                config=config, conn=conn, client=client, source_root=source
            )
            books.extend(_recovery_books(conn, books, PipelineMode(mode)))
            results = [
                process_book(book, context, mode=PipelineMode(mode)).status
                for book in books
            ]

    sys.exit(_report(results))


if __name__ == "__main__":
    main()
