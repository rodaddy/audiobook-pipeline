"""Regression coverage for the current ffprobe, concat, and identify boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.services import concat, identify
from audiobook_pipeline.services.concat import (
    SOURCE_EMBEDDED,
    SOURCE_FILES,
    build_chapters,
)
from audiobook_pipeline.services.identify import fetch_chapters
from audiobook_pipeline.utils import ffmpeg
from audiobook_pipeline.utils.ffmpeg import build_chapter_metadata, probe


def source_book(
    tmp_path: Path, *names: str, duration_ms: int = 10_000
) -> BookDirectory:
    """Create a validated book with files that represent precise probe output."""
    folder = tmp_path / "Book"
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for name in names:
        path = folder / name
        path.write_bytes(b"audio")
        files.append(AudioFile(path=path, duration_ms=duration_ms))
    return BookDirectory(path=folder, files=tuple(files))


def chapter_set(*chapters: tuple[int, int, str]) -> ChapterSet:
    """Build embedded chapters with the same Pydantic model production uses."""
    return ChapterSet(
        chapters=tuple(
            Chapter(start_ms=start, end_ms=end, title=title)
            for start, end, title in chapters
        ),
        source=SOURCE_EMBEDDED,
    )


@pytest.fixture
def embedded(monkeypatch: pytest.MonkeyPatch) -> dict[Path, ChapterSet]:
    """Control only the ffprobe result consumed by concat."""
    table: dict[Path, ChapterSet] = {}

    def fake_probe(path: Path, **_: object) -> object:
        return type("Probed", (), {"chapters": table.get(path, ChapterSet())})()

    monkeypatch.setattr(concat, "probe", fake_probe)
    return table


def test_probe_parses_embedded_chapters_and_uses_a_fallback_title(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {
        "format": {"duration": "31.8", "format_name": "mov"},
        "streams": [{"codec_name": "aac", "sample_rate": "44100", "channels": 2}],
        "chapters": [
            {"start_time": "0", "end_time": "30.467", "tags": {"title": "Opening"}},
            {"start_time": "30.467", "end_time": "31.8"},
        ],
    }
    monkeypatch.setattr(ffmpeg, "_run", lambda *_args, **_kwargs: json.dumps(payload))

    result = probe(tmp_path / "Book.m4b")

    assert [
        (chapter.start_ms, chapter.end_ms, chapter.title)
        for chapter in result.chapters.chapters
    ] == [
        (0, 30_467, "Opening"),
        (30_467, 31_800, "Chapter 2"),
    ]


def test_probe_skips_malformed_and_zero_length_chapters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    payload = {
        "format": {"duration": "10", "format_name": "mp3"},
        "streams": [{"codec_name": "mp3", "sample_rate": "44100", "channels": 2}],
        "chapters": [
            {"start_time": "bad", "end_time": "1"},
            {"start_time": "5", "end_time": "5"},
            {"start_time": "5", "end_time": "10", "tags": {"title": "Good"}},
        ],
    }
    monkeypatch.setattr(ffmpeg, "_run", lambda *_args, **_kwargs: json.dumps(payload))

    result = probe(tmp_path / "Book.mp3")

    assert [chapter.title for chapter in result.chapters.chapters] == ["Good"]


def test_single_file_embedded_chapters_survive_into_ffmetadata(
    tmp_path: Path, embedded: dict[Path, ChapterSet]
) -> None:
    book = source_book(tmp_path, "The Martian.m4b")
    embedded[book.files[0].path] = chapter_set(
        (0, 3_000, "Opening Credits"), (3_000, 10_000, "Chapter 1")
    )

    chapters = build_chapters(book)
    metadata = build_chapter_metadata(chapters.chapters)

    assert chapters.source == SOURCE_EMBEDDED
    assert metadata.count("[CHAPTER]") == 2
    assert "title=Opening Credits" in metadata
    assert "title=Chapter 1" in metadata


def test_multi_file_embedded_chapters_are_kept_with_offsets(
    tmp_path: Path, embedded: dict[Path, ChapterSet]
) -> None:
    book = source_book(tmp_path, "Part 1.m4b", "Part 2.m4b")
    embedded[book.files[0].path] = chapter_set((0, 5_000, "A"), (5_000, 10_000, "B"))
    embedded[book.files[1].path] = chapter_set((0, 5_000, "C"), (5_000, 10_000, "D"))

    chapters = build_chapters(book)

    assert chapters.source == SOURCE_EMBEDDED
    assert [(chapter.start_ms, chapter.title) for chapter in chapters.chapters] == [
        (0, "A"),
        (5_000, "B"),
        (10_000, "C"),
        (15_000, "D"),
    ]


def test_missing_or_partial_embedded_marks_fall_back_to_file_boundaries(
    tmp_path: Path, embedded: dict[Path, ChapterSet]
) -> None:
    book = source_book(tmp_path, "01 - One.mp3", "02 - Two.mp3")
    embedded[book.files[0].path] = chapter_set((0, 10_000, "Real Chapter"))

    chapters = build_chapters(book)

    assert chapters.source == SOURCE_FILES
    assert [
        (chapter.start_ms, chapter.end_ms, chapter.title)
        for chapter in chapters.chapters
    ] == [
        (0, 10_000, "One"),
        (10_000, 20_000, "Two"),
    ]


def test_remote_chapters_are_accepted_only_for_the_matching_edition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "runtimeLengthMs": 20_000,
        "chapters": [
            {"startOffsetMs": 0, "lengthMs": 5_000, "title": "Opening"},
            {"startOffsetMs": 5_000, "lengthMs": 15_000, "title": "Chapter 1"},
        ],
    }

    monkeypatch.setattr(identify, "get_json", lambda *_args, **_kwargs: payload)
    with httpx.Client() as client:
        result = fetch_chapters(client, "ASIN", local_ms=20_000)

    assert result.edition_verified
    assert result.chapters.source == "audnexus"
    assert [chapter.title for chapter in result.chapters.chapters] == [
        "Opening",
        "Chapter 1",
    ]


def test_embedded_chapter_end_is_clamped_to_its_source_duration(
    tmp_path: Path, embedded: dict[Path, ChapterSet]
) -> None:
    """Historical guard currently missing from concat's embedded path.

    This intentionally red test records that a 10-second source can presently
    emit a chapter ending at 999999ms. No source change belongs in this port.
    """
    book = source_book(tmp_path, "Book.m4b")
    embedded[book.files[0].path] = chapter_set((0, 999_999, "Runaway"))

    chapters = build_chapters(book)

    assert chapters.chapters[0].end_ms == 10_000
