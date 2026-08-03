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

import sys
from pathlib import Path

import click
from loguru import logger

from audiobook_pipeline.config import load_settings
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.models.book import BookDirectory
from audiobook_pipeline.models.stage import PipelineMode
from audiobook_pipeline.services.discovery import discover_books
from audiobook_pipeline.services.pipeline import RunContext, process_book
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

    books = discover_books(source)
    if limit is not None:
        # Reported, never silent. A truncated run that looks like a complete
        # one is how a library ends up half-converted with nothing saying so.
        click.echo(f"Limiting to {limit} of {len(books)} discovered book(s).")
        books = books[:limit]

    if dry_run:
        click.echo(f"{len(books)} book(s) under {source}:")
        for book in books:
            click.echo(_describe(book))
        return

    with connect(config.paths.db_path) as conn, build_client() as client:
        context = RunContext(config, conn, client)
        results = [
            process_book(book, context, mode=PipelineMode(mode)).status
            for book in books
        ]

    sys.exit(_report(results))


if __name__ == "__main__":
    main()
