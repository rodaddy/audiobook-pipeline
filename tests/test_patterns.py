"""Tests for the individual path patterns.

One test per pattern, with a table of cases. Each pattern is exercised ALONE,
which is the point of them being separate functions: a failing layout is
reproduced with one string rather than by building a directory tree.
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

NOTHING: dict[str, str] = {}


def found(result: ParsedPath) -> dict[str, str]:
    """Only the fields a pattern actually filled."""
    return {key: value for key, value in result.model_dump().items() if value}


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "Salvatore-Legend of Drizzt-#3-Sojourn",
            {
                "author": "Salvatore",
                "series": "Legend of Drizzt",
                "position": "3",
                "title": "Sojourn",
            },
        ),
        # The LAST marker wins, so a nested subseries gives the inner position.
        (
            "A-Outer-#1-Inner-#2-Title",
            {"author": "A", "series": "Outer", "position": "2", "title": "Title"},
        ),
        ("Homeland", NOTHING),
    ],
)
def test_hash_marked(name: str, expected: dict[str, str]) -> None:
    assert found(hash_marked(name)) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "Deathgate Cycle 1 - Dragon Wing",
            {"series": "Deathgate Cycle", "position": "1", "title": "Dragon Wing"},
        ),
        # A decimal position must survive: "0.2" is a real novella number, and
        # truncating it collapses several books onto one name.
        (
            "Powder Mage 0.2 - Servant of the Crown",
            {
                "series": "Powder Mage",
                "position": "0.2",
                "title": "Servant of the Crown",
            },
        ),
        ("Homeland", NOTHING),
    ],
)
def test_numbered_dash(name: str, expected: dict[str, str]) -> None:
    assert found(numbered_dash(name)) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "The First Law 04 Best Served Cold",
            {
                "series": "The First Law",
                "position": "04",
                "title": "Best Served Cold",
            },
        ),
        # Without this guard "Book 3" parses as a series called "Book".
        ("Book 3", NOTHING),
    ],
)
def test_numbered_bare(name: str, expected: dict[str, str]) -> None:
    assert found(numbered_bare(name)) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "Mistborn [01] The Final Empire",
            {"series": "Mistborn", "position": "01", "title": "The Final Empire"},
        ),
        ("Mistborn The Final Empire", NOTHING),
    ],
)
def test_bracket_position(name: str, expected: dict[str, str]) -> None:
    assert found(bracket_position(name)) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "R.A. Salvatore - The Legend of Drizzt",
            {"author": "R.A. Salvatore", "series": "The Legend of Drizzt"},
        ),
        # A digit on the left means the dash is a series marker, not a boundary.
        ("Powder Mage 01 - Promise of Blood", NOTHING),
        ("Brian McClellan", NOTHING),
    ],
)
def test_author_dash_series(name: str, expected: dict[str, str]) -> None:
    assert found(author_dash_series(name)) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Brian McClellan", {"author": "Brian McClellan"}),
        ("Tad Williams (All Chaptered)", {"author": "Tad Williams"}),
        ("The Coldfire Trilogy", NOTHING),
        ("Done", NOTHING),
        ("Dragonlance", NOTHING),
    ],
)
def test_author_only(name: str, expected: dict[str, str]) -> None:
    assert found(author_only(name)) == expected
