"""Tests for the author heuristics.

Every case here is a real directory from the source tree or the live library,
because the whole value of these rules is that they were learned from real
folders rather than reasoned about.
"""

from __future__ import annotations

import pytest

from audiobook_pipeline.services.names import (
    clean_collection_suffix,
    extract_author,
    looks_like_author,
    strip_hash,
    strip_label_suffix,
)


@pytest.mark.parametrize(
    "name",
    [
        "Brian McClellan",
        "C S Friedman",
        "R.A. Salvatore",
        "Lois McMaster Bujold",
        "J. R. R. Tolkien, Christopher Tolkien",
        "J. R. R. Tolkien, Christopher Tolkien - editor",
        "Brandon Sanderson, Mary Robinette Kowal, Dan Wells, Howard Tayler",
        "Paul B. Thompson & Tonya R. Carter",
    ],
)
def test_real_authors_are_accepted(name: str) -> None:
    assert looks_like_author(name)


@pytest.mark.parametrize(
    ("name", "why"),
    [
        ("The Coldfire Trilogy", "collection word"),
        ("Noobtown Books 1-7", "collection word and digits"),
        ("Powder Mage 01", "digits"),
        ("Dragonlance", "a single word is a franchise"),
        ("The Martian", "starts with an article"),
        ("Done", "single word staging folder"),
        ("tFiles", "single word staging folder"),
        ("Volumes", "collection word"),
        ("processing", "pipeline folder"),
    ],
)
def test_non_authors_are_rejected(name: str, why: str) -> None:
    """Refusing matters more than matching: a wrong author is unsweepable."""
    assert not looks_like_author(name), why


def test_a_single_credit_longer_than_the_ceiling_is_rejected() -> None:
    """Measured per person: a co-authored folder is legitimately long."""
    assert not looks_like_author("A" * 60 + " B")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("R.A. Salvatore - The Legend of Drizzt", "R.A. Salvatore"),
        ("Tad Williams (All Chaptered)", "Tad Williams"),
        ("Brian McClellan", "Brian McClellan"),
        # A digit on the left means the split landed inside a series marker,
        # not between an author and a series.
        (
            "Powder Mage 01 - Promise of Blood",
            "Powder Mage 01 - Promise of Blood",
        ),
    ],
)
def test_extract_author(raw: str, expected: str) -> None:
    assert extract_author(raw) == expected


def test_strip_hash_removes_a_pipeline_work_suffix() -> None:
    assert strip_hash("Homeland - a7edd490030561fb") == "Homeland"


def test_strip_hash_leaves_a_real_dash_alone() -> None:
    assert strip_hash("Exile - Book Two") == "Exile - Book Two"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Homeland - Unabridged", "Homeland"),
        ("Homeland - Audiobook", "Homeland"),
        ("Homeland - Book Two", "Homeland - Book Two"),
    ],
)
def test_strip_label_suffix(raw: str, expected: str) -> None:
    assert strip_label_suffix(raw) == expected


def test_clean_collection_suffix() -> None:
    assert clean_collection_suffix("Temeraire [1-5]") == "Temeraire"
