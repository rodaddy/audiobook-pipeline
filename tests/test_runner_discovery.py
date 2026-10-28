"""Regression coverage for discovery without the retired runner API."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from audiobook_pipeline.models.book import SOURCE_EXTENSIONS
from audiobook_pipeline.services import discovery
from audiobook_pipeline.services.discovery import discover_books, is_source_audio
from audiobook_pipeline.utils.ffmpeg import FfmpegError

HOUR_MS = 60 * 60 * 1000
DurationMap = dict[Path, int]


@pytest.fixture
def durations(monkeypatch: pytest.MonkeyPatch) -> DurationMap:
    """Make discovery classification deterministic without decoding fixtures."""
    table: DurationMap = {}

    def fake_probe(path: Path, **_: object) -> SimpleNamespace:
        try:
            return SimpleNamespace(duration_ms=table[path])
        except KeyError as exc:
            raise FfmpegError("ffprobe", f"unreadable: {path}") from exc

    monkeypatch.setattr(discovery, "probe", fake_probe)
    return table


def source_file(
    root: Path, relative: str, durations: DurationMap, hours: float
) -> Path:
    """Create a source-shaped file and register the duration its probe reports."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    durations[path] = int(hours * HOUR_MS)
    return path


def test_loose_audio_does_not_prune_nested_books(
    tmp_path: Path, durations: DurationMap
) -> None:
    """A collection keeps both its loose title and every nested title."""
    source_file(tmp_path, "Loose Extra.mp3", durations, 8)
    for relative in (
        "Author A/Book One/01.mp3",
        "Author A/Book Two/01.mp3",
        "Author B/Book Three/01.mp3",
    ):
        source_file(tmp_path, relative, durations, 9)

    found = discover_books(tmp_path)

    assert {book.identity_path.relative_to(tmp_path).as_posix() for book in found} == {
        "Loose Extra.mp3",
        "Author A/Book One/01.mp3",
        "Author A/Book Two/01.mp3",
        "Author B/Book Three/01.mp3",
    }


def test_collection_without_loose_audio_finds_each_nested_book(
    tmp_path: Path, durations: DurationMap
) -> None:
    for relative in ("Author A/Book One/01.mp3", "Author B/Book Two/01.mp3"):
        source_file(tmp_path, relative, durations, 9)

    assert {
        book.path.relative_to(tmp_path).as_posix() for book in discover_books(tmp_path)
    } == {
        "Author A/Book One",
        "Author B/Book Two",
    }


@pytest.mark.parametrize("suffix", sorted(SOURCE_EXTENSIONS))
def test_every_declared_source_format_is_discoverable(
    tmp_path: Path, durations: DurationMap, suffix: str
) -> None:
    """M4B is input audio too; extension never means already finished."""
    source = source_file(tmp_path, f"Book/source{suffix}", durations, 8)

    found = discover_books(tmp_path)

    assert is_source_audio(source)
    assert [audio.path for book in found for audio in book.files] == [source]


def test_unreadable_audio_is_excluded_without_aborting_discovery(
    tmp_path: Path, durations: DurationMap
) -> None:
    readable = source_file(tmp_path, "Book/01.mp3", durations, 8)
    broken = tmp_path / "Book/02.mp3"
    broken.write_bytes(b"not audio")

    found = discover_books(tmp_path)

    assert [audio.path for book in found for audio in book.files] == [readable]


def test_disc_parts_are_one_logical_book_when_root_has_intro(
    tmp_path: Path, durations: DurationMap
) -> None:
    """Historical guard: CD folders must not become independent books.

    The current discovery walk emits the root, CD1, and CD2 independently.
    Keep this red until discovery grows an explicit disc-part grouping rule.
    """
    for relative in ("Book/Intro.mp3", "Book/CD1/01.mp3", "Book/CD2/01.mp3"):
        source_file(tmp_path, relative, durations, 1)

    found = discover_books(tmp_path / "Book")

    assert len(found) == 1
    assert [audio.path.name for audio in found[0].files] == [
        "Intro.mp3",
        "01.mp3",
        "01.mp3",
    ]
