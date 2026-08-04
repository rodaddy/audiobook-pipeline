"""Read-only scanning and comparison of loose and finished audiobook libraries."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.book import SOURCE_EXTENSIONS
from audiobook_pipeline.models.library import LibraryBook, LibraryDiff
from audiobook_pipeline.services.matching import normalize_title, titles_match
from audiobook_pipeline.services.names import normalize_author

log = logger.bind(stage="library")

TARGET_EXTENSIONS = frozenset({".m4b"})
_CONTAINER_FOLDERS = frozenset({
    "newbooks",
    "original",
    "incoming",
    "unsorted",
    "_unsorted",
    "to_fix",
    "downloads",
    "new",
})
_PART = re.compile(
    r"(?:[,\s]+part\s+\d+(?:\s+of\s+\d+)?|\s*\d{1,3}-\d{1,3})\s*$", re.IGNORECASE
)
_CHAPTER = re.compile(r"^(?:ch)?\d{1,3}[a-d]?\s*[-\.]\s*", re.IGNORECASE)
_CHAPTER_OR_SERIES_PREFIX = re.compile(
    r"^(?:ch)?\d{1,3}[a-d]?\s*[-\.]\s*|^\d{1,3}-\d{1,3}\s+|^hp[\.\s]*\d+\s*[-\.]\s*",
    re.IGNORECASE,
)
_RESIDUAL_DISC = re.compile(r"\s+\d{1,2}$")


def scan_library(root: Path, *, extensions: frozenset[str]) -> tuple[LibraryBook, ...]:
    """Scan audio files and collapse chapter/part files into logical books."""
    if not root.is_dir():
        return ()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
    )
    entries = [
        _entry_for(path, root)
        for path in files
        if len(path.relative_to(root).parts) > 1
    ]
    return _collapse(entries)


def compare_libraries(source: Path, target: Path) -> LibraryDiff:
    """Return source books that have no title match in the finished target."""
    target_books = scan_library(target, extensions=TARGET_EXTENSIONS)
    source_books = _deduplicate(scan_library(source, extensions=SOURCE_EXTENSIONS))
    target_by_author: dict[str, set[str]] = defaultdict(set)
    for book in target_books:
        target_by_author[book.author_key].add(book.title_key)
    all_titles = set().union(*target_by_author.values()) if target_by_author else set()
    matched, missing = _partition(source_books, target_by_author, all_titles)
    log.info(
        "library diff source={} target={} missing={}",
        len(source_books),
        len(target_books),
        len(missing),
    )
    return LibraryDiff(
        matched=tuple(matched),
        missing=tuple(missing),
        source_count=len(source_books),
        target_count=len(target_books),
    )


def _entry_for(path: Path, root: Path) -> tuple[LibraryBook, str]:
    """Convert one file into its metadata plus multipart grouping key."""
    rel = path.relative_to(root)
    author = _author_for(rel)
    stem = path.stem
    title, multipart, group = _multipart_title(stem, path.parent, root)
    book = LibraryBook(
        author=author,
        author_key=normalize_author(author),
        title=title,
        title_key=normalize_title(_strip_author_prefix(title, author)),
        path=rel,
        multipart=multipart,
    )
    return book, group


def _author_for(rel: Path) -> str:
    """Choose the first non-container directory as the author label."""
    candidates = (
        part
        for part in rel.parts[:-1]
        if part.lower() not in _CONTAINER_FOLDERS and "audiobook" not in part.lower()
    )
    return next(candidates, rel.parts[0])


def _multipart_title(stem: str, parent: Path, root: Path) -> tuple[str, bool, str]:
    """Return logical title and grouping key for parts or numbered chapters."""
    stripped = _RESIDUAL_DISC.sub("", _PART.sub("", stem).strip()).strip()
    if stripped != stem and stripped:
        return (
            stripped,
            True,
            f"title:{parent.relative_to(root)}:{normalize_title(stripped)}",
        )
    if _CHAPTER_OR_SERIES_PREFIX.match(stem):
        return parent.name, True, f"directory:{parent.relative_to(root)}"
    return stem, False, f"file:{parent.relative_to(root)}:{stem}"


def _strip_author_prefix(title: str, author: str) -> str:
    """Remove a leading author credit without treating ordinary dashes as noise."""
    prefix, separator, remainder = title.partition(" - ")
    if separator and normalize_author(prefix) == normalize_author(author):
        return remainder
    return title


def _collapse(entries: list[tuple[LibraryBook, str]]) -> tuple[LibraryBook, ...]:
    """Keep one deterministic representative for each multipart group."""
    groups: dict[str, LibraryBook] = {}
    for book, group in entries:
        groups.setdefault(group, book)
    return tuple(groups.values())


def _deduplicate(books: tuple[LibraryBook, ...]) -> tuple[LibraryBook, ...]:
    """Keep the first source occurrence of a non-empty normalized title."""
    seen: set[str] = set()
    result: list[LibraryBook] = []
    for book in books:
        if book.title_key and book.title_key in seen:
            continue
        if book.title_key:
            seen.add(book.title_key)
        result.append(book)
    return tuple(result)


def _partition(
    books: tuple[LibraryBook, ...], by_author: dict[str, set[str]], all_titles: set[str]
) -> tuple[list[LibraryBook], list[LibraryBook]]:
    """Split source books according to exact or fuzzy title presence."""
    matched: list[LibraryBook] = []
    missing: list[LibraryBook] = []
    for book in books:
        candidates = by_author.get(book.author_key, set()) | all_titles
        (
            matched
            if any(titles_match(book.title_key, title) for title in candidates)
            else missing
        ).append(book)
    return matched, missing
