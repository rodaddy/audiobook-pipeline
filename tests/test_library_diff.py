"""Tests for ops/library_diff.py -- cross-library comparison."""

from __future__ import annotations

from pathlib import Path

from audiobook_pipeline.services.library import compare_libraries, scan_library
from audiobook_pipeline.services.matching import normalize_title

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
    def test_three_parts_collapse_to_one(self, tmp_path: Path) -> None:
        lib = _make_library(
            tmp_path,
            "source",
            {
                "Anne Rice/Vampire Chronicles/Vampire Chronicles, Part 1.m4b": b"\x00",
                "Anne Rice/Vampire Chronicles/Vampire Chronicles, Part 2.m4b": b"\x00",
                "Anne Rice/Vampire Chronicles/Vampire Chronicles, Part 3.m4b": b"\x00",
            },
        )
        collapsed = scan_library(lib, extensions=frozenset({".m4b"}))
        assert len(collapsed) == 1
        assert collapsed[0].multipart is True

    def test_non_part_files_unchanged(self, tmp_path: Path) -> None:
        lib = _make_library(
            tmp_path,
            "source",
            {
                "Author/Book One/Book One.m4b": b"\x00",
                "Author/Book Two/Book Two.m4b": b"\x00",
            },
        )
        collapsed = scan_library(lib, extensions=frozenset({".m4b"}))
        assert len(collapsed) == 2
        assert all(not e.multipart for e in collapsed)

    def test_mixed_parts_and_standalone(self, tmp_path: Path) -> None:
        lib = _make_library(
            tmp_path,
            "source",
            {
                "Author/Series/Book, Part 1.m4b": b"\x00",
                "Author/Series/Book, Part 2.m4b": b"\x00",
                "Author/Standalone/Standalone.m4b": b"\x00",
            },
        )
        collapsed = scan_library(lib, extensions=frozenset({".m4b"}))
        assert len(collapsed) == 2  # 1 group + 1 standalone

    def test_hp_and_numbered_chapter_files_collapse_to_one_book(
        self, tmp_path: Path
    ) -> None:
        library = _make_library(
            tmp_path,
            "source",
            {
                "Author/Book/HP. 1 - Chapter.mp3": b"x",
                "Author/Book/HP. 2 - Chapter.mp3": b"x",
                "Author/Other/1-01 Chapter.mp3": b"x",
                "Author/Other/1-02 Chapter.mp3": b"x",
            },
        )
        books = scan_library(library, extensions=frozenset({".mp3"}))
        assert len(books) == 2
        assert all(book.multipart for book in books)


# ---------------------------------------------------------------------------
# Author normalization matching
# ---------------------------------------------------------------------------


class TestAuthorMatching:
    def test_initials_match(self, tmp_path: Path) -> None:
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

    def test_author_prefix_is_removed_before_title_matching(
        self, tmp_path: Path
    ) -> None:
        source = _make_library(
            tmp_path,
            "source",
            {"B. T. Narro/Book/B. T. Narro - Rhythm of Rivalry.mp3": b"x"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"B. T. Narro/Book/Rhythm of Rivalry.m4b": b"x"},
        )
        assert len(compare_libraries(source, target).missing) == 0

    def test_ampersand_and_match(self, tmp_path: Path) -> None:
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


def test_distinct_dash_titles_do_not_share_a_normalized_key() -> None:
    assert normalize_title("A-B") != normalize_title("A-C")
    assert normalize_title("A - B") != normalize_title("A - C")


# ---------------------------------------------------------------------------
# Franchise folder matching
# ---------------------------------------------------------------------------


class TestFranchiseMatching:
    def test_source_author_target_franchise(self, tmp_path: Path) -> None:
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
    def test_slight_title_variation(self, tmp_path: Path) -> None:
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

    def test_asin_in_source_stripped(self, tmp_path: Path) -> None:
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

    def test_unabridged_stripped(self, tmp_path: Path) -> None:
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
    def test_fully_covered_library_returns_zero_missing(self, tmp_path: Path) -> None:
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

    def test_genuinely_missing_book(self, tmp_path: Path) -> None:
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
        assert diff.missing[0].title_key == "missing book"
        assert len(diff.matched) == 1

    def test_multipart_source_matches_single_target(self, tmp_path: Path) -> None:
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

    def test_empty_source(self, tmp_path: Path) -> None:
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


# ---------------------------------------------------------------------------
# Unconverted source formats
#
# The whole point of a source library is that it holds material that has NOT
# been through the pipeline yet -- loose mp3/m4a/flac, not m4b. Scanning only
# for *.m4b made every such book INVISIBLE to the diff: not missing, not
# matched, simply absent from source_count.
#
# Measured 2026-08-01 against the real trees. A diff of
# CleanDesktop/tFiles/Done reported "50 source books, 43 matched, 7 missing"
# while 71 mp3 (Brian McClellan, Powder Mage) and 4 m4a (C S Friedman, Coldfire)
# sat in that source and appeared in NEITHER column -- and neither author had a
# folder in the target library at all. The headline was read as "43 already
# converted", so those books would have been skipped as done.
# ---------------------------------------------------------------------------


