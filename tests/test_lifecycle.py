"""Tests for the validate, archive, and cleanup lifecycle boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.config import EncodingSettings, PathSettings
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.media import AudioStream, ProbeResult
from audiobook_pipeline.services import archive, cleanup, validate


def _book(root: Path, *names: str) -> BookDirectory:
    """Create a readable source book with deliberately unsorted track names."""
    source = root / "source" / "Example Book"
    source.mkdir(parents=True)
    files = []
    for name in names:
        path = source / name
        path.write_bytes(b"audio")
        files.append(AudioFile(path=path, duration_ms=60_000))
    return BookDirectory(path=source, files=tuple(files))


def _probe(path: Path) -> ProbeResult:
    """Return one valid live probe result for a fake audio fixture."""
    return ProbeResult(
        path=path,
        duration_ms=60_000,
        stream=AudioStream(
            codec="mp3", sample_rate=44_100, channels=1, bit_rate=64_000
        ),
    )


def test_validate_refreshes_audio_and_writes_ordered_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The next stage consumes a durable, naturally ordered list of validated files."""
    book = _book(tmp_path, "track10.mp3", "track2.mp3")
    paths = PathSettings(work_dir=tmp_path / "work")
    monkeypatch.setattr(validate, "probe", _probe)

    result = validate.validate_book(book, paths, EncodingSettings(), "book-hash")

    assert [audio.path.name for audio in result.book.files] == [
        "track2.mp3",
        "track10.mp3",
    ]
    assert result.target_bitrate_kbps == 64
    assert result.file_list.read_text().splitlines() == [
        str(book.path / "track2.mp3"),
        str(book.path / "track10.mp3"),
    ]


def test_validate_refuses_missing_input_without_writing_handoff(tmp_path: Path) -> None:
    """A stale discovery result cannot be turned into a completed stage."""
    missing = tmp_path / "source" / "missing.mp3"
    missing.parent.mkdir()
    book = BookDirectory(
        path=missing.parent,
        files=(AudioFile(path=missing, duration_ms=60_000),),
    )
    paths = PathSettings(work_dir=tmp_path / "work")

    with pytest.raises(FileNotFoundError, match=r"missing\.mp3"):
        validate.validate_book(book, paths, EncodingSettings(), "book-hash")

    assert not (paths.work_dir / "book-hash" / "audio_files.txt").exists()


def test_load_validated_book_preserves_the_completed_natural_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry after validation must concatenate the validated chapter order."""
    book = _book(tmp_path, "track10.mp3", "track2.mp3")
    paths = PathSettings(work_dir=tmp_path / "work")
    monkeypatch.setattr(validate, "probe", _probe)
    validate.validate_book(book, paths, EncodingSettings(), "book-hash")

    recovered = validate.load_validated_book(book, paths, "book-hash")

    assert [audio.path.name for audio in recovered.files] == [
        "track2.mp3",
        "track10.mp3",
    ]


def test_archive_requires_valid_output_then_moves_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Original audio moves only after the finished M4B passes its gate."""
    source = tmp_path / "source" / "Example Book"
    source.mkdir(parents=True)
    original = source / "chapter.mp3"
    original.write_bytes(b"original")
    final = tmp_path / "library" / "Example Book.m4b"
    final.parent.mkdir()
    final.write_bytes(b"m4b")
    monkeypatch.setattr(archive, "validate_m4b", lambda *_args, **_kwargs: None)

    result = archive.archive_source(
        source, final, tmp_path / "archive", source_duration_ms=60_000
    )

    assert result.archive_path == tmp_path / "archive" / "Example Book"
    assert (result.archive_path / "chapter.mp3").read_bytes() == b"original"
    assert not source.exists()


def test_archive_refusal_preserves_originals(tmp_path: Path) -> None:
    """A bad or missing final M4B is never permission to move source files."""
    source = tmp_path / "source" / "Example Book"
    source.mkdir(parents=True)
    original = source / "chapter.mp3"
    original.write_bytes(b"original")

    with pytest.raises(FileNotFoundError, match="finished M4B"):
        archive.archive_source(
            source,
            tmp_path / "missing.m4b",
            tmp_path / "archive",
            source_duration_ms=60_000,
        )

    assert original.exists()
    assert not (tmp_path / "archive").exists()


def test_finished_gate_rejects_a_renamed_mp3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suffix alone is never enough to authorize moving the originals."""
    final = tmp_path / "renamed.m4b"
    final.write_bytes(b"x" * 1_000)
    monkeypatch.setattr(
        archive,
        "probe",
        lambda path: ProbeResult(
            path=path,
            duration_ms=8_000,
            stream=AudioStream(
                codec="mp3", sample_rate=44_100, channels=1, bit_rate=1_000
            ),
            format_name="mp3",
        ),
    )

    with pytest.raises(archive.FinishedM4BError):
        archive.validate_m4b(final, source_duration_ms=8_000)


def test_finished_gate_accepts_plausible_aac_mp4_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate checks codec, container, duration, and size together."""
    final = tmp_path / "finished.m4b"
    final.write_bytes(b"x" * 1_000)
    monkeypatch.setattr(
        archive,
        "probe",
        lambda path: ProbeResult(
            path=path,
            duration_ms=8_000,
            stream=AudioStream(
                codec="aac", sample_rate=44_100, channels=1, bit_rate=1_000
            ),
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
        ),
    )

    archive.validate_m4b(final, source_duration_ms=8_000)


def test_archive_refuses_a_source_already_under_its_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Archiving can never move an archive subtree into itself."""
    archive_root = tmp_path / "archive"
    source = archive_root / "Example Book"
    source.mkdir(parents=True)
    original = source / "chapter.mp3"
    original.write_bytes(b"original")
    final = tmp_path / "library" / "Example Book.m4b"
    final.parent.mkdir()
    final.write_bytes(b"m4b")
    monkeypatch.setattr(archive, "validate_m4b", lambda *_args, **_kwargs: None)

    with pytest.raises(archive.ArchiveError):
        archive.archive_source(source, final, archive_root, source_duration_ms=60_000)

    assert original.exists()


@pytest.mark.parametrize(
    ("enabled", "dry_run", "removed"),
    [(False, False, False), (True, True, False), (True, False, True)],
)
def test_cleanup_stays_inside_book_work_directory(
    tmp_path: Path, enabled: bool, dry_run: bool, removed: bool
) -> None:
    """Cleanup removes only the book scratch tree and honours its safety flags."""
    root = tmp_path / "work"
    target = root / "book-hash"
    target.mkdir(parents=True)
    (target / "converted.m4b").write_bytes(b"scratch")
    sibling = root / "other-book"
    sibling.mkdir()
    (sibling / "keep.txt").write_text("keep")

    result = cleanup.cleanup_work_dir(
        root, "book-hash", enabled=enabled, dry_run=dry_run
    )

    assert result.removed is removed
    assert result.attempted is (enabled and not dry_run)
    assert target.exists() is (not removed)
    assert (sibling / "keep.txt").read_text() == "keep"


def test_cleanup_refuses_a_target_outside_its_configured_root(tmp_path: Path) -> None:
    """A malformed book hash cannot turn cleanup into a parent-directory delete."""
    work_root = tmp_path / "work"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("keep")

    with pytest.raises(cleanup.CleanupError):
        cleanup.cleanup_work_dir(work_root, "../outside", enabled=True, dry_run=False)

    assert sentinel.read_text() == "keep"
