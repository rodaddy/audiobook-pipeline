"""Typed Audnexus chapter-fetch and edition-guard tests with mocked HTTP."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from audiobook_pipeline.services.identify import fetch_chapters

HOUR_MS = 3_600_000


def payload(
    chapters: list[dict[str, Any]], runtime_ms: int, *, accurate: bool = True
) -> dict[str, Any]:
    """Build one Audnexus response shape without using a network fixture."""
    return {
        "chapters": chapters,
        "runtimeLengthMs": runtime_ms,
        "isAccurate": accurate,
        "brandIntroDurationMs": 1904,
        "brandOutroDurationMs": 4969,
    }


def chapter(start_ms: int, length_ms: int, title: str) -> dict[str, Any]:
    """Build one external chapter record."""
    return {"startOffsetMs": start_ms, "lengthMs": length_ms, "title": title}


def client_returning(payload: dict[str, Any], *, status: int = 200) -> httpx.Client:
    """Return one mocked HTTP client serving the supplied Audnexus payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestHappyPath:
    def test_returns_normalized_chapters(self) -> None:
        response = payload(
            [chapter(0, 1000, "Opening Credits"), chapter(1000, 2000, "Chapter 1")],
            3000,
        )

        with client_returning(response) as client:
            result = fetch_chapters(client, "B00C2DMXCG", local_ms=3000)

        assert [
            (item.start_ms, item.end_ms, item.title)
            for item in result.chapters.chapters
        ] == [
            (0, 1000, "Opening Credits"),
            (1000, 3000, "Chapter 1"),
        ]
        assert result.edition_verified

    def test_untitled_chapter_gets_a_positional_name(self) -> None:
        with client_returning(payload([chapter(0, 1000, "")], 1000)) as client:
            result = fetch_chapters(client, "X", local_ms=1000)

        assert result.chapters.chapters[0].title == "Chapter 1"

    def test_offsets_are_used_without_brand_adjustment(self) -> None:
        response = payload([chapter(50_000, 1000, "Chapter 1")], 51_000)

        with client_returning(response) as client:
            result = fetch_chapters(client, "X", local_ms=51_000)

        assert result.chapters.chapters[0].start_ms == 50_000


class TestDurationGuard:
    def test_matching_duration_is_accepted(self) -> None:
        with client_returning(payload([chapter(0, HOUR_MS, "One")], HOUR_MS)) as client:
            assert fetch_chapters(client, "X", local_ms=HOUR_MS).edition_verified

    def test_wrong_edition_is_rejected(self) -> None:
        remote_ms = int(2.98 * HOUR_MS)
        with client_returning(
            payload([chapter(0, remote_ms, "One")], remote_ms)
        ) as client:
            result = fetch_chapters(client, "X", local_ms=int(3.38 * HOUR_MS))

        assert result.is_empty and result.edition_mismatch

    def test_duration_just_inside_tolerance_is_accepted(self) -> None:
        remote_ms = 10 * HOUR_MS
        local_ms = remote_ms + 60_000
        with client_returning(
            payload([chapter(0, remote_ms, "One")], remote_ms)
        ) as client:
            assert fetch_chapters(client, "X", local_ms=local_ms).edition_verified

    def test_large_absolute_gap_is_rejected_inside_the_percentage_limit(self) -> None:
        remote_ms = 100 * HOUR_MS
        with client_returning(
            payload([chapter(0, remote_ms, "One")], remote_ms)
        ) as client:
            result = fetch_chapters(client, "X", local_ms=remote_ms + 1_800_000)

        assert result.is_empty and result.edition_mismatch

    def test_missing_runtime_is_rejected(self) -> None:
        with client_returning({
            "chapters": [chapter(0, 1000, "One")],
            "runtimeLengthMs": 0,
        }) as client:
            result = fetch_chapters(client, "X", local_ms=1000)

        assert result.is_empty and result.edition_mismatch


class TestAccuracyFlag:
    def test_inaccurate_hint_does_not_replace_the_runtime_edition_guard(self) -> None:
        with client_returning(
            payload([chapter(0, 1000, "One")], 1000, accurate=False)
        ) as client:
            result = fetch_chapters(client, "X", local_ms=1000)

        assert result.edition_verified

    def test_missing_accuracy_hint_still_accepts_a_verified_edition(self) -> None:
        response = {"chapters": [chapter(0, 1000, "One")], "runtimeLengthMs": 1000}

        with client_returning(response) as client:
            assert fetch_chapters(client, "X", local_ms=1000).edition_verified


class TestMalformedData:
    def test_http_failure_returns_an_unverified_empty_table(self) -> None:
        with client_returning({"detail": "not found"}, status=404) as client:
            result = fetch_chapters(client, "X", local_ms=1000)

        assert result.is_empty and not result.edition_mismatch

    def test_non_numeric_chapter_data_is_rejected_at_the_boundary(self) -> None:
        bad = {"startOffsetMs": "nope", "lengthMs": 1000, "title": "Bad"}
        response = payload([bad, chapter(0, 1000, "Good")], 1000)

        with client_returning(response) as client, pytest.raises(ValueError):
            fetch_chapters(client, "X", local_ms=1000)

    def test_chapter_past_end_of_local_audio_is_clamped(self) -> None:
        response = payload([chapter(0, 999_000, "Runaway")], 1000)

        with client_returning(response) as client:
            result = fetch_chapters(client, "X", local_ms=1000)

        assert result.chapters.chapters[0].end_ms == 1000

    def test_chapter_starting_past_end_of_local_audio_is_dropped(self) -> None:
        response = payload(
            [
                chapter(0, 500, "Ok"),
                chapter(50_000, 100, "Late"),
                chapter(900, -100, "Inverted"),
            ],
            1000,
        )

        with client_returning(response) as client:
            result = fetch_chapters(client, "X", local_ms=1000)

        assert [item.title for item in result.chapters.chapters] == ["Ok"]

    def test_non_monotonic_table_is_rejected_at_the_typed_boundary(self) -> None:
        response = payload(
            [chapter(5000, 100, "Second"), chapter(0, 100, "First")], 10_000
        )

        with client_returning(response) as client, pytest.raises(ValueError):
            fetch_chapters(client, "X", local_ms=10_000)

    def test_no_usable_chapters_returns_an_empty_verified_table(self) -> None:
        bad = {"startOffsetMs": None, "lengthMs": None, "title": "Bad"}

        with client_returning(payload([bad], 1000)) as client:
            result = fetch_chapters(client, "X", local_ms=1000)

        assert result.is_empty and result.edition_verified