class TestUnconvertedSourceFormats:
    """Source books that are still mp3/m4a/flac must be seen by the diff."""

    def test_mp3_source_book_is_reported_missing(self, tmp_path: Path) -> None:
        """An mp3-only book absent from the target is MISSING, not invisible."""
        source = _make_library(
            tmp_path,
            "source",
            {"Brian McClellan/Promise of Blood/Promise of Blood.mp3": b"\x00"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Andy Weir/Project Hail Mary/Project Hail Mary.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert diff.source_count == 1
        assert len(diff.missing) == 1
        assert diff.missing[0].author == "Brian McClellan"

    def test_m4a_source_book_is_reported_missing(self, tmp_path: Path) -> None:
        """.m4a is neither m4b nor in SOURCE_EXTENSIONS -- it was missed twice."""
        source = _make_library(
            tmp_path,
            "source",
            {"C S Friedman/Black Sun Rising/Black Sun Rising.m4a": b"\x00"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Andy Weir/Project Hail Mary/Project Hail Mary.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert diff.source_count == 1
        assert len(diff.missing) == 1

    def test_mp3_source_matches_converted_m4b_target(self, tmp_path: Path) -> None:
        """The converted copy in the target counts as a match across formats.

        This is the other half: having made mp3 visible, an mp3 whose m4b
        already exists must NOT be re-reported as missing.
        """
        source = _make_library(
            tmp_path,
            "source",
            {"Andy Weir/The Martian/The Martian.mp3": b"\x00"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Andy Weir/The Martian/The Martian.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0
        assert len(diff.matched) == 1

    def test_chapter_per_file_mp3_collapses_to_one_book(self, tmp_path: Path) -> None:
        """71 loose mp3 chapters are one book, not 71 missing books."""
        source = _make_library(
            tmp_path,
            "source",
            {
                f"Brian McClellan/Promise of Blood/{n:02d}- Chapter.mp3": b"\x00"
                for n in range(1, 25)
            },
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Andy Weir/Project Hail Mary/Project Hail Mary.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert diff.source_count == 1
        assert len(diff.missing) == 1

    def test_target_scan_still_counts_only_m4b(self, tmp_path: Path) -> None:
        """A stray mp3 in the TARGET is not a converted book.

        The target library is the ground truth of what the pipeline has already
        produced, and it produces m4b. Counting a leftover source file there as
        a finished book would mask the very gap this tool exists to find.
        """
        source = _make_library(
            tmp_path,
            "source",
            {"Author/Book One/Book One.mp3": b"\x00"},
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Author/Book One/Book One.mp3": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert diff.target_count == 0
        assert len(diff.missing) == 1


# ---------------------------------------------------------------------------
# Multi-part naming styles found in real source trees
#
# Each of these appeared in CleanDesktop/tFiles/Done and matched NONE of the
# three original patterns, so every part counted as its own missing book: one
# Powder Mage novel reported as 19 missing books, the trilogy as 75 rather
# than 6. Probed directly against the regexes on 2026-08-01.
# ---------------------------------------------------------------------------


class TestRealWorldPartNaming:
    """Part-marker spellings that must collapse to one book."""

    def test_part_n_of_m_collapses(self, tmp_path: Path) -> None:
        """'Part 1 of 3' -- the dominant spelling, previously unmatched."""
        source = _make_library(
            tmp_path,
            "source",
            {
                f"Brian McClellan/Servant of the Crown/"
                f"Servant of the Crown Part {n} of 3.mp3": b"\x00"
                for n in (1, 2, 3)
            },
        )
        target = _make_library(tmp_path, "target", {})
        target.mkdir(parents=True, exist_ok=True)
        diff = compare_libraries(source, target)
        assert diff.source_count == 1
        assert diff.missing[0].title == "Servant of the Crown"

    def test_glued_numeric_part_suffix_collapses(self, tmp_path: Path) -> None:
        """'Promise of Blood01-19' -- part marker with no separator."""
        source = _make_library(
            tmp_path,
            "source",
            {
                f"Brian McClellan/Promise of Blood/Promise of Blood{n:02d}-19.mp3": (
                    b"\x00"
                )
                for n in range(1, 20)
            },
        )
        target = _make_library(tmp_path, "target", {})
        target.mkdir(parents=True, exist_ok=True)
        diff = compare_libraries(source, target)
        assert diff.source_count == 1
        assert diff.missing[0].title == "Promise of Blood"

    def test_glued_suffix_book_matches_target(self, tmp_path: Path) -> None:
        """The collapsed title must match the converted book in the target.

        Collapsing to the wrong title is as bad as not collapsing: the group
        used to keep the literal stem 'Promise of Blood01-19', which no real
        title equals, so it read as missing even once converted.
        """
        source = _make_library(
            tmp_path,
            "source",
            {
                f"Brian McClellan/Promise of Blood/Promise of Blood{n:02d}-19.mp3": (
                    b"\x00"
                )
                for n in range(1, 20)
            },
        )
        target = _make_library(
            tmp_path,
            "target",
            {"Brian McClellan/Promise of Blood/Promise of Blood.m4b": b"\x00"},
        )
        diff = compare_libraries(source, target)
        assert len(diff.missing) == 0
        assert len(diff.matched) == 1

    def test_parenthetical_before_part_marker_collapses(self, tmp_path: Path) -> None:
        """'The Autumn Republic (Unabridged) Part 01 of 19'."""
        source = _make_library(
            tmp_path,
            "source",
            {
                f"Brian McClellan/The Autumn Republic/"
                f"The Autumn Republic (Unabridged) Part {n:02d} of 19.mp3": b"\x00"
                for n in range(1, 20)
            },
        )
        target = _make_library(tmp_path, "target", {})
        target.mkdir(parents=True, exist_ok=True)
        diff = compare_libraries(source, target)
        assert diff.source_count == 1

    def test_hyphenated_title_is_not_a_part_marker(self, tmp_path: Path) -> None:
        """A real hyphenated title must NOT be eaten as a part suffix.

        The glued-suffix pattern is deliberately anchored to digits-hyphen-
        digits at end of string. 'Catch-22' has a hyphen and a number and is
        one whole book; treating it as part 22 of nothing would merge
        unrelated books under an empty title.
        """
        source = _make_library(
            tmp_path,
            "source",
            {
                "Joseph Heller/Catch-22/Catch-22.mp3": b"\x00",
                "Joseph Heller/Something Happened/Something Happened.mp3": b"\x00",
            },
        )
        target = _make_library(tmp_path, "target", {})
        target.mkdir(parents=True, exist_ok=True)
        diff = compare_libraries(source, target)
        assert diff.source_count == 2
        assert {b.title for b in diff.missing} == {"Catch-22", "Something Happened"}

    def test_distinct_books_in_series_folder_stay_distinct(
        self, tmp_path: Path
    ) -> None:
        """Coldfire: 4 one-file books under one series dir are 4 books.

        The chapter pattern groups by DIRECTORY, so a book-per-subdirectory
        layout must not be collapsed into one entry by the series folder.
        """
        source = _make_library(
            tmp_path,
            "source",
            {
                "C S Friedman/The Coldfire Trilogy/0.5 - Dominion/"
                "0.5 - Dominion.m4a": b"\x00",
                "C S Friedman/The Coldfire Trilogy/01 - Black Sun Rising/"
                "01 - Black Sun Rising.m4a": b"\x00",
                "C S Friedman/The Coldfire Trilogy/02 - When True Night Falls/"
                "02 - When True Night Falls.m4a": b"\x00",
                "C S Friedman/The Coldfire Trilogy/03 - Crown of Shadows/"
                "03 - Crown of Shadows.m4a": b"\x00",
            },
        )
        target = _make_library(tmp_path, "target", {})
        target.mkdir(parents=True, exist_ok=True)
        diff = compare_libraries(source, target)
        assert diff.source_count == 4

    def test_two_level_numbering_collapses_to_one_book(self, tmp_path: Path) -> None:
        """'The Crimson Campaign 01 Part 3 of 7' -- disc AND part number.

        21 files, one book. Stripping only the 'Part N of 7' half left three
        groups (01/02/03), reporting one novel as three missing books.
        """
        source = _make_library(
            tmp_path,
            "source",
            {
                f"Brian McClellan/Powder Mage 02 - The Crimson Campaign/"
                f"The Crimson Campaign {disc:02d} Part {part} of 7.mp3": b"\x00"
                for disc in (1, 2, 3)
                for part in range(1, 8)
            },
        )
        target = _make_library(tmp_path, "target", {})
        target.mkdir(parents=True, exist_ok=True)
        diff = compare_libraries(source, target)
        assert diff.source_count == 1
        assert diff.missing[0].title == "The Crimson Campaign"

    def test_title_ending_in_a_year_keeps_its_number(self, tmp_path: Path) -> None:
        """The disc-number strip must not eat a number that is the title.

        Bounded to 1-2 digits for exactly this reason: '1984' and
        'Fahrenheit 451' end in numbers that are part of the name.
        """
        source = _make_library(
            tmp_path,
            "source",
            {
                "Ray Bradbury/Fahrenheit 451/Fahrenheit 451, Part 1.mp3": b"\x00",
                "Ray Bradbury/Fahrenheit 451/Fahrenheit 451, Part 2.mp3": b"\x00",
            },
        )
        target = _make_library(tmp_path, "target", {})
        target.mkdir(parents=True, exist_ok=True)
        diff = compare_libraries(source, target)
        assert diff.source_count == 1
        assert diff.missing[0].title == "Fahrenheit 451"
