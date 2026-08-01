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

# What counts as a book on each side, and the two sets are NOT the same.
#
# TARGET is the finished library -- the pipeline emits M4B, so an M4B is the
# evidence that a book has already been produced. A stray MP3 sitting in the
# target is un-migrated source material, and counting it as a finished book
# would report the exact gap this tool exists to find as already closed.
#
# SOURCE is unconverted material, which is the whole reason a source library
# exists. Scanning it for M4B only made every still-loose book invisible:
# absent from source_count, so neither missing nor matched. Measured
# 2026-08-01 -- a diff reporting "50 source books, 43 matched" had silently
# skipped 71 MP3 and 4 M4A belonging to two authors who were not in the target
# at all.
#
# .m4a is listed explicitly: it is neither M4B nor in audit.SOURCE_EXTENSIONS,
# so it fell through both sets. It is the container Coldfire Trilogy shipped in.
TARGET_EXTENSIONS = frozenset({".m4b"})
SOURCE_EXTENSIONS = frozenset(
    {".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".wma", ".wav", ".aac"}
)

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

# Pattern: "Part N" suffix, with an optional "of M" total.
#
# The `of M` half is not decoration -- it is the dominant spelling in the wild,
# and without it every part of a book stayed a separate entry. Measured
# 2026-08-01: "Servant of the Crown Part 1 of 3" and "The Autumn Republic
# (Unabridged) Part 01 of 19" both fell through, reporting one book as 3 and 19
# missing books respectively.
_PART_SUFFIX_RE = re.compile(
    r"[,\s]+part\s+\d+(?:\s+of\s+\d+)?\s*$",
    re.IGNORECASE,
)

# Pattern: bare "NN-MM" part suffix glued to the title, as in
# "Promise of Blood01-19" -- part 1 of 19, with no separator at all.
#
# Anchored to require BOTH numbers so an ordinary hyphenated title
# ("Catch-22", "Book 2 - Exile") is not mistaken for a part marker: the
# trailing group must be digits-hyphen-digits at end of string.
_PART_NUMBERED_SUFFIX_RE = re.compile(r"\s*\d{1,3}-\d{1,3}\s*$")

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


def _iter_audio_files(library_root: Path, extensions: frozenset[str]) -> list[Path]:
    """List audio files under a library, one pass, filtered by extension.

    One rglob("*") rather than an rglob per extension: the multi-glob form
    walks the whole tree once per pattern, and this runs over libraries with
    hundreds of books on a network mount.
    """
    return sorted(
        p
        for p in library_root.rglob("*")
        if p.is_file() and p.suffix.lower() in extensions
    )


# How a filename says "I am one piece of a larger book", in priority order.
#
# A TABLE rather than an if/elif chain (_DOCS/STANDARDS-python.md): adding the
# next naming convention -- and there is always a next one -- is one row here,
# not another branch in a function that already had three.
#
# Each row is (regex, strips_to_base), and that second flag is the whole
# distinction between the two kinds of multi-part file:
#   True  -> the marker can be REMOVED to recover the book title, so parts
#            group by title ("Promise of Blood01-19" -> "promise of blood").
#            Sibling books in one directory therefore stay separate.
#   False -> the filename is only a chapter number ("01 - Intro"); no title
#            survives stripping, so the parent DIRECTORY names the book and
#            everything in it collapses together.
_MULTIPART_PATTERNS: tuple[tuple[re.Pattern[str], bool], ...] = (
    (_PART_SUFFIX_RE, True),
    (_PART_NUMBERED_SUFFIX_RE, True),
    (_CHAPTER_FILE_RE, False),
    (_NUMBERED_PREFIX_RE, False),
)


# A residual disc/volume number left behind after the primary part marker is
# stripped: "The Crimson Campaign 01 Part 3 of 7" -> "The Crimson Campaign 01".
#
# Real 2026-08-01 case: 21 files, ONE book, numbered on two levels. Stripping
# only "Part 3 of 7" left three groups (01/02/03) that each reported as a
# separate missing book. Bounded to 1-2 digits and required at end of string so
# a title that genuinely ends in a number ("Fahrenheit 451", "2312") keeps it --
# those are 3-4 digits and do not match.
_RESIDUAL_DISC_RE = re.compile(r"\s+\d{1,2}\s*$")


def _strip_part_marker(pattern: re.Pattern[str], stem: str) -> str:
    """Recover the book title by removing a part marker and any disc number.

    Shared by classification and title selection so the two cannot disagree
    about what a group is called -- they did before, which is how a group keyed
    on 'promise of blood' ended up titled 'Promise of Blood01-19'.
    """
    base = pattern.sub("", stem).strip()
    return _RESIDUAL_DISC_RE.sub("", base).strip()


def _classify_multipart(stem: str, parent: Path) -> tuple[bool, bool, str]:
    """Decide whether a filename is one piece of a multi-part book.

    Returns (is_part, is_chapter, part_group). An empty part_group means the
    file stands alone and must not be collapsed with anything.
    """
    for pattern, strips_to_base in _MULTIPART_PATTERNS:
        if not pattern.search(stem):
            continue
        if not strips_to_base:
            return False, True, str(parent)
        base = _strip_part_marker(pattern, stem)
        # A filename that is ONLY a part marker leaves nothing to group on;
        # fall back to the directory rather than keying every book on "".
        if not base:
            return False, True, str(parent)
        return True, False, f"{parent}|{base.lower()}"
    return False, False, ""


def _extract_books(
    library_root: Path,
    extensions: frozenset[str] = TARGET_EXTENSIONS,
) -> list[BookEntry]:
    """Scan a library and extract a BookEntry for each audio file.

    Handles both organized (Author/Book/file.m4b) and messy source
    structures (NewBooks/Collection/Series/Book/chapters.mp3).

    `extensions` decides what counts as a book here, and the source and target
    are deliberately asymmetric -- see the constants above.
    """
    entries: list[BookEntry] = []
    if not library_root.is_dir():
        return entries

    for m4b in _iter_audio_files(library_root, extensions):
        rel = m4b.relative_to(library_root)
        parts = rel.parts
        if len(parts) < 2:
            continue

        author = _guess_author_from_path(rel)
        stem = m4b.stem
        norm_author = _normalize_author(author)
        norm_title = _normalize_for_dedup(stem.lower(), author=author)

        is_part, is_chapter, part_group = _classify_multipart(stem, m4b.parent)
        is_multipart = is_part or is_chapter

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


def _group_title(representative: BookEntry) -> str:
    """Pick the book title for a collapsed multi-part group.

    Reuses the SAME table that classified the file, rather than re-testing the
    title against a subset of the patterns. The old code re-derived this from
    _CHAPTER_FILE_RE/_NUMBERED_PREFIX_RE only, so a part-suffix group kept its
    raw filename -- "Promise of Blood01-19" was matched against the target as
    that literal string, which no real title will ever equal.
    """
    stem = representative.title
    for pattern, strips_to_base in _MULTIPART_PATTERNS:
        if not pattern.search(stem):
            continue
        if strips_to_base:
            base = _strip_part_marker(pattern, stem)
            if base:
                return base
        # Chapter names carry no title; the containing directory does.
        return _book_title_from_dir(Path(representative.path).parent.name)
    return stem


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

        title = _group_title(representative)
        norm_title = _normalize_for_dedup(title.lower(), author=representative.author)

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
    source_entries = _extract_books(source, SOURCE_EXTENSIONS)
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

    log.info(f"Diff complete: {len(diff.matched)} matched, {len(diff.missing)} missing")

    return diff
