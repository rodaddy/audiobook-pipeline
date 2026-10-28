"""Regression coverage for BookDirectory's book-versus-chapter decision."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from audiobook_pipeline.models.book import (
    SEPARATE_BOOK_MIN_MS,
    AudioFile,
    BookDirectory,
)

HOUR_MS = 60 * 60 * 1000


def classified_book(tmp_path: Path, durations_ms: list[int]) -> BookDirectory:
    """Build a validated source directory with synthetic, precise runtimes."""
    folder = tmp_path / "Collection"
    files = tuple(
        AudioFile(path=folder / f"title-{index:02d}.m4b", duration_ms=duration)
        for index, duration in enumerate(durations_ms, start=1)
    )
    return BookDirectory(path=folder, files=files)


def test_forty_full_length_titles_are_separate_books(tmp_path: Path) -> None:
    durations = [(10 + index % 6) * HOUR_MS for index in range(40)]

    assert classified_book(tmp_path, durations).holds_separate_books


def test_eight_full_length_titles_are_separate_books(tmp_path: Path) -> None:
    durations = [9.07, 13.73, 16.57, 13.27, 17.39, 16.97, 16.13, 16.58]

    assert classified_book(
        tmp_path, [int(hours * HOUR_MS) for hours in durations]
    ).holds_separate_books


@pytest.mark.parametrize("minutes", [15, 60])
def test_chapter_length_files_remain_one_multi_file_book(
    tmp_path: Path, minutes: int
) -> None:
    book = classified_book(tmp_path, [minutes * 60 * 1000] * 19)

    assert not book.holds_separate_books
    assert book.is_multi_file_book


def test_short_intro_does_not_demote_full_length_books(tmp_path: Path) -> None:
    book = classified_book(tmp_path, [2 * 60 * 1000, *([12 * HOUR_MS] * 9)])

    assert book.holds_separate_books


def test_one_long_file_does_not_promote_chapters(tmp_path: Path) -> None:
    book = classified_book(tmp_path, [12 * HOUR_MS, *([HOUR_MS] * 9)])

    assert not book.holds_separate_books


def test_single_file_is_not_a_concat_candidate(tmp_path: Path) -> None:
    book = classified_book(tmp_path, [HOUR_MS])

    assert book.holds_separate_books
    assert not book.is_multi_file_book


def test_zero_duration_is_rejected_at_the_pydantic_boundary(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="greater than 0"):
        classified_book(tmp_path, [0])


@pytest.mark.parametrize(
    ("duration_ms", "separate"),
    [
        (SEPARATE_BOOK_MIN_MS - 1, False),
        (SEPARATE_BOOK_MIN_MS, True),
    ],
)
def test_two_hour_boundary_is_stable(
    tmp_path: Path, duration_ms: int, separate: bool
) -> None:
    assert classified_book(tmp_path, [duration_ms] * 5).holds_separate_books is separate
