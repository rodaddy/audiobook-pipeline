"""Tests for reading a source path into metadata.

One test per behaviour, asserting every case it covers. Every path here is a
real one from the source tree, and several are the exact books that were
misfiled on the 2026-08-02 live run.
"""

from __future__ import annotations

from pathlib import Path

from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.services.parse import _book_name, parse_path

ROOT = Path("/src/Done")


def parse(relative: str, root: Path = ROOT) -> ParsedPath:
    """Parse a path expressed relative to the run root."""
    return parse_path(ROOT / relative, root)


def test_the_author_is_read_from_whatever_level_holds_it() -> None:
    """No fixed depth works: across 35 real books the author sits at several.

    The first two are the books that landed under "Unknown Author" on the live
    run, with the right name sitting one and two directories above them.
    """
    assert parse("Brian McClellan/Powder Mage 0.2 - Servant").author == (
        "Brian McClellan"
    )
    assert parse("Brian McClellan/Powder Mage 0.5 - Girl/Girl.mp3").author == (
        "Brian McClellan"
    )
    assert parse("C S Friedman/The Coldfire Trilogy/01 - Black Sun").author == (
        "C S Friedman"
    )
    assert parse("R.A. Salvatore - The Legend of Drizzt/01 - Homeland.m4b").author == (
        "R.A. Salvatore"
    )


def test_a_path_that_names_no_person_yields_no_author() -> None:
    """Refusing beats guessing: a wrong author folder cannot be swept later."""
    assert parse("$100M Offers.mp3").author == ""
    assert parse("Noobtown Books 1-7/Book 3 Castle.m4b").author == ""
    assert parse("Dragonlance/Dragonlance/Some Book.m4b").author == ""
    assert parse_path(Path("/elsewhere/Someone/A Book"), ROOT).author == ""


def test_the_root_bounds_the_walk() -> None:
    """The root itself may name the author, and nothing above it ever does.

    Converting one author is ordinary -- `audiobook-convert .../Brian
    McClellan` -- and stopping BELOW the root filed all three of that author's
    books under "Unknown Author" in the sandbox.
    """
    author_root = ROOT / "Brian McClellan"

    assert parse("Brian McClellan/Powder Mage 0.2", author_root).author == (
        "Brian McClellan"
    )
    # A root that is not a person supplies nothing, and neither does the
    # parent of a run pointed straight at one book.
    assert parse("Some Book").author == ""
    assert parse_path(author_root / "Some Book", author_root / "Some Book").author == ""


def test_a_decimal_position_survives() -> None:
    """Path.stem read ".2" as an extension, collapsing three novellas."""
    assert parse("A Author/Powder Mage 0.1 - Forsworn").position == "0.1"
    assert parse("A Author/Powder Mage 0.2 - Servant").position == "0.2"
    assert parse("A Author/Powder Mage 01 - Promise of Blood").position == "01"
    assert parse("A Author/Powder Mage 0.2 - Servant").title == "Servant"


def test_a_numbered_middle_folder_yields_the_series_not_its_whole_name() -> None:
    """It wrote the entire folder name into the library as a series."""
    author_root = ROOT / "Brian McClellan"
    book = author_root / "Powder Mage 0.5 - The Girl of Hrusch Avenue" / "book.mp3"

    assert parse_path(book, author_root).series == "Powder Mage"


def test_book_name() -> None:
    """A suffix is dropped only when it is a known AUDIO extension."""
    assert _book_name(Path("/src/Powder Mage 0.2 - Servant")) == (
        "Powder Mage 0.2 - Servant"
    )
    assert _book_name(Path("/src/The Girl of Hrusch Avenue.mp3")) == (
        "The Girl of Hrusch Avenue"
    )
    # "file.m4b" says nothing; the folder holding it does.
    assert _book_name(Path("/src/The Martian/file.m4b")) == "The Martian"


def test_a_year_and_subtitle_are_dropped_from_a_fallback_title() -> None:
    assert parse("An Author/Food A Love Story (2014).m4b").title == "Food A Love Story"


def test_trailing_part_marker_is_removed_before_book_pattern_matching() -> None:
    claim = parse("An Author/Servant of the Crown Part 1 of 3.mp3")

    assert claim.title == "Servant of the Crown"


def test_bare_part_suffix_remains_part_of_the_book_title() -> None:
    claim = parse("Some Author/Kill Bill Part 2/Kill Bill Part 2.m4b")

    assert claim.title == "Kill Bill Part 2"
