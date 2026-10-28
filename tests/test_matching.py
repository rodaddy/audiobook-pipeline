"""Tests for reducing two spellings of one book to one key.

Every string here is a real filename from the source or target library. The
measurements quoted in the comments are the ones recorded when the legacy
diff was corrected on 2026-08-01.
"""

from __future__ import annotations

import pytest

from audiobook_pipeline.services.matching import normalize_title, titles_match


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Promise of Blood", "promise of blood"),
        ("Book 1 - Promise of Blood", "promise of blood"),
        ("Promise of Blood (Unabridged)", "promise of blood"),
        ("Promise of Blood [B00AAI79WY]", "promise of blood"),
        # The three part spellings seen in the wild. Each one reported the
        # same book as 3, 19, and 19 separate missing books before it was
        # handled.
        ("Servant of the Crown Part 1 of 3", "servant of the crown"),
        ("The Autumn Republic (Unabridged) Part 01 of 19", "the autumn republic"),
        ("Promise of Blood01-19", "promise of blood"),
        # An accent must not split one book into two.
        ("The Children of Húrin", "the children of hurin"),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_every_spelling_of_one_book_reduces_to_the_same_key() -> None:
    """This is the whole point: one book, many filenames, one key."""
    keys = {
        normalize_title(spelling)
        for spelling in (
            "Promise of Blood",
            "Book 1 - Promise of Blood",
            "Promise of Blood (Unabridged)",
            "Promise of Blood Part 1 of 19",
            "Promise of Blood01-19",
            "01 - Promise of Blood [B00AAI79WY]",
        )
    }
    assert keys == {"promise of blood"}


def test_a_title_that_is_only_a_marker_yields_no_key() -> None:
    """An empty key must never match, or every unnamed file matches itself."""
    assert normalize_title("01") == ""
    assert normalize_title("Part 1 of 3") == ""


@pytest.mark.parametrize(
    ("left", "right", "same_book"),
    [
        ("promise of blood", "promise of blood", True),
        # One side carrying the series name is the usual source/target
        # difference, and must still match.
        ("the final empire", "mistborn the final empire", True),
        # Two different books by one author must NOT collapse.
        ("exile", "exodus", False),
        ("homeland", "sojourn", False),
        # An empty key is not evidence of anything, even against itself.
        ("", "", False),
        ("", "homeland", False),
    ],
)
def test_titles_match(left: str, right: str, same_book: bool) -> None:
    assert titles_match(left, right) is same_book
