"""CLI entry point for library audit (audiobook-audit command)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import click
from loguru import logger

log = logger.bind(stage="audit-cli")


@click.command()
@click.argument("library_path", required=False, type=click.Path(exists=True))
@click.option(
    "--fix",
    is_flag=True,
    help="Auto-fix safe issues (delete leftover sources, touch stale files).",
)
@click.option(
    "--check",
    "checks",
    multiple=True,
    type=click.Choice(["tags", "duplicates", "structure", "sources", "stale"]),
    help="Run only specific check(s). Can be repeated.",
)
@click.option(
    "--json-output",
    "json_out",
    is_flag=True,
    help="Output as JSON instead of human-readable.",
)
@click.option("-v", "--verbose", is_flag=True, help="Show all files, not just issues.")
@click.option(
    "-c",
    "--config",
    "config_file",
    type=click.Path(exists=True),
    help="Path to .env file.",
)
@click.option(
    "--dry-run", is_flag=True, help="Show what --fix would do without doing it."
)
@click.option(
    "--plex-url",
    default=None,
    help="Plex server URL (default: http://10.71.1.35:32400).",
)
@click.option(
    "--diff",
    "diff_target",
    type=click.Path(exists=True),
    default=None,
    help="Compare LIBRARY_PATH (source) against this target library to find missing books.",
)
def main(
    library_path: str | None,
    fix: bool,
    checks: tuple[str, ...],
    json_out: bool,
    verbose: bool,
    config_file: str | None,
    dry_run: bool,
    plex_url: str | None,
    diff_target: str | None,
) -> None:
    """Audit an audiobook library for metadata, duplicates, and structure issues."""
    from .cli import _find_config_file, _load_env_file
    from .ops.audit import ALL_CHECKS, apply_fixes, run_audit

    # Load .env for NFS_OUTPUT_DIR default
    env_file = Path(config_file) if config_file else _find_config_file()
    if env_file and env_file.is_file():
        _load_env_file(env_file)

    # Resolve library path
    if library_path:
        lib_root = Path(library_path).resolve()
    else:
        lib_root = Path(os.environ.get("NFS_OUTPUT_DIR", "/mnt/media/AudioBooks"))
        if not lib_root.is_dir():
            raise click.UsageError(
                f"Library path not found: {lib_root}. "
                "Pass LIBRARY_PATH argument or set NFS_OUTPUT_DIR."
            )

    # Configure logging
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(
        lambda msg: click.echo(msg, err=True),
        format="{level:<8} | {message}",
        level=level,
        filter=lambda r: r["extra"].get("stage", "") in ("audit", "library-diff"),
    )

    # Handle --diff mode (separate workflow from audit checks)
    if diff_target:
        from .ops.library_diff import compare_libraries

        target_root = Path(diff_target).resolve()
        diff = compare_libraries(source=lib_root, target=target_root)
        _print_diff(diff, json_out)

        # Write report
        report_dir = Path.cwd() / ".reports"
        report_dir.mkdir(exist_ok=True)
        report_file = report_dir / "library-diff.md"
        report_file.write_text(_format_diff_markdown(diff))
        if not json_out:
            click.echo(f"\nReport saved to {report_file}")
        return

    selected_checks = checks if checks else ALL_CHECKS
    plex_url_resolved = plex_url or "http://10.71.1.35:32400"
    plex_token = os.environ.get("PLEX_TOKEN", "")

    report = run_audit(
        library_root=lib_root,
        checks=selected_checks,
        plex_url=plex_url_resolved,
        plex_token=plex_token,
    )

    # Apply fixes if requested
    fix_actions: list[str] = []
    if fix or dry_run:
        fix_actions = apply_fixes(lib_root, report.findings, dry_run=dry_run or not fix)

    # Output
    if json_out:
        output = report.to_dict()
        if fix_actions:
            output["fix_actions"] = fix_actions
        click.echo(json.dumps(output, indent=2))
    else:
        _print_report(report, fix_actions)

    # Write report file
    report_dir = Path.cwd() / ".reports"
    report_dir.mkdir(exist_ok=True)
    report_file = report_dir / "library-audit.md"
    report_file.write_text(_format_markdown_report(report, fix_actions))
    if not json_out:
        click.echo(f"\nReport saved to {report_file}")


def _print_report(report, fix_actions: list[str]) -> None:
    """Print human-readable audit report to stdout."""
    from .ops.audit import AuditReport

    click.echo(f"\nLibrary Audit Report")
    click.echo("=" * 50)
    click.echo(f"Library:            {report.library_root}")
    click.echo(f"Total files:        {report.total_files}")
    click.echo(f"Issues found:       {len(report.findings)}")
    click.echo("")
    click.echo(f"  CRITICAL  {report.critical_count}")
    click.echo(f"  WARNING   {report.warning_count}")
    click.echo(f"  INFO      {report.info_count}")
    click.echo(f"  FIXABLE   {report.fixable_count}")

    # Group by check, then by severity
    by_check: dict[str, list] = {}
    for f in report.findings:
        by_check.setdefault(f.check, []).append(f)

    severity_order = {"critical": 0, "warning": 1, "info": 2}

    for check_name, findings in sorted(by_check.items()):
        findings.sort(key=lambda f: severity_order.get(f.severity, 99))
        click.echo(f"\n{check_name.upper()} ({len(findings)} issues)")
        click.echo("-" * 50)
        for f in findings:
            marker = {"critical": "!!", "warning": " !", "info": "  "}.get(
                f.severity, "  "
            )
            fix_hint = " [fixable]" if f.fixable else ""
            click.echo(f"  {marker} {f.path}")
            click.echo(f"     {f.message}{fix_hint}")

    if fix_actions:
        click.echo(f"\nFix Actions ({len(fix_actions)})")
        click.echo("-" * 50)
        for action in fix_actions:
            click.echo(f"  {action}")

    if not report.findings:
        click.echo("\nNo issues found.")


def _format_markdown_report(report, fix_actions: list[str]) -> str:
    """Format audit report as Markdown for .reports/ file."""
    lines = [
        "# Library Audit Report",
        "",
        f"**Library:** `{report.library_root}`",
        f"**Total files scanned:** {report.total_files}",
        f"**Issues found:** {len(report.findings)}",
        "",
        "| Severity | Count |",
        "|----------|-------|",
        f"| CRITICAL | {report.critical_count} |",
        f"| WARNING  | {report.warning_count} |",
        f"| INFO     | {report.info_count} |",
        f"| FIXABLE  | {report.fixable_count} |",
        "",
    ]

    by_check: dict[str, list] = {}
    for f in report.findings:
        by_check.setdefault(f.check, []).append(f)

    severity_order = {"critical": 0, "warning": 1, "info": 2}

    for check_name, findings in sorted(by_check.items()):
        findings.sort(key=lambda f: severity_order.get(f.severity, 99))
        lines.append(f"## {check_name.title()} ({len(findings)} issues)")
        lines.append("")
        for f in findings:
            fix_hint = " *[fixable]*" if f.fixable else ""
            lines.append(f"- **{f.severity.upper()}** `{f.path}`{fix_hint}")
            lines.append(f"  - {f.message}")
        lines.append("")

    if fix_actions:
        lines.append("## Fix Actions")
        lines.append("")
        for action in fix_actions:
            lines.append(f"- {action}")
        lines.append("")

    return "\n".join(lines)


def _print_diff(diff, json_out: bool) -> None:
    """Print library diff results."""
    if json_out:
        import json as json_mod

        output = {
            "source_count": diff.source_count,
            "target_count": diff.target_count,
            "matched": len(diff.matched),
            "missing": len(diff.missing),
            "missing_books": [
                {"author": b.author, "title": b.title, "path": b.path}
                for b in diff.missing
            ],
        }
        click.echo(json_mod.dumps(output, indent=2))
        return

    click.echo(f"\nLibrary Diff Report")
    click.echo("=" * 50)
    click.echo(f"Source books:  {diff.source_count}")
    click.echo(f"Target books:  {diff.target_count}")
    click.echo(f"Matched:       {len(diff.matched)}")
    click.echo(f"Missing:       {len(diff.missing)}")

    if diff.missing:
        click.echo(f"\nMissing Books ({len(diff.missing)})")
        click.echo("-" * 50)
        by_author: dict[str, list] = {}
        for book in diff.missing:
            by_author.setdefault(book.author, []).append(book)
        for author in sorted(by_author):
            click.echo(f"\n  {author}:")
            for book in sorted(by_author[author], key=lambda b: b.title):
                click.echo(f"    - {book.title}")
    else:
        click.echo("\nAll source books found in target library.")


def _format_diff_markdown(diff) -> str:
    """Format library diff as Markdown for .reports/ file."""
    lines = [
        "# Library Diff Report",
        "",
        f"**Source books:** {diff.source_count}",
        f"**Target books:** {diff.target_count}",
        f"**Matched:** {len(diff.matched)}",
        f"**Missing:** {len(diff.missing)}",
        "",
    ]

    if diff.missing:
        lines.append("## Missing Books")
        lines.append("")
        by_author: dict[str, list] = {}
        for book in diff.missing:
            by_author.setdefault(book.author, []).append(book)
        for author in sorted(by_author):
            lines.append(f"### {author}")
            lines.append("")
            for book in sorted(by_author[author], key=lambda b: b.title):
                lines.append(f"- {book.title} (`{book.path}`)")
            lines.append("")
    else:
        lines.append("All source books found in target library.")
        lines.append("")

    return "\n".join(lines)
