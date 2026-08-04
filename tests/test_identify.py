"""Tests for catalogue search, match scoring, and chapter recovery."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.services.audible import _strip_html, search
from audiobook_pipeline.services.identify import (
    _title_score,
    best_match,
    duration_matches,
    fetch_chapters,
)

HOUR_MS = 3_600_000


def client_returning(payload: Any, *, status: int = 200) -> httpx.Client:
    """A client whose every request resolves to one canned response."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def candidate(title: str, author: str = "", asin: str = "A1") -> BookMetadata:
    """A minimal search candidate."""
    return BookMetadata(title=title, author=author, asin=asin)


# ---------------------------------------------------------------------------
# a subtitle must not sink the right match
# ---------------------------------------------------------------------------


def test_subtitle_does_not_sink_the_right_title() -> None:
    """Measured 2026-08-01: the full-string score was 41 against 100."""
    assert _title_score("Forsworn", "Forsworn: A Powder Mage Novella") > 90


def test_exact_full_title_still_scores_100() -> None:
    """Taking the max must not demote a candidate that matches completely."""
    assert _title_score("Forsworn", "Forsworn") == 100


def test_author_breaks_the_tie_between_same_titled_books() -> None:
    """The real 2026-08-01 case: two books both called Forsworn."""
    wrong = candidate("Forsworn", "David Estes", "WRONG")
    right = candidate("Forsworn: A Powder Mage Novella", "Brian McClellan", "RIGHT")

    winner = best_match(
        [wrong, right], title_hint="Forsworn", author_hint="Brian McClellan"
    )

    assert winner is not None
    assert winner.asin == "RIGHT"


def test_position_cannot_overturn_a_real_title_difference() -> None:
    first = candidate("A Completely Different Book", "Someone", "WRONG")
    second = candidate("The Crystal Shard", "R.A. Salvatore", "RIGHT")

    winner = best_match(
        [first, second],
        title_hint="The Crystal Shard",
        author_hint="R.A. Salvatore",
    )

    assert winner is not None
    assert winner.asin == "RIGHT"


def test_no_candidates_yields_no_match() -> None:
    assert best_match([], title_hint="Anything") is None


def test_missing_author_hint_does_not_award_points() -> None:
    """An unknown author must not outrank a known mismatch."""
    right = candidate("Homeland", "R.A. Salvatore", "RIGHT")
    wrong = candidate("Homeland", "Someone Else", "WRONG")

    assert best_match([wrong, right], title_hint="Homeland") is not None


# ---------------------------------------------------------------------------
# the edition guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("local_ms", "remote_ms", "expected"),
    [
        (2 * HOUR_MS, 2 * HOUR_MS, True),
        (2 * HOUR_MS, 2 * HOUR_MS + 60_000, True),  # a minute out, fine
        (2 * HOUR_MS, int(2.5 * HOUR_MS), False),  # abridged edition
        (20 * HOUR_MS, 20 * HOUR_MS + 400_000, False),  # inside 2%, over 300s
        (2 * HOUR_MS, None, False),  # unverifiable is not verified
        (0, 2 * HOUR_MS, False),
    ],
)
def test_duration_guard(local_ms: int, remote_ms: int | None, expected: bool) -> None:
    assert duration_matches(local_ms=local_ms, remote_ms=remote_ms) is expected


def test_chapters_are_rejected_when_the_edition_differs() -> None:
    """A wrong edition's marks look plausible and drift further in."""
    payload = {
        "runtimeLengthMs": 3 * HOUR_MS,
        "chapters": [{"startOffsetMs": 0, "lengthMs": 600_000, "title": "One"}],
    }

    with client_returning(payload) as client:
        result = fetch_chapters(client, "B000", local_ms=2 * HOUR_MS)

    assert result.is_empty


def test_chapters_are_accepted_when_the_duration_matches() -> None:
    payload = {
        "runtimeLengthMs": 2 * HOUR_MS,
        "chapters": [
            {"startOffsetMs": 0, "lengthMs": 600_000, "title": "Opening Credits"},
            {"startOffsetMs": 600_000, "lengthMs": 600_000, "title": "Chapter 1"},
        ],
    }

    with client_returning(payload) as client:
        result = fetch_chapters(client, "B000", local_ms=2 * HOUR_MS)

    assert [c.title for c in result.chapters.chapters] == [
        "Opening Credits",
        "Chapter 1",
    ]
    assert result.chapters.chapters[1].start_ms == 600_000
    assert result.chapters.source == "audnexus"
    assert result.edition_verified


