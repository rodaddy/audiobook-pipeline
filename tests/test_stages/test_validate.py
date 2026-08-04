"""Regression coverage for current validation handoff failure contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.config import EncodingSettings, PathSettings
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.media import AudioStream, ProbeResult
from audiobook_pipeline.services import validate
from audiobook_pipeline.services.validate import (
    ValidationHandoffError,
    load_validated_book,
    validate_book,
)


def source_book(
    tmp_path: Path, *names: str, duration_ms: int = 60_000
) -> BookDirectory:
    """Create real non-empty input files with discovery-time durations."""
    folder = tmp_path / "Book"
    folder.mkdir()
    files = []
    for name in names:
        path = folder / name
        path.write_bytes(b"audio")
        files.append(AudioFile(path=path, duration_ms=duration_ms))
    return BookDirectory(path=folder, files=tuple(files))


def set_probe(monkeypatch: pytest.MonkeyPatch, bit_rate: int) -> None:
    """Make current validation receive typed probe facts."""
    monkeypatch.setattr(
        validate,
        "probe",
        lambda path, **_: ProbeResult(
            path=path,
            duration_ms=60_000,
            stream=AudioStream(
                codec="mp3", sample_rate=44_100, channels=2, bit_rate=bit_rate
            ),
        ),
    )


def test_validation_writes_natural_order_handoff_and_caps_bitrate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = source_book(tmp_path, "chapter10.mp3", "chapter2.mp3", "chapter1.mp3")
    set_probe(monkeypatch, 256_000)

    result = validate_book(
        book,
        PathSettings(work_dir=tmp_path / "work", library_dir=tmp_path / "library"),
        EncodingSettings(max_bitrate=128),
        "book-hash",
    )

    assert [audio.path.name for audio in result.book.files] == [
        "chapter1.mp3",
        "chapter2.mp3",
        "chapter10.mp3",
    ]
    assert result.target_bitrate_kbps == 128
    assert result.file_list.read_text().splitlines() == [
        str(audio.path) for audio in result.book.files
    ]


def test_missing_source_refuses_before_writing_a_handoff(tmp_path: Path) -> None:
    missing = BookDirectory(
        path=tmp_path / "missing",
        files=(AudioFile(path=tmp_path / "missing" / "one.mp3", duration_ms=60_000),),
    )
    paths = PathSettings(work_dir=tmp_path / "work", library_dir=tmp_path / "library")

    with pytest.raises(FileNotFoundError):
        validate_book(missing, paths, EncodingSettings(), "book-hash")

    assert not (paths.work_dir / "book-hash" / "audio_files.txt").exists()


def test_separate_whole_books_are_rejected_before_concat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = source_book(tmp_path, "one.m4b", "two.m4b", duration_ms=8_000_000)
    set_probe(monkeypatch, 128_000)

    with pytest.raises(ValueError, match="separate books"):
        validate_book(
            book,
            PathSettings(work_dir=tmp_path / "work", library_dir=tmp_path / "library"),
            EncodingSettings(),
            "book-hash",
        )


def test_changed_handoff_cannot_be_reused_for_concat(tmp_path: Path) -> None:
    book = source_book(tmp_path, "one.mp3", "two.mp3")
    paths = PathSettings(work_dir=tmp_path / "work", library_dir=tmp_path / "library")
    handoff = paths.work_dir / "book-hash" / "audio_files.txt"
    handoff.parent.mkdir(parents=True)
    handoff.write_text(f"{book.files[0].path}\n{tmp_path / 'other.mp3'}\n")

    with pytest.raises(ValidationHandoffError):
        load_validated_book(book, paths, "book-hash")
