"""Tests for the shared text helpers.

These exist because the same transformations were previously defined in
several modules and DIVERGED -- ``identify`` treated an en-dash as a subtitle
separator and ``organize`` did not. One definition, one set of tests.
"""

from __future__ import annotations

import pytest

from audiobook_pipeline.utils.text import (
    EN_DASH,
    collapse_whitespace,
    fold_accents,
    has_digit,
    strip_brackets,
    strip_html,
    strip_punctuation,
    strip_subtitle,
    strip_subtitle_or_dash,
    strip_year,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  The   Name  of the Wind ", "The Name of the Wind"),
        ("one\ttwo\nthree", "one two three"),
        ("", ""),
    ],
)
def test_collapse_whitespace(raw: str, expected: str) -> None:
    assert collapse_whitespace(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Forsworn: A Powder Mage Novella", "Forsworn"),
        ("The Name of the Wind: Kingkiller Day One", "The Name of the Wind"),
        (f"Exile {EN_DASH} Book Two of the Dark Elf Trilogy", "Exile"),
        ("Homeland", "Homeland"),
        # A plain hyphen is NOT a subtitle separator for naming: a book really
        # can be called "Something - Something".
        ("Exile - Book Two", "Exile - Book Two"),
        # Never return nothing: an unnamed file is worse than a long one.
        (":", ":"),
    ],
)
def test_strip_subtitle(raw: str, expected: str) -> None:
    assert strip_subtitle(raw) == expected


def test_strip_subtitle_or_dash_also_splits_a_hyphen() -> None:
    """The matching variant is allowed to be greedier than the naming one."""
    assert strip_subtitle_or_dash("Exile - Book Two") == "Exile"
    assert strip_subtitle("Exile - Book Two") == "Exile - Book Two"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Food A Love Story (2014)", "Food A Love Story"),
        ("No Year Here", "No Year Here"),
        # Not a year: four digits are required.
        ("Something (123)", "Something (123)"),
    ],
)
def test_strip_year(raw: str, expected: str) -> None:
    assert strip_year(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tad Williams (All Chaptered)", "Tad Williams"),
        ("Temeraire [1-5]", "Temeraire"),
        ("Both (a) [b]", "Both"),
    ],
)
def test_strip_brackets(raw: str, expected: str) -> None:
    assert strip_brackets(raw) == expected


def test_strip_punctuation_is_for_comparison_only() -> None:
    assert strip_punctuation("R.A. Salvatore!") == "RA Salvatore"


def test_strip_html_unescapes_entities_too() -> None:
    assert (
        strip_html("<p>Tom &amp; Jerry go <b>home</b>.</p>") == "Tom & Jerry go home."
    )


def test_fold_accents_makes_one_book_compare_equal_to_itself() -> None:
    """macOS stores decomposed, so the same title can differ from itself."""
    assert fold_accents("Húrin") == fold_accents("Húrin") == "Hurin"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Brian McClellan", False), ("Powder Mage 01", True), ("", False)],
)
def test_has_digit(raw: str, expected: bool) -> None:
    assert has_digit(raw) is expected
