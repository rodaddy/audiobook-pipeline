"""Cross-library comparison for audiobook collections.

Compares a source library against a target library to find truly missing
books. Handles multi-part M4B fragments, chapter-per-file books, author
name variations, franchise folder consolidation, and fuzzy title matching.

Key normalizations:
    - Author: periods stripped, &/and equivalence, franchise folder awareness
    - Title: Part N suffixes, ASIN codes, (Unabridged), author prefixes,
      chapter numbers (NN-), series prefixes (HP., ChNN)
    - Multi-part: Part N files AND chapter-per-file (NN- Title) collapsed
      by parent directory into single book entries
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz

from .audit import (
    FRANCHISE_FOLDERS,
    _is_franchise_folder,
    _normalize_author,
    _normalize_for_dedup,
)

log = logger.bind(stage="library-diff")

# Minimum fuzzy match ratio to consider titles equivalent
FUZZY_THRESHOLD = 85

# Top-level folders that are containers, not author names
NON_AUTHOR_FOLDERS = {
    "newbooks",
    "original",
    "incoming",
    "unsorted",
    "_unsorted",
    "to_fix",
    "audiobooks_to_fix",
    "downloads",
    "new",
}

# Pattern: chapter-per-file naming like "01- Title", "Ch01 - Title"
_CHAPTER_FILE_RE = re.compile(r"^(?:ch)?\d{1,3}[a-d]?\s*[-\.]\s*", re.IGNORECASE)

# Pattern: "Part N" suffix (case insensitive)
_PART_SUFFIX_RE = re.compile(r"[,\s]+part\s+\d+\s*$", re.IGNORECASE)

# Pattern: numbered prefix like "1-01 Title" (disc-track) or "HP. 3 -"
_NUMBERED_PREFIX_RE = re.compile(
    r"^(?:\d+-\d+\s+|HP[\.\s]*\d+\s*[-\.]\s*)", re.IGNORECASE
)


@dataclass
class BookEntry:
    """A single book (or multi-part group) in a library."""

    author: str  # folder-level author name
    norm_author: str  # normalized for matching
    title: str  # extracted book title
    norm_title: str  # normalized for matching
    path: str  # relative path from library root
    is_multipart: bool = False  # True if Part N or chapter file
    part_group: str = ""  # group key for collapsing


@dataclass
class LibraryDiff:
    """Result of comparing two libraries."""

    missing: list[BookEntry] = field(default_factory=list)
    matched: list[BookEntry] = field(default_factory=list)
    source_count: int = 0
    target_count: int = 0


def _guess_author_from_path(rel: Path) -> str:
    """Extract best-guess author from a relative path.

    Skips known non-author container folders (NewBooks, Original, etc.)
    and returns the first path component that looks like an author name.
    """
    for part in rel.parts[:-1]:  # exclude filename
        if part.lower() in NON_AUTHOR_FOLDERS:
            continue
        # Skip "Audiobooks (narrator)" style folders
        if part.lower().startswith("audiobooks"):
            continue
        # Skip "Other Audiobooks" type folders
        if "audiobook" in part.lower():
            continue
        return part
    # Fallback: use first folder
    return rel.parts[0] if len(rel.parts) > 1 else ""


def _book_title_from_dir(dir_name: str) -> str:
    """Extract book title from a directory name.

    Strips "Book N - " prefixes commonly used in series folders.
    """
    s = dir_name
    # Strip "Book N - " or "Book N.N - " prefix
    s = re.sub(r"^book\s+[\d.]+\s*-?\s*", "", s, flags=re.IGNORECASE)
    return s.strip()


def _extract_books(library_root: Path) -> list[BookEntry]:
    """Scan a library and extract BookEntry for each M4B file.

    Handles both organized (Author/Book/file.m4b) and messy source
    structures (NewBooks/Collection/Series/Book/chapters.m4b).
    """
    entries: list[BookEntry] = []
    if not library_root.is_dir():
        return entries

    for m4b in sorted(library_root.rglob("*.m4b")):
        rel = m4b.relative_to(library_root)
        parts = rel.parts
        if len(parts) < 2:
            continue

        author = _guess_author_from_path(rel)
        stem = m4b.stem
        norm_author = _normalize_author(author)
        norm_title = _normalize_for_dedup(stem.lower(), author=author)

        # Detect multi-part: "Part N" suffix
        is_part = bool(_PART_SUFFIX_RE.search(stem))
        # Detect chapter-per-file: "01- Title" or "Ch01 - Title"
        is_chapter = bool(_CHAPTER_FILE_RE.match(stem))
        # Detect numbered prefix: "1-01 Introduction" (disc-track)
        is_numbered = bool(_NUMBERED_PREFIX_RE.match(stem))

        is_multipart = is_part or is_chapter or is_numbered

        # Group key for collapsing
        part_group = ""
        if is_part:
            base = _PART_SUFFIX_RE.sub("", stem).strip()
            part_group = f"{m4b.parent}|{base.lower()}"
        elif is_chapter or is_numbered:
            # Use parent directory name as the book title
            part_group = str(m4b.parent)

        entries.append(
            BookEntry(
                author=author,
                norm_author=norm_author,
                title=stem,
                norm_title=norm_title,
                path=str(rel),
                is_multipart=is_multipart,
                part_group=part_group,
            )
        )

    return entries


def _collapse_multipart(entries: list[BookEntry]) -> list[BookEntry]:
    """Collapse multi-part/chapter entries into single book entries.

    Groups by part_group key and emits one representative entry per group.
    For chapter files, uses the parent directory name as the book title.
    """
    groups: dict[str, list[BookEntry]] = defaultdict(list)
    result: list[BookEntry] = []

    for entry in entries:
        if entry.is_multipart and entry.part_group:
            groups[entry.part_group].append(entry)
        else:
            result.append(entry)

    for group_key, group_entries in groups.items():
        representative = group_entries[0]

        # For chapter-per-file groups, derive title from directory name
        # (individual chapter names are meaningless for matching)
        title = representative.title
        norm_title = representative.norm_title
        if _CHAPTER_FILE_RE.match(representative.title) or _NUMBERED_PREFIX_RE.match(
            representative.title
        ):
            dir_name = Path(representative.path).parent.name
            title = _book_title_from_dir(dir_name)
            norm_title = _normalize_for_dedup(
                title.lower(), author=representative.author
            )

        result.append(
            BookEntry(
                author=representative.author,
                norm_author=representative.norm_author,
                title=title,
                norm_title=norm_title,
                path=representative.path,
                is_multipart=True,
                part_group=group_key,
            )
        )

    return result


def _deduplicate_source(entries: list[BookEntry]) -> list[BookEntry]:
    """Remove duplicate source entries (same book in NewBooks/ and Original/).

    Deduplicates by normalized title, keeping the first occurrence.
    """
    seen: set[str] = set()
    result: list[BookEntry] = []

    for entry in entries:
        key = entry.norm_title
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(entry)

    return result


def _build_target_index(
    entries: list[BookEntry],
) -> dict[str, set[str]]:
    """Build a lookup: normalized_author -> set of normalized_titles."""
    index: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        index[entry.norm_author].add(entry.norm_title)

    return dict(index)


def _find_match(
    book: BookEntry,
    target_index: dict[str, set[str]],
    all_target_titles: set[str],
) -> bool:
    """Check if a source book has a match in the target library.

    Matching strategy (in order):
    1. Exact normalized title under same normalized author
    2. Exact normalized title under any author (cross-author / franchise)
    3. Fuzzy title match (>=85%) under same author
    4. Fuzzy title match (>=85%) under any author
    """
    norm_title = book.norm_title
    norm_author = book.norm_author

    if not norm_title:
        return False

    # 1. Exact match, same author
    if norm_author in target_index and norm_title in target_index[norm_author]:
        return True

    # 2. Exact match, any author (catches franchise reorganization)
    if norm_title in all_target_titles:
        return True

    # 3. Fuzzy match, same author first
    # Use token_set_ratio to handle titles with extra series/subtitle info
    if norm_author in target_index:
        for target_title in target_index[norm_author]:
            if fuzz.token_set_ratio(norm_title, target_title) >= FUZZY_THRESHOLD:
                return True

    # 4. Fuzzy match, any author
    for target_title in all_target_titles:
        if fuzz.token_set_ratio(norm_title, target_title) >= FUZZY_THRESHOLD:
            return True

    return False


def compare_libraries(source: Path, target: Path) -> LibraryDiff:
    """Compare source library against target to find missing books.

    Args:
        source: Path to the source library (books to check).
        target: Path to the target library (ground truth).

    Returns:
        LibraryDiff with missing and matched books.
    """
    log.info(f"Scanning target library: {target}")
    target_entries = _extract_books(target)
    target_entries = _collapse_multipart(target_entries)
    log.info(f"Target: {len(target_entries)} books")

    log.info(f"Scanning source library: {source}")
    source_entries = _extract_books(source)
    source_entries = _collapse_multipart(source_entries)
    pre_dedup = len(source_entries)
    source_entries = _deduplicate_source(source_entries)
    log.info(
        f"Source: {len(source_entries)} unique books "
        f"({pre_dedup} before dedup, after multi-part collapse)"
    )

    # Build target lookup structures
    target_index = _build_target_index(target_entries)
    all_target_titles: set[str] = set()
    for titles in target_index.values():
        all_target_titles.update(titles)

    diff = LibraryDiff(
        source_count=len(source_entries),
        target_count=len(target_entries),
    )

    for book in source_entries:
        if _find_match(book, target_index, all_target_titles):
            diff.matched.append(book)
        else:
            diff.missing.append(book)
            log.debug(
                f"No match: {book.author}/{book.title} "
                f"(norm: '{book.norm_author}' / '{book.norm_title}')"
            )

    log.info(
        f"Diff complete: {len(diff.matched)} matched, " f"{len(diff.missing)} missing"
    )

    return diff