def test_chapters_without_offsets_are_skipped() -> None:
    payload = {
        "runtimeLengthMs": 2 * HOUR_MS,
        "chapters": [
            {"title": "No offset at all"},
            {"startOffsetMs": 0, "lengthMs": 0, "title": "Zero length"},
            {"startOffsetMs": 0, "lengthMs": 600_000, "title": "Good"},
        ],
    }

    with client_returning(payload) as client:
        result = fetch_chapters(client, "B000", local_ms=2 * HOUR_MS)

    assert [c.title for c in result.chapters.chapters] == ["Good"]


def test_failed_chapter_fetch_yields_an_empty_table() -> None:
    """No chapters is a normal outcome, not an exception."""
    with client_returning({"detail": "not found"}, status=404) as client:
        assert fetch_chapters(client, "B000", local_ms=2 * HOUR_MS).is_empty


# ---------------------------------------------------------------------------
# catalogue mapping
# ---------------------------------------------------------------------------


def test_html_is_stripped_and_entities_unescaped() -> None:
    assert _strip_html("<p>Tom &amp; Jerry go <b>home</b>.</p>") == (
        "Tom & Jerry go home."
    )


def test_search_maps_a_product_into_a_validated_model() -> None:
    payload = {
        "products": [
            {
                "asin": "B00K23Y51K",
                "title": "Forsworn: A Powder Mage Novella",
                "authors": [{"name": "Brian McClellan"}],
                "narrators": [{"name": "Christian Rodska"}],
                "series": [
                    {"title": "The Powder Mage Universe"},
                    {"title": "The Powder Mage Trilogy", "sequence": "0.1"},
                ],
                "release_date": "2014-05-20",
                "publisher_name": "Orbit",
                "publisher_summary": "<p>A <b>novella</b>.</p>",
                "category_ladders": [
                    {"ladder": [{"name": "Fantasy"}, {"name": "Epic"}]}
                ],
            }
        ]
    }

    with client_returning(payload) as client:
        results = search(client, "Forsworn Brian McClellan")

    assert len(results) == 1
    book = results[0]
    assert book.asin == "B00K23Y51K"
    assert book.author == "Brian McClellan"
    assert book.narrator == "Christian Rodska"
    assert book.release_year == 2014
    assert book.summary == "A novella."
    assert book.genres == ("Fantasy", "Epic")


def test_series_with_a_position_beats_the_umbrella_series() -> None:
    """The umbrella has no position, and the position is what a path needs."""
    payload = {
        "products": [
            {
                "title": "Forsworn",
                "series": [
                    {"title": "The Powder Mage Universe"},
                    {"title": "The Powder Mage Trilogy", "sequence": "0.1"},
                ],
            }
        ]
    }

    with client_returning(payload) as client:
        book = search(client, "x")[0]

    assert book.series == "The Powder Mage Trilogy"
    assert book.series_position == "0.1"


def test_product_without_a_title_is_dropped() -> None:
    payload = {"products": [{"asin": "A1"}, {"asin": "A2", "title": "Real"}]}

    with client_returning(payload) as client:
        results = search(client, "x")

    assert [b.title for b in results] == ["Real"]


def test_failed_search_yields_no_candidates() -> None:
    """An unidentifiable book is still convertible."""
    with client_returning({"error": "nope"}, status=404) as client:
        assert search(client, "x") == []


def test_a_runtime_mismatch_is_reported_as_a_wrong_edition() -> None:
    """The reason has to survive, not just the empty table.

    A failed fetch and a wrong edition both yield no chapters, but only the
    second means the MATCH is wrong. Collapsing them is what let a 19-hour
    book be tagged as a 10.8-hour one on the 2026-08-02 live run.
    """
    payload = {
        "runtimeLengthMs": 10 * HOUR_MS,
        "chapters": [{"startOffsetMs": 0, "lengthMs": 600_000, "title": "One"}],
    }

    with client_returning(payload) as client:
        result = fetch_chapters(client, "B000", local_ms=19 * HOUR_MS)

    assert result.is_empty
    assert result.edition_mismatch
    assert not result.edition_verified


def test_a_failed_fetch_is_not_an_edition_mismatch() -> None:
    """A network failure says nothing about whether the match was right."""
    with client_returning({"detail": "not found"}, status=404) as client:
        result = fetch_chapters(client, "B000", local_ms=2 * HOUR_MS)

    assert result.is_empty
    assert not result.edition_mismatch
    assert not result.edition_verified
