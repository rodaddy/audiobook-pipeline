"""``audiobook-audit`` -- report what the library and database contain.

Purpose:
    Read-only. Answers "what is in there, and what is wrong with it" without
    changing anything, which is what makes it safe to run against a live
    library mid-conversion.

WHY THIS IS READ-ONLY AND STAYS THAT WAY
    A reporter that starts mutating state is more dangerous than the problem it
    solves: its output stops being evidence of what WAS and becomes evidence of
    what it just did. Fixes belong in ``audiobook-convert``, which records what
    it changed.

Example:
    $ audiobook-audit
    $ audiobook-audit --status failed

See Also:
    - audiobook_pipeline.db.queries: every read this performs
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import click

from audiobook_pipeline.config import load_settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.db.rows import BookRow


def _library_counts(library: Path) -> tuple[int, int]:
    """Count what is actually on disk.

    Args:
        library: The library root.

    Returns:
        Number of M4B files and number of author directories. Counted from the
        FILESYSTEM, not the database: the two disagreeing is the single most
        useful thing this command can report.
    """
    if not library.is_dir():
        return 0, 0
    books = sum(1 for _ in library.rglob("*.m4b"))
    authors = sum(
        1 for p in library.iterdir() if p.is_dir() and not p.name.startswith(".")
    )
    return books, authors


def _print_books(books: list[BookRow]) -> None:
    """Print one line per book.

    Args:
        books: The records to show.
    """
    for book in books:
        title = book.parsed_title or Path(book.source_path).name
        click.echo(f"  {book.status:10}  {book.book_hash}  {title}")


@click.command()
@click.option(
    "--profile",
    default="default",
    help="Named config layer to load from config/config.{profile}.json.",
)
@click.option(
    "--status",
    default=None,
    help="Show only books with this status (pending, completed, failed).",
)
@click.option(
    "--failures",
    is_flag=True,
    help="Show the recorded error for every failed book.",
)
def main(*, profile: str, status: str | None, failures: bool) -> None:
    """Report on the library and the pipeline database."""
    config = load_settings(profile=profile, configure_logging=False)

    files, authors = _library_counts(config.paths.library_dir)
    click.echo(f"Library: {config.paths.library_dir}")
    click.echo(f"  {files} m4b file(s) across {authors} author folder(s)")

    with connect(config.paths.db_path) as conn:
        books = queries.list_books(conn, status=status)
        counts = Counter(book.status for book in books)

        click.echo(f"\nDatabase: {config.paths.db_path}")
        click.echo(f"  {len(books)} book(s) recorded")
        for state, count in sorted(counts.items()):
            click.echo(f"    {state:10} {count}")

        if failures:
            failed = [b for b in books if b.status == "failed"]
            click.echo(f"\n{len(failed)} failure(s):")
            for book in failed:
                name = book.parsed_title or Path(book.source_path).name
                click.echo(f"  {name}\n    {book.error_message}")
        elif status is not None:
            click.echo()
            _print_books(books)


if __name__ == "__main__":
    main()
