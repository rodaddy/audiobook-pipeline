"""Tests for api/audnexus.py -- remote chapter timings and their guards.

Audnexus supplies chapters for a book whose audio carries none: Promise of
Blood arrives as 19 MP3 files of ~60 minutes, which are encoding splits, and
the book really has 42 chapters.

The guards matter more than the happy path. Writing a plausible but WRONG
chapter table into a live library is the worst available outcome -- it looks
correct, plays fine, and every chapter jump lands in the wrong place.
Measured 2026-08-01 across the books in this library, local-vs-remote runtime
differences were 0.00, 0.00, 0.13, 0.18 and 0.28 percent for correct matches
and 13.42 percent for a wrong edition. Nothing in between.
"""

from __future__ import annotations

from unittest.mock import patch

from audiobook_pipeline.api.audnexus import (
    CHAPTER_DURATION_TOLERANCE_PCT,
    get_chapters,
)

HOUR_MS = 3600_000


def _payload(chapters: list[dict], runtime_ms: int, accurate: bool = True) -> dict:
    return {
        "chapters": chapters,
        "runtimeLengthMs": runtime_ms,
        "isAccurate": accurate,
        "brandIntroDurationMs": 1904,
        "brandOutroDurationMs": 4969,
    }


def _ch(start_ms: int, length_ms: int, title: str) -> dict:
    return {"startOffsetMs": start_ms, "lengthMs": length_ms, "title": title}


def _fetch(payload):
    return patch("audiobook_pipeline.api.audnexus.fetch_chapters", return_value=payload)


class TestHappyPath:
    def test_returns_normalised_chapters(self):
        payload = _payload(
            [_ch(0, 1000, "Opening Credits"), _ch(1000, 2000, "Chapter 1")], 3000
        )
        with _fetch(payload):
            got = get_chapters("B00C2DMXCG", 3.0)
        assert got == [
            {"start_ms": 0, "end_ms": 1000, "title": "Opening Credits"},
            {"start_ms": 1000, "end_ms": 3000, "title": "Chapter 1"},
        ]

    def test_untitled_chapter_gets_positional_name(self):
        with _fetch(_payload([_ch(0, 1000, "")], 1000)):
            got = get_chapters("X", 1.0)
        assert got[0]["title"] == "Chapter 1"

    def test_offsets_are_used_raw_not_brand_adjusted(self):
        """brandIntroDurationMs must NOT be subtracted from startOffsetMs.

        Audible's offsets already account for branding. Subtracting it again
        shifts every chapter earlier by a couple of seconds -- invisible in a
        spot check, wrong for the whole book.
        """
        with _fetch(_payload([_ch(50_000, 1000, "Chapter 1")], 51_000)):
            got = get_chapters("X", 51.0)
        assert got[0]["start_ms"] == 50_000


class TestDurationGuard:
    def test_matching_duration_accepted(self):
        with _fetch(_payload([_ch(0, HOUR_MS, "One")], HOUR_MS)):
            assert get_chapters("X", 3600.0)

    def test_wrong_edition_rejected(self):
        """The real 13.42% case: 3.38h local vs 2.98h remote."""
        remote_ms = int(2.98 * HOUR_MS)
        with _fetch(_payload([_ch(0, remote_ms, "One")], remote_ms)):
            assert get_chapters("X", 3.38 * 3600) == []

    def test_just_inside_tolerance_accepted(self):
        remote_ms = 10 * HOUR_MS
        local_sec = (remote_ms / 1000) * (
            1 + (CHAPTER_DURATION_TOLERANCE_PCT / 100) / 2
        )
        with _fetch(_payload([_ch(0, remote_ms, "One")], remote_ms)):
            assert get_chapters("X", local_sec)

    def test_large_absolute_gap_rejected_even_inside_percentage(self):
        """A 100h book 0.5% adrift is 30 minutes out. Percentage alone is not
        enough of a guard on a long book."""
        remote_ms = 100 * HOUR_MS
        local_sec = (remote_ms / 1000) + 1800
        with _fetch(_payload([_ch(0, remote_ms, "One")], remote_ms)):
            assert get_chapters("X", local_sec) == []

    def test_missing_runtime_rejected(self):
        payload = {"chapters": [_ch(0, 1000, "One")], "runtimeLengthMs": 0}
        with _fetch(payload):
            assert get_chapters("X", 1.0) == []


class TestAccuracyFlag:
    def test_inaccurate_payload_rejected(self):
        with _fetch(_payload([_ch(0, 1000, "One")], 1000, accurate=False)):
            assert get_chapters("X", 1.0) == []

    def test_missing_flag_is_treated_as_accurate(self):
        payload = {"chapters": [_ch(0, 1000, "One")], "runtimeLengthMs": 1000}
        with _fetch(payload):
            assert get_chapters("X", 1.0)


class TestMalformedData:
    def test_no_payload_returns_empty(self):
        with _fetch(None):
            assert get_chapters("X", 1.0) == []

    def test_malformed_chapter_skipped(self):
        bad = {"startOffsetMs": "nope", "lengthMs": 1000, "title": "Bad"}
        with _fetch(_payload([bad, _ch(0, 1000, "Good")], 1000)):
            got = get_chapters("X", 1.0)
        assert [c["title"] for c in got] == ["Good"]

    def test_chapter_past_end_of_local_audio_is_clamped(self):
        with _fetch(_payload([_ch(0, 999_000, "Runaway")], 1000)):
            got = get_chapters("X", 1.0)
        assert got[0]["end_ms"] == 1000

    def test_chapter_starting_past_end_is_dropped(self):
        with _fetch(_payload([_ch(0, 500, "Ok"), _ch(50_000, 100, "Late")], 1000)):
            got = get_chapters("X", 1.0)
        assert [c["title"] for c in got] == ["Ok"]

    def test_non_monotonic_table_rejected_entirely(self):
        """Individually plausible marks that go backwards are a corrupt table."""
        with _fetch(_payload([_ch(5000, 100, "Second"), _ch(0, 100, "First")], 10_000)):
            assert get_chapters("X", 10.0) == []

    def test_no_usable_chapters_returns_empty(self):
        bad = {"startOffsetMs": None, "lengthMs": None, "title": "Bad"}
        with _fetch(_payload([bad], 1000)):
            assert get_chapters("X", 1.0) == []
