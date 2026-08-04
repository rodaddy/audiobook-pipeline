"""Regression tests for safe names and stable book identities."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.services.pipeline import book_hash
from audiobook_pipeline.utils.paths import sanitize_chapter_title, sanitize_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('a/b\\c:"d', "a_b_c_d"),
        ("..hidden", "hidden"),
        ("__private", "private"),
        ("a___b", "a_b"),
        ("chapter_01.mp3", "chapter_01.mp3"),
        ("name...", "name"),
    ],
)
def test_sanitize_filename_preserves_historical_rules(raw: str, expected: str) -> None:
    assert sanitize_filename(raw) == expected


@pytest.mark.parametrize("name", ["a" * 300 + ".mp3", "a" * 300, "界" * 100])
def test_sanitize_filename_obeys_byte_limit(name: str) -> None:
    result = sanitize_filename(name)
    assert len(result.encode()) <= 255
    if name.endswith(".mp3"):
        assert result.endswith(".mp3")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Chapter: One", "Chapter One"),
        ("a/b\\c", "a b c"),
        ("  hello  ", "hello"),
    ],
)
def test_sanitize_chapter_title_uses_readable_spaces(raw: str, expected: str) -> None:
    assert sanitize_chapter_title(raw) == expected


def _book(path: Path, durations: tuple[int, ...]) -> BookDirectory:
    files = tuple(
        AudioFile(path=path / f"chapter-{index}.mp3", duration_ms=duration)
        for index, duration in enumerate(durations)
    )
    return BookDirectory(path=path, files=files)


def test_book_hash_is_short_deterministic_hex(tmp_path: Path) -> None:
    book = _book(tmp_path / "book", (60_000, 60_000))
    digest = book_hash(book)
    assert digest == book_hash(book)
    assert len(digest) == 16
    assert all(character in "0123456789abcdef" for character in digest)


def test_book_hash_changes_with_identity_or_duration(tmp_path: Path) -> None:
    original = _book(tmp_path / "book", (60_000, 60_000))
    moved = _book(tmp_path / "other", (60_000, 60_000))
    reripped = _book(tmp_path / "book", (60_000, 61_000))
    assert len({book_hash(original), book_hash(moved), book_hash(reripped)}) == 3
