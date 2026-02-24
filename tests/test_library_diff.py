"""Tests for ops/library_diff.py -- cross-library comparison."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.ops.library_diff import (
    BookEntry,
    LibraryDiff,
    _collapse_multipart,
    _extract_books,
    compare_libraries,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_library(tmp_path: Path, name: str, structure: dict[str, bytes]) -> Path:
    """Create a mock library directory structure.

    Keys are relative paths, values are file contents.
    """
    lib = tmp_path / name
    for rel_path, content in structure.items():
        full = lib / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(content)
    return lib


# ---------------------------------------------------------------------------
# Multi-part grouping
# ---------------------------------------------------------------------------


class TestMultipartCollapse:
    def test_three_parts_collapse_to_one(self, tmp_path):
        lib = _make_library(
            tmp_path,
            "source",
            {
                "Anne Rice/Vampire Chronicles/Vampire Chronicles, Part 1.m4b": b"\x00",
                "Anne Rice/Vampire Chronicles/Vampire Chronicles, Part 2.m4b": b"\x00",
                "Anne Rice/Vampire Chronicles/Vampire Chronicles, Part 3.m4b": b"\x00",
            },
        )
        entries = _extract_books(lib)
        assert len(entries) == 3
        collapsed = _collapse_multipart(entries)
        assert len(collapsed) == 1
        assert collapsed[0].is_multipart is True

    def test_non_part_files_unchanged(self, tmp_path):
        lib = _make_library(
            tmp_path,
            "source",
            {
                "Author/Book One/Book One.m4b": b"\x00",
                "Author/Book Two/Book Two.m4b": b"\x00",
            },
        )
        entries = _extract_books(lib)
        collapsed = _collapse_multipart(entries)
        assert len(collapsed) == 2
        assert all(not e.is_multipart for e in collapsed)

    def test_mixed_parts_and_standalone(self, tmp_path):
        lib = _make_library(
            tmp_path,
            "source",
            {
                "Author/Series/Book, Part 1.m4b": b"\x00",
                "Author/Series/Book, Part 2.m4b": b"\x00",
                "Author/Standalone/Standalone.m4b": b"\x00",
            },
        )
        entries = _extract_books(lib)
        collapsed = _collapse_multipart(entries)
        assert len(collapsed) == 2  # 1 group + 1 standalone


# ---------------------------------------------------------------------------
# Author normalization matching
# ---------------------------------------------------------------------------


class TestAuthorMatching:
    def test_initials_match(self, tmp_path):
        """R.A. Salvatore in source should match R. A. Salvatore in target."""
        source = _make_library(
            tmp_path,
            "source",
            {"R.A. Salvatore/Homeland/Homeland.m4b": b"\x00"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"R. A. Salvatore/Homeland/Homeland.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0
        assert len(diff.matched) == 1

    def test_ampersand_and_match(self, tmp_path):
        """Weis & Hickman should match Weis and Hickman."""
        source = _make_library(
            tmp_path,
            "source",
            {
                "Margaret Weis & Tracy Hickman/Dragons of Autumn/Dragons of Autumn Twilight.m4b": b"\x00"
            },
        )
        target = _make_library(
            tmp_path,
            "target",
            {
                "Margaret Weis and Tracy Hickman/Dragons of Autumn/Dragons of Autumn Twilight.m4b": b"\x00"
            },
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0


# ---------------------------------------------------------------------------
# Franchise folder matching
# ---------------------------------------------------------------------------


class TestFranchiseMatching:
    def test_source_author_target_franchise(self, tmp_path):
        """Source under 'Margaret Weis' should match target under 'Dragonlance'."""
        source = _make_library(
            tmp_path,
            "source",
            {
                "Margaret Weis/Dragons of Autumn Twilight/Dragons of Autumn Twilight.m4b": b"\x00"
            },
        )
        target = _make_library(
            tmp_path,
            "target",
            {
                "Dragonlance/Dragons of Autumn Twilight/Dragons of Autumn Twilight.m4b": b"\x00"
            },
        )
        diff = compare_libraries(source, target)
        # Should match via cross-author title lookup
        assert len(diff.missing) == 0


# ---------------------------------------------------------------------------
# Fuzzy title matching
# ---------------------------------------------------------------------------


class TestFuzzyMatching:
    def test_slight_title_variation(self, tmp_path):
        """'The Way of Kings' should fuzzy-match 'Way of Kings'."""
        source = _make_library(
            tmp_path,
            "source",
            {"Sanderson/Way of Kings/The Way of Kings.m4b": b"\x00"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Sanderson/Way of Kings/Way of Kings.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0

    def test_asin_in_source_stripped(self, tmp_path):
        """Source with ASIN code should still match clean target."""
        source = _make_library(
            tmp_path,
            "source",
            {"Author/Book/The Great Book [B00AAI79WY].m4b": b"\x00"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Author/Book/The Great Book.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0

    def test_unabridged_stripped(self, tmp_path):
        """Source with (Unabridged) should match clean target."""
        source = _make_library(
            tmp_path,
            "source",
            {"Author/Book/The Great Book (Unabridged).m4b": b"\x00"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Author/Book/The Great Book.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0


# ---------------------------------------------------------------------------
# Full coverage scenario
# ---------------------------------------------------------------------------


class TestFullCoverage:
    def test_fully_covered_library_returns_zero_missing(self, tmp_path):
        """When every source book exists in target, missing should be empty."""
        books = {
            "Author A/Book One/Book One.m4b": b"\x00",
            "Author A/Book Two/Book Two.m4b": b"\x00",
            "Author B/Book Three/Book Three.m4b": b"\x00",
        }
        source = _make_library(tmp_path, "source", books)
        target = _make_library(tmp_path, "target", books)
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0
        assert len(diff.matched) == 3

    def test_genuinely_missing_book(self, tmp_path):
        """A book only in source should appear in missing."""
        source = _make_library(
            tmp_path,
            "source",
            {
                "Author/Book One/Book One.m4b": b"\x00",
                "Author/Missing Book/Missing Book.m4b": b"\x00",
            },
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Author/Book One/Book One.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 1
        assert diff.missing[0].norm_title == "missing book"
        assert len(diff.matched) == 1

    def test_multipart_source_matches_single_target(self, tmp_path):
        """Source with Part 1-3 should match single consolidated target."""
        source = _make_library(
            tmp_path,
            "source",
            {
                "Author/Book/Book Title, Part 1.m4b": b"\x00",
                "Author/Book/Book Title, Part 2.m4b": b"\x00",
                "Author/Book/Book Title, Part 3.m4b": b"\x00",
            },
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Author/Book/Book Title.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0
        assert len(diff.matched) == 1

    def test_empty_source(self, tmp_path):
        source = _make_library(tmp_path, "source", {})
        target = _make_library(
            tmp_path,
            "target",
            {"Author/Book/Book.m4b": b"\x00"},
        )
        # Need to create the source dir since no files
        source.mkdir(parents=True, exist_ok=True)
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0
        assert len(diff.matched) == 0
        assert diff.source_count == 0
