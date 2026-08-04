"""Regression coverage for current concat list and chapter services."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.services import concat
from audiobook_pipeline.services.concat import (
    SOURCE_EMBEDDED,
    SOURCE_FILES,
    build_chapters,
    write_concat_list,
)


def book_of(tmp_path: Path, *names: str) -> BookDirectory:
    """Create a typed two-minute-per-file book for boundary tests."""
    folder = tmp_path / "Book"
    folder.mkdir()
    return BookDirectory(
        path=folder,
        files=tuple(
            AudioFile(path=(folder / name), duration_ms=120_000) for name in names
        ),
    )


def test_concat_list_quotes_apostrophes_with_demuxer_syntax(tmp_path: Path) -> None:
    book = book_of(tmp_path, "O'Brien.mp3")

    written = write_concat_list(book.files, tmp_path / "work")

    assert "O''Brien.mp3" in written.read_text(encoding="utf-8")


def test_concat_list_preserves_discovery_order(tmp_path: Path) -> None:
    book = book_of(tmp_path, "01.mp3", "02.mp3", "03.mp3")

    lines = write_concat_list(book.files, tmp_path / "work").read_text().splitlines()

    assert [Path(line.split("'")[1]).name for line in lines] == [
        "01.mp3",
        "02.mp3",
        "03.mp3",
    ]


def test_file_boundaries_produce_one_chapter_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = book_of(tmp_path, "01 - One.mp3", "02 - Two.mp3")
    monkeypatch.setattr(
        concat,
        "probe",
        lambda *_args, **_kwargs: type("Probed", (), {"chapters": ChapterSet()})(),
    )

    chapters = build_chapters(book)

    assert chapters.source == SOURCE_FILES
    assert [
        (chapter.start_ms, chapter.end_ms, chapter.title)
        for chapter in chapters.chapters
    ] == [
        (0, 120_000, "One"),
        (120_000, 240_000, "Two"),
    ]


def test_embedded_chapters_win_and_rebase_each_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = book_of(tmp_path, "part1.m4b", "part2.m4b")
    marks = {
        book.files[0].path: ChapterSet(
            chapters=(Chapter(start_ms=0, end_ms=120_000, title="One"),)
        ),
        book.files[1].path: ChapterSet(
            chapters=(Chapter(start_ms=0, end_ms=120_000, title="Two"),)
        ),
    }
    monkeypatch.setattr(
        concat,
        "probe",
        lambda path, **_: type("Probed", (), {"chapters": marks[path]})(),
    )

    chapters = build_chapters(book)

    assert chapters.source == SOURCE_EMBEDDED
    assert [(chapter.start_ms, chapter.title) for chapter in chapters.chapters] == [
        (0, "One"),
        (120_000, "Two"),
    ]
