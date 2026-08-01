"""Tests for ffprobe.are_separate_books -- books vs chapters of one book.

Multiple .m4b files in a directory are ambiguous, and the pipeline used to
resolve that ambiguity by COUNT: more than one meant "chaptered book, concat
them". Pointed at a Legend of Drizzt folder on 2026-08-01 that rule planned a
single 510-hour M4B from 40 complete novels, tagged as one album.

Real durations measured that day, used as the fixtures below:
    40 Legend of Drizzt books    10.19-15.71h each
     8 Noobtown books             9.07-17.39h each
    19 Promise of Blood chapters  0.95-1.01h each
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from audiobook_pipeline.ffprobe import (
    SEPARATE_BOOK_MIN_DURATION,
    are_separate_books,
)

HOUR = 3600.0


def _files(n: int) -> list[Path]:
    return [Path(f"/fake/book{i:02d}.m4b") for i in range(n)]


def _with_durations(durations: list[float]):
    """Patch get_duration to return each duration in order of file name."""
    mapping = {f"/fake/book{i:02d}.m4b": d for i, d in enumerate(durations)}
    return patch(
        "audiobook_pipeline.ffprobe.get_duration",
        side_effect=lambda p: mapping[str(p)],
    )


class TestSeparateBooks:
    def test_forty_full_length_books(self):
        """Legend of Drizzt: 40 novels, 10-15h each."""
        durations = [(10 + (i % 6)) * HOUR for i in range(40)]
        with _with_durations(durations):
            assert are_separate_books(_files(40)) is True

    def test_eight_full_length_books(self):
        """Noobtown: 9-17h each."""
        durations = [9.07, 13.73, 16.57, 13.27, 17.39, 16.97, 16.13, 16.58]
        with _with_durations([d * HOUR for d in durations]):
            assert are_separate_books(_files(8)) is True

    def test_hour_long_chapters_are_one_book(self):
        """Promise of Blood: 19 chapters of ~1h. Must concatenate."""
        durations = [1.0 * HOUR] * 19
        with _with_durations(durations):
            assert are_separate_books(_files(19)) is False

    def test_short_chapters_are_one_book(self):
        """Typical 10-20 minute chapters."""
        with _with_durations([900.0] * 30):
            assert are_separate_books(_files(30)) is False


class TestMedianNotMeanOrMin:
    """A stray short or long file must not flip the verdict."""

    def test_short_intro_track_does_not_demote_real_books(self):
        """One 2-minute intro among 12-hour books: still separate books."""
        durations = [120.0] + [12 * HOUR] * 9
        with _with_durations(durations):
            assert are_separate_books(_files(10)) is True

    def test_one_long_file_does_not_promote_chapters(self):
        """One 12h file among 1h chapters: still one book."""
        durations = [12 * HOUR] + [1 * HOUR] * 9
        with _with_durations(durations):
            assert are_separate_books(_files(10)) is False


class TestFailsSafe:
    """On any doubt, treat as separate books and refuse to concatenate.

    The two errors are not equally costly: not concatenating is visible and
    reversible; concatenating 40 books is not.
    """

    def test_probe_failure_treated_as_separate(self):
        with patch(
            "audiobook_pipeline.ffprobe.get_duration",
            side_effect=OSError("ffprobe exploded"),
        ):
            assert are_separate_books(_files(5)) is True

    def test_zero_duration_treated_as_separate(self):
        with _with_durations([0.0] * 5):
            assert are_separate_books(_files(5)) is True

    def test_single_file_is_never_chapters(self):
        assert are_separate_books(_files(1)) is True

    def test_empty_list_is_never_chapters(self):
        assert are_separate_books([]) is True


class TestThresholdBoundary:
    def test_just_under_threshold_is_chapters(self):
        with _with_durations([SEPARATE_BOOK_MIN_DURATION - 1] * 5):
            assert are_separate_books(_files(5)) is False

    def test_exactly_at_threshold_is_separate(self):
        with _with_durations([SEPARATE_BOOK_MIN_DURATION] * 5):
            assert are_separate_books(_files(5)) is True
