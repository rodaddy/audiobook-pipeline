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
from typing import cast

import click

from audiobook_pipeline.config import load_settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.db.rows import BookRow
from audiobook_pipeline.services.audit import ALL_CHECKS, run_audit
from audiobook_pipeline.services.library import compare_libraries


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
@click.argument(
    "library_path",
    required=False,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--diff",
    "diff_target",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Compare source LIBRARY_PATH against this finished library.",
)
@click.option(
    "--check",
    "checks",
    multiple=True,
    type=click.Choice(ALL_CHECKS),
    help="Run only this read-only library check.",
)
@click.option(
    "--json-output",
    "json_out",
    is_flag=True,
    help="Render the library audit or diff as JSON.",
)
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
def main(**options: object) -> None:
    """Report on the library and the pipeline database."""
    library_path = cast(Path | None, options["library_path"])
    diff_target = cast(Path | None, options["diff_target"])
    checks = cast(tuple[str, ...], options["checks"])
    json_out = cast(bool, options["json_out"])
    profile = cast(str, options["profile"])
    status = cast(str | None, options["status"])
    failures = cast(bool, options["failures"])
    if library_path is not None:
        _run_library_surface(library_path, diff_target, checks, json_out)
        return
    _run_database_surface(profile, status, failures)


def _run_database_surface(profile: str, status: str | None, failures: bool) -> None:
    """Print the existing database-status view when no library path is given."""
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


def _run_library_surface(
    source: Path, target: Path | None, checks: tuple[str, ...], json_out: bool
) -> None:
    """Print a read-only filesystem audit or source-to-target comparison."""
    if target is not None:
        diff = compare_libraries(source, target)
        payload = {
            "source_count": diff.source_count,
            "target_count": diff.target_count,
            "matched": len(diff.matched),
            "missing": len(diff.missing),
            "missing_books": [book.model_dump(mode="json") for book in diff.missing],
        }
    else:
        report = run_audit(source, checks=checks or ALL_CHECKS)
        payload = report.model_dump(mode="json") | {
            "summary": {
                "total_issues": len(report.findings),
                "critical": report.count("critical"),
                "warning": report.count("warning"),
                "info": report.count("info"),
                "fixable": 0,
            }
        }
    if json_out:
        click.echo(__import__("json").dumps(payload, indent=2, default=str))
        return
    click.echo(payload)


if __name__ == "__main__":
    main()
