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

import json
from collections import Counter
from pathlib import Path
from typing import cast

import click

from audiobook_pipeline.config import load_settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.db.rows import BookRow
from audiobook_pipeline.models.library import AuditFinding, AuditReport, LibraryDiff
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
        exit_code = _run_library_surface(library_path, diff_target, checks, json_out)
        if exit_code:
            raise click.exceptions.Exit(exit_code)
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
) -> int:
    """Print a read-only filesystem audit or source-to-target comparison.

    Returns:
        One when a diff is missing books or an audit has critical findings;
        zero otherwise. Warnings remain report-only so callers can distinguish
        broken library invariants from cleanup advice.
    """
    if target is not None:
        diff = compare_libraries(source, target)
        payload = {
            "source_count": diff.source_count,
            "target_count": diff.target_count,
            "matched": len(diff.matched),
            "missing": len(diff.missing),
            "missing_books": [book.model_dump(mode="json") for book in diff.missing],
        }
        if json_out:
            click.echo(json.dumps(payload, indent=2, default=str))
        else:
            _echo_diff(diff)
        return int(bool(diff.missing))

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
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        _echo_audit(report)
    return int(report.count("critical") > 0)


def _echo_diff(diff: LibraryDiff) -> None:
    """Print a source-to-target comparison for a person to read.

    Args:
        diff: The comparison to render.
    """
    click.echo(
        f"{diff.source_count} book(s) in source, "
        f"{diff.target_count} in target, {len(diff.missing)} missing."
    )
    if not diff.missing:
        click.echo("Every source book is present in the target library.")
        return
    click.echo("\nMissing from the target library:")
    for book in sorted(diff.missing, key=lambda item: (item.author, item.title)):
        click.echo(f"  {book.title}")
        # The "author" is whichever source folder held the book, which is only
        # a person's name when the source is organised that way. Saying "under"
        # rather than "by" keeps that honest -- the path is the useful fact.
        click.echo(f"    under {book.path}")
    click.echo(
        "\nConvert them with:\n"
        "  uv run audiobook-convert <source>\n"
        "Run with --json-output to get this as JSON."
    )


def _echo_audit(report: AuditReport) -> None:
    """Print a library audit for a person to read.

    Findings are grouped by FILE rather than listed flat: a single untagged
    book otherwise produces six lines that read as six problems.

    Args:
        report: The audit to render.
    """
    click.echo(f"Audited {report.total_files} file(s) in {report.library_root}.")
    if not report.findings:
        click.echo("No problems found.")
        return

    by_path: dict[str, list[AuditFinding]] = {}
    for finding in report.findings:
        by_path.setdefault(str(finding.path or "(library)"), []).append(finding)

    for path, findings in sorted(by_path.items()):
        click.echo(f"\n{path}")
        for finding in findings:
            click.echo(f"  {finding.severity:8} {finding.message}")

    critical = report.count("critical")
    click.echo(
        f"\n{len(report.findings)} finding(s): "
        f"{critical} critical, {report.count('warning')} warning, "
        f"{report.count('info')} info."
    )
    if critical:
        # Critical means Plex or Audiobookshelf will mis-shelve the book, so
        # say what to do rather than leaving the reader to infer it.
        click.echo(
            "\nCritical findings are usually a book the catalogue could not "
            "identify.\nRe-run those books with --mode metadata to retry "
            "identity and tagging\nwithout re-encoding the audio."
        )


if __name__ == "__main__":
    main()
