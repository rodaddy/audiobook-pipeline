"""Historical path-parsing regression guards on the rewrite parser API."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.services.parse import parse_path


def parse(raw: str, root: str = "/lib") -> ParsedPath:
    """Parse a fixture path through the public typed parser."""
    return parse_path(Path(raw), Path(root))


def test_author_is_found_above_a_series_folder() -> None:
    result = parse(
        "/lib/C S Friedman/The Coldfire Trilogy/03 - Crown of Shadows/"
        "03 - Crown of Shadows.m4a"
    )
    assert result.author == "C S Friedman"
    assert result.series == "The Coldfire Trilogy"


def test_collection_folder_is_not_mistaken_for_author() -> None:
    result = parse(
        "/lib/C S Friedman/The Coldfire Trilogy/01 - Black Sun Rising/"
        "01 - Black Sun Rising.m4a"
    )
    assert result.author == "C S Friedman"


def test_author_climb_stops_at_the_given_root() -> None:
    result = parse(
        "/Volumes/x/tFiles/Done/C S Friedman/The Coldfire Trilogy/"
        "02 - When True Night Falls/02 - When True Night Falls.m4a",
        "/Volumes/x/tFiles/Done",
    )
    assert result.author == "C S Friedman"


@pytest.mark.parametrize(
    ("raw", "title"),
    [
        (
            "/lib/Author Name/Series/Series 03 - Third Book/Series 03 - Third Book.m4a",
            "Third Book",
        ),
        ("/lib/George Orwell/1984/1984.m4b", "1984"),
        ("/lib/Some Author/Kill Bill Part 2/Kill Bill Part 2.m4b", "Kill Bill Part 2"),
    ],
)
def test_numbered_book_names_keep_their_title(raw: str, title: str) -> None:
    """Ordinary book numbering never erases a title or its genuine Part suffix."""
    assert parse(raw).title == title


def test_decimal_series_folder_does_not_truncate_the_book_name() -> None:
    result = parse(
        "/lib/C S Friedman/The Coldfire Trilogy/Coldfire 0.5 - Dominion/"
        "Coldfire 0.5 - Dominion.m4a"
    )
    assert result.author == "C S Friedman"
    assert result.title == "Dominion"


def test_part_of_marker_is_not_a_book_title() -> None:
    """A split-file counter is transport metadata, never the title to tag."""
    result = parse(
        "/lib/Brian McClellan/Powder Mage 0.2 - Servant of the Crown/"
        "Servant of the Crown Part 1 of 3.mp3"
    )
    assert result.title == "Servant of the Crown"
