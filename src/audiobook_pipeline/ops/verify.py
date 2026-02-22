"""Post-run data quality verification for the audiobook library.

Scans a library (or dry-run output) for consistency issues that
pass functional tests but produce messy results:
- Author name variations (same person, different spellings)
- Books landing in _unsorted
- Duplicate titles under the same author
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import click
from loguru import logger

log = logger.bind(stage="verify")


def verify_library(library_root: Path) -> dict:
    """Scan a library directory and return data quality findings.

    Returns dict with keys:
        author_variations: list of {surname, variants: [str], count: int}
        unsorted: list of paths in _unsorted/
        duplicate_titles: list of {author, title, paths: [str]}
        summary: {total_authors, total_books, issues: int}
    """
    if not library_root.is_dir():
        return {
            "author_variations": [],
            "unsorted": [],
            "duplicate_titles": [],
            "summary": {"total_authors": 0, "total_books": 0, "issues": 0},
        }

    # Collect top-level author folders
    authors: list[str] = []
    unsorted_books: list[str] = []
    # Map: author -> list of (title_folder, full_path)
    author_titles: dict[str, list[tuple[str, Path]]] = defaultdict(list)

    for item in sorted(library_root.iterdir()):
        if not item.is_dir():
            continue
        if item.name == "_unsorted":
            # Collect everything under _unsorted
            for book in _walk_books(item):
                unsorted_books.append(str(book.relative_to(library_root)))
            continue
        authors.append(item.name)
        for book_dir in _walk_books(item):
            title = book_dir.name
            author_titles[item.name].append((title, book_dir))

    # Find author name variations by surname
    surname_map: dict[str, list[str]] = defaultdict(list)
    for author in authors:
        surname = _extract_surname(author)
        if surname:
            surname_map[surname].append(author)

    author_variations = []
    for surname, variants in sorted(surname_map.items()):
        if len(variants) > 1:
            author_variations.append(
                {
                    "surname": surname,
                    "variants": sorted(variants),
                    "count": len(variants),
                }
            )

    # Find duplicate titles under the same author
    duplicate_titles = []
    for author, titles in author_titles.items():
        title_groups: dict[str, list[Path]] = defaultdict(list)
        for title, path in titles:
            norm = _normalize_title(title)
            title_groups[norm].append(path)
        for norm_title, paths in title_groups.items():
            if len(paths) > 1:
                duplicate_titles.append(
                    {
                        "author": author,
                        "title": norm_title,
                        "paths": [str(p.relative_to(library_root)) for p in paths],
                    }
                )

    total_issues = len(author_variations) + len(unsorted_books) + len(duplicate_titles)

    return {
        "author_variations": author_variations,
        "unsorted": unsorted_books,
        "duplicate_titles": duplicate_titles,
        "summary": {
            "total_authors": len(authors),
            "total_books": sum(len(v) for v in author_titles.values()),
            "issues": total_issues,
        },
    }


def verify_dryrun_log(log_path: Path) -> dict:
    """Parse a dry-run log file and check output paths for consistency.

    Extracts destination paths from "[DRY-RUN] Would copy/move ... -> /path"
    lines and runs the same data quality checks.
    """
    if not log_path.is_file():
        return {"error": f"Log file not found: {log_path}"}

    text = log_path.read_text()
    # Extract destination paths: "       -> /Volumes/media_files/AudioBooks/Author/..."
    dest_pattern = re.compile(r"^\s+->\s+(.+)$", re.MULTILINE)
    dest_paths = [m.group(1).strip() for m in dest_pattern.finditer(text)]

    if not dest_paths:
        return {
            "error": "No destination paths found in log",
            "raw_lines": len(text.splitlines()),
        }

    # Find common root (the library root)
    library_root = _find_common_root(dest_paths)
    if not library_root:
        return {"error": "Could not determine library root from paths"}

    # Parse paths into author/title structure
    authors: set[str] = set()
    unsorted_books: list[str] = []
    author_titles: dict[str, list[str]] = defaultdict(list)

    for dest in dest_paths:
        try:
            rel = Path(dest).relative_to(library_root)
        except ValueError:
            continue
        parts = rel.parts
        if not parts:
            continue

        if parts[0] == "_unsorted":
            unsorted_books.append(str(rel))
            continue

        author = parts[0]
        authors.add(author)
        # Title is the last part of the path
        title = parts[-1] if len(parts) > 1 else parts[0]
        author_titles[author].append(title)

    # Surname variation analysis
    surname_map: dict[str, list[str]] = defaultdict(list)
    for author in sorted(authors):
        surname = _extract_surname(author)
        if surname:
            if author not in surname_map[surname]:
                surname_map[surname].append(author)

    author_variations = []
    for surname, variants in sorted(surname_map.items()):
        if len(variants) > 1:
            author_variations.append(
                {
                    "surname": surname,
                    "variants": sorted(variants),
                    "count": len(variants),
                }
            )

    # Duplicate title detection
    duplicate_titles = []
    for author, titles in author_titles.items():
        title_counts: dict[str, int] = defaultdict(int)
        for t in titles:
            title_counts[_normalize_title(t)] += 1
        for norm_title, count in title_counts.items():
            if count > 1:
                duplicate_titles.append(
                    {
                        "author": author,
                        "title": norm_title,
                        "count": count,
                    }
                )

    total_issues = len(author_variations) + len(unsorted_books) + len(duplicate_titles)

    return {
        "author_variations": author_variations,
        "unsorted": unsorted_books,
        "duplicate_titles": duplicate_titles,
        "summary": {
            "total_authors": len(authors),
            "total_destinations": len(dest_paths),
            "issues": total_issues,
        },
    }


def print_report(results: dict) -> None:
    """Print a human-readable data quality report."""
    summary = results.get("summary", {})
    click.echo(f"\nData Quality Report")
    click.echo(f"{'=' * 50}")
    click.echo(f"Authors: {summary.get('total_authors', '?')}")
    click.echo(
        f"Books: {summary.get('total_books', summary.get('total_destinations', '?'))}"
    )
    click.echo(f"Issues: {summary.get('issues', '?')}")

    variations = results.get("author_variations", [])
    if variations:
        click.echo(f"\nAuthor Name Variations ({len(variations)} groups)")
        click.echo("-" * 50)
        for v in variations:
            click.echo(f"  Surname '{v['surname']}' has {v['count']} spellings:")
            for name in v["variants"]:
                click.echo(f"    - {name}")

    unsorted = results.get("unsorted", [])
    if unsorted:
        click.echo(f"\nBooks in _unsorted ({len(unsorted)})")
        click.echo("-" * 50)
        for path in unsorted:
            click.echo(f"  {path}")

    duplicates = results.get("duplicate_titles", [])
    if duplicates:
        click.echo(f"\nDuplicate Titles ({len(duplicates)})")
        click.echo("-" * 50)
        for d in duplicates:
            click.echo(f"  {d['author']}: {d['title']}")
            if "paths" in d:
                for p in d["paths"]:
                    click.echo(f"    - {p}")

    if not variations and not unsorted and not duplicates:
        click.echo("\nNo data quality issues found.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _walk_books(author_dir: Path) -> list[Path]:
    """Find book directories (leaf dirs with files) under an author dir."""
    books = []
    for item in sorted(author_dir.rglob("*")):
        if item.is_dir() and any(item.iterdir()):
            # Check if this dir has actual files (not just subdirs)
            has_files = any(f.is_file() for f in item.iterdir())
            if has_files:
                books.append(item)
    return books


def _extract_surname(name: str) -> str:
    """Extract surname from author name for grouping."""
    if not name:
        return ""
    parts = re.split(r",\s*|\s+and\s+", name)
    last_author = parts[-1].strip()
    words = last_author.split()
    if not words:
        return ""
    surname = words[-1].lower().rstrip(".,;:")
    return surname


def _normalize_title(title: str) -> str:
    """Normalize a title for duplicate detection."""
    s = title.lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _find_common_root(paths: list[str]) -> Path | None:
    """Find the common root directory from a list of paths."""
    if not paths:
        return None
    parts_list = [Path(p).parts for p in paths]
    common = []
    for i, part in enumerate(parts_list[0]):
        if all(len(p) > i and p[i] == part for p in parts_list):
            common.append(part)
        else:
            break
    return Path(*common) if common else None
