"""Tests for reading a source path into metadata.

Every path here is a real one from the source tree, and several are the exact
books that were misfiled on the 2026-08-02 live run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.services.parse import _book_name, parse_path

ROOT = Path("/src/Done")


def parse(relative: str) -> ParsedPath:
    """Parse a path expressed relative to the run root."""
    return parse_path(ROOT / relative, ROOT)


# ---------------------------------------------------------------------------
# the author the catalogue could not supply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "Brian McClellan/Powder Mage 0.2 - Servant of the Crown",
        "Brian McClellan/Powder Mage 0.5 - Hrusch Avenue/Hrusch Avenue.mp3",
        "Brian McClellan/Powder Mage 01 - Promise of Blood",
    ],
)
def test_the_author_is_found_at_whatever_depth_it_sits(relative: str) -> None:
    """These two landed under "Unknown Author" on the live run."""
    assert parse(relative).author == "Brian McClellan"


def test_the_author_is_found_above_a_series_folder() -> None:
    """Author/Series/Book -- two levels up, which a fixed depth misses."""
    result = parse("C S Friedman/The Coldfire Trilogy/01 - Black Sun Rising")

    assert result.author == "C S Friedman"
    assert result.series == "The Coldfire Trilogy"


def test_an_author_and_series_in_one_folder_are_split() -> None:
    result = parse("R.A. Salvatore - The Legend of Drizzt/Book 01 - Homeland.m4b")

    assert result.author == "R.A. Salvatore"


def test_a_book_at_the_root_names_no_author() -> None:
    """Nothing sits above it, so there is nothing to read -- not a guess."""
    assert parse("$100M Offers.mp3").author == ""


def test_a_franchise_folder_is_not_adopted_as_an_author() -> None:
    """ "Noobtown Books 1-7" groups books; it is not a person."""
    assert parse("Noobtown Books 1-7/Book 3 Castle of the Noobs.m4b").author == ""


def test_the_walk_stops_at_the_run_root() -> None:
    """Otherwise a run pointed at one book adopts its Downloads folder."""
    book = ROOT / "Brian McClellan" / "Some Book"
    assert parse_path(book, book).author == ""


def test_a_path_outside_the_root_yields_no_author() -> None:
    assert parse_path(Path("/elsewhere/Someone/A Book"), ROOT).author == ""


# ---------------------------------------------------------------------------
# positions, and the decimal that kept getting truncated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "position", "title"),
    [
        ("A Author/Powder Mage 0.1 - Forsworn", "0.1", "Forsworn"),
        (
            "A Author/Powder Mage 0.2 - Servant of the Crown",
            "0.2",
            "Servant of the Crown",
        ),
        ("A Author/Powder Mage 01 - Promise of Blood", "01", "Promise of Blood"),
    ],
)
def test_a_decimal_position_survives(relative: str, position: str, title: str) -> None:
    """Path.stem read ".2" as a file extension, collapsing three novellas."""
    result = parse(relative)

    assert result.position == position
    assert result.title == title


# ---------------------------------------------------------------------------
# naming the book
# ---------------------------------------------------------------------------


def test_a_directory_keeps_its_whole_name() -> None:
    assert _book_name(Path("/src/Powder Mage 0.2 - Servant")) == (
        "Powder Mage 0.2 - Servant"
    )


def test_an_audio_file_loses_only_its_extension() -> None:
    assert _book_name(Path("/src/The Girl of Hrusch Avenue.mp3")) == (
        "The Girl of Hrusch Avenue"
    )


def test_a_generic_filename_defers_to_its_folder() -> None:
    """ "file.m4b" says nothing; the folder that holds it does."""
    assert _book_name(Path("/src/The Martian/file.m4b")) == "The Martian"


def test_a_year_and_subtitle_are_dropped_from_a_fallback_title() -> None:
    result = parse("An Author/Food A Love Story (2014).m4b")

    assert result.title == "Food A Love Story"


def test_an_author_that_merely_repeats_the_series_is_dropped() -> None:
    """ "Dragonlance/Dragonlance" files a franchise as a person otherwise."""
    result = parse("Dragonlance/Dragonlance/Some Book.m4b")

    assert result.author == ""
