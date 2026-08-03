"""Tests for the individual path patterns.

Each pattern is tested ALONE. That is the point of them being separate
functions: a failing layout is reproduced with one string, not by building a
directory tree and running a parser over it.
"""

from __future__ import annotations

import pytest

from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.services.patterns import (
    author_dash_series,
    author_only,
    bracket_position,
    hash_marked,
    numbered_bare,
    numbered_dash,
)


def found(result: ParsedPath) -> dict[str, str]:
    """Only the fields a pattern actually filled."""
    return {key: value for key, value in result.model_dump().items() if value}


# ---------------------------------------------------------------------------
# hash-marked, the most explicit layout
# ---------------------------------------------------------------------------


def test_hash_marker_yields_every_field() -> None:
    assert found(hash_marked("Salvatore-Legend of Drizzt-#3-Sojourn")) == {
        "author": "Salvatore",
        "series": "Legend of Drizzt",
        "position": "3",
        "title": "Sojourn",
    }


def test_the_last_hash_marker_wins() -> None:
    """A nested subseries resolves to the innermost position."""
    assert hash_marked("A-Outer-#1-Inner-#2-Title").position == "2"


def test_a_name_without_a_marker_is_not_this_pattern() -> None:
    assert hash_marked("Homeland").is_empty


# ---------------------------------------------------------------------------
# numbered layouts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "position", "title"),
    [
        ("Deathgate Cycle 1 - Dragon Wing", "1", "Dragon Wing"),
        # A decimal position must survive: "0.2" is a real novella number and
        # truncating it to "0" collapses several books onto one name.
        ("Powder Mage 0.2 - Servant of the Crown", "0.2", "Servant of the Crown"),
    ],
)
def test_numbered_dash(name: str, position: str, title: str) -> None:
    result = numbered_dash(name)
    assert result.position == position
    assert result.title == title


def test_numbered_bare_splits_on_the_number() -> None:
    assert found(numbered_bare("The First Law 04 Best Served Cold")) == {
        "series": "The First Law",
        "position": "04",
        "title": "Best Served Cold",
    }


def test_a_trailing_number_is_not_a_title() -> None:
    """Otherwise "Book 3" parses as a series called "Book"."""
    assert numbered_bare("Book 3").is_empty


def test_bracket_position() -> None:
    assert found(bracket_position("Mistborn [01] The Final Empire")) == {
        "series": "Mistborn",
        "position": "01",
        "title": "The Final Empire",
    }


# ---------------------------------------------------------------------------
# author layouts
# ---------------------------------------------------------------------------


def test_author_dash_series_splits_a_combined_folder() -> None:
    assert found(author_dash_series("R.A. Salvatore - The Legend of Drizzt")) == {
        "author": "R.A. Salvatore",
        "series": "The Legend of Drizzt",
    }


def test_author_dash_series_refuses_a_series_marker() -> None:
    """The left side has a digit, so the dash is not an author boundary."""
    assert author_dash_series("Powder Mage 01 - Promise of Blood").is_empty


def test_author_only_accepts_a_plain_name() -> None:
    assert author_only("Brian McClellan").author == "Brian McClellan"


@pytest.mark.parametrize("name", ["The Coldfire Trilogy", "Done", "Dragonlance"])
def test_author_only_refuses_anything_that_is_not_a_person(name: str) -> None:
    assert author_only(name).is_empty
