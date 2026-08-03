"""Tests for the author heuristics.

Every name here is a real directory from the source tree or the live library,
because the value of these rules is that they were learned from real folders
rather than reasoned about.
"""

from __future__ import annotations

from audiobook_pipeline.services.names import (
    clean_collection_suffix,
    extract_author,
    looks_like_author,
    split_credits,
    strip_hash,
    strip_label_suffix,
)


def test_real_author_folders_are_accepted() -> None:
    """Checked against all 129 author folders in the live library."""
    assert looks_like_author("Brian McClellan")
    assert looks_like_author("C S Friedman")
    assert looks_like_author("R.A. Salvatore")
    # Ceilings are per CREDIT, not per folder: a per-folder limit of 5 words
    # and 50 characters rejected eight real authors.
    assert looks_like_author("J. R. R. Tolkien, Christopher Tolkien - editor")
    assert looks_like_author("Brandon Sanderson, Mary Robinette Kowal, Dan Wells")
    assert looks_like_author("Paul B. Thompson & Tonya R. Carter")


def test_things_that_are_not_people_are_rejected() -> None:
    """Refusing matters more than matching: a wrong author is unsweepable."""
    assert not looks_like_author("The Coldfire Trilogy")  # collection word
    assert not looks_like_author("Noobtown Books 1-7")  # collection and digits
    assert not looks_like_author("Powder Mage 01")  # digits
    assert not looks_like_author("Dragonlance")  # a franchise, one word
    assert not looks_like_author("The Martian")  # starts with an article
    assert not looks_like_author("Done")  # a staging folder
    assert not looks_like_author("tFiles")
    assert not looks_like_author("Volumes")
    assert not looks_like_author("processing")
    assert not looks_like_author("A" * 60 + " B")  # one credit, too long


def test_split_credits_separates_co_authors_and_drops_roles() -> None:
    assert split_credits("Brian McClellan") == ["Brian McClellan"]
    assert split_credits("J. R. R. Tolkien, Christopher Tolkien - editor") == [
        "J. R. R. Tolkien",
        "Christopher Tolkien",
    ]
    assert split_credits("Paul B. Thompson & Tonya R. Carter") == [
        "Paul B. Thompson",
        "Tonya R. Carter",
    ]


def test_extract_author_isolates_the_person() -> None:
    assert extract_author("R.A. Salvatore - The Legend of Drizzt") == "R.A. Salvatore"
    assert extract_author("Tad Williams (All Chaptered)") == "Tad Williams"
    assert extract_author("Brian McClellan") == "Brian McClellan"
    # A digit on the left means the split landed inside a series marker.
    assert extract_author("Powder Mage 01 - Promise of Blood") == (
        "Powder Mage 01 - Promise of Blood"
    )


def test_suffixes_are_stripped_without_eating_real_names() -> None:
    assert strip_hash("Homeland - a7edd490030561fb") == "Homeland"
    assert strip_hash("Exile - Book Two") == "Exile - Book Two"
    assert strip_label_suffix("Homeland - Unabridged") == "Homeland"
    assert strip_label_suffix("Homeland - Audiobook") == "Homeland"
    assert strip_label_suffix("Homeland - Book Two") == "Homeland - Book Two"
    assert clean_collection_suffix("Temeraire [1-5]") == "Temeraire"
