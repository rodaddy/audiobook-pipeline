"""Tests for source-tree discovery, including defects 1, 3 and 4."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.book import SOURCE_EXTENSIONS
from audiobook_pipeline.services import discovery
from audiobook_pipeline.services.discovery import (
    discover_books,
    is_source_audio,
    walk_candidates,
)
from audiobook_pipeline.utils.ffmpeg import FfmpegError

HOUR_MS = 60 * 60 * 1000

#: Durations keyed by filename, so a fixture tree can say "this file is 10
#: hours" without producing 10 hours of audio.
DurationMap = dict[str, int]


@pytest.fixture
def durations(monkeypatch: pytest.MonkeyPatch) -> DurationMap:
    """Replace ffprobe with a lookup keyed on filename.

    Discovery's job is classification, not decoding. Probing real audio here
    would test ffmpeg and make the durations that drive every branch impossible
    to set precisely.
    """
    table: DurationMap = {}

    def fake_probe(path: Path, **_: object) -> object:
        if path.name not in table:
            raise FfmpegError("ffprobe", f"no readable duration for {path}")
        return type("Probed", (), {"duration_ms": table[path.name]})()

    monkeypatch.setattr(discovery, "probe", fake_probe)
    return table


def make_audio(
    directory: Path, name: str, durations: DurationMap, hours: float
) -> Path:
    """Create an audio file of a declared duration."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"fake audio")
    durations[name] = int(hours * HOUR_MS)
    return path


# ---------------------------------------------------------------------------
# what counts as audio -- defect 1
# ---------------------------------------------------------------------------


def test_every_source_extension_is_accepted(tmp_path: Path) -> None:
    """Defect 1: pre-filtering by extension made 76 source files invisible."""
    for suffix in SOURCE_EXTENSIONS:
        path = tmp_path / f"book{suffix}"
        path.write_bytes(b"x")
        assert is_source_audio(path), suffix


def test_extension_matching_is_case_insensitive(tmp_path: Path) -> None:
    path = tmp_path / "Book.MP3"
    path.write_bytes(b"x")
    assert is_source_audio(path)


def test_explicitly_excluded_simple_output_is_not_rediscovered(
    tmp_path: Path, durations: DurationMap
) -> None:
    """Only the recorded simple output is hidden; ordinary M4Bs stay inputs."""
    generated = make_audio(tmp_path / "Book", "Book.m4b", durations, hours=8)
    source = make_audio(tmp_path / "Book", "original.m4b", durations, hours=8)

    found = discover_books(tmp_path, excluded=frozenset({generated.resolve()}))

    assert [audio.path for book in found for audio in book.files] == [source]


def test_non_audio_and_hidden_files_are_not_audio(tmp_path: Path) -> None:
    for name in ("cover.jpg", "notes.txt", ".DS_Store", "._Book.mp3"):
        path = tmp_path / name
        path.write_bytes(b"x")
        assert not is_source_audio(path), name


def test_directories_are_not_audio(tmp_path: Path) -> None:
    folder = tmp_path / "Book.mp3"
    folder.mkdir()
    assert not is_source_audio(folder)


# ---------------------------------------------------------------------------
# defect 3 -- a loose file at the root must not prune the tree
# ---------------------------------------------------------------------------


def test_loose_file_at_root_does_not_prune_the_tree(
    tmp_path: Path, durations: DurationMap
) -> None:
    """Defect 3: this shape collapsed 11 book directories into 1."""
    make_audio(tmp_path, "Stray.mp3", durations, hours=8)
    for n in range(1, 12):
        make_audio(tmp_path / f"Book {n}", f"book{n}.mp3", durations, hours=9)

    found = discover_books(tmp_path)

    assert len(found) == 12
    assert (tmp_path / "Book 11") in {b.path for b in found}


def test_nested_collections_are_fully_descended(
    tmp_path: Path, durations: DurationMap
) -> None:
    deep = tmp_path / "Author" / "Series" / "Book 1"
    make_audio(deep, "part1.mp3", durations, hours=9)
    make_audio(tmp_path / "Author", "loose.mp3", durations, hours=7)

    assert {b.path for b in discover_books(tmp_path)} == {tmp_path / "Author", deep}


def test_hidden_directories_are_skipped(tmp_path: Path, durations: DurationMap) -> None:
    make_audio(tmp_path / ".Trashes", "junk.mp3", durations, hours=9)
    make_audio(tmp_path / "Book", "book.mp3", durations, hours=9)

    assert [b.path for b in discover_books(tmp_path)] == [tmp_path / "Book"]


# ---------------------------------------------------------------------------
# defect 4 -- separate books must not be concatenated
# ---------------------------------------------------------------------------


def test_folder_of_novels_yields_one_book_each(
    tmp_path: Path, durations: DurationMap
) -> None:
    """Defect 4: 40 novels in one folder became a single 510-hour concat."""
    folder = tmp_path / "Collection"
    for n in range(40):
        make_audio(folder, f"novel{n:02d}.m4b", durations, hours=12.75)

    found = discover_books(tmp_path)

    assert len(found) == 40
    assert all(len(b.files) == 1 for b in found)


def test_chaptered_folder_stays_one_book(
    tmp_path: Path, durations: DurationMap
) -> None:
    folder = tmp_path / "Chaptered Book"
    for n in range(1, 21):
        make_audio(folder, f"chapter{n:02d}.mp3", durations, hours=0.75)

    found = discover_books(tmp_path)

    assert len(found) == 1
    assert len(found[0].files) == 20
    assert found[0].is_multi_file_book


def test_one_long_file_among_chapters_does_not_flip_the_verdict(
    tmp_path: Path, durations: DurationMap
) -> None:
    """The median is what makes this hold; a mean would be dragged over."""
    folder = tmp_path / "Book"
    for n in range(1, 20):
        make_audio(folder, f"chapter{n:02d}.mp3", durations, hours=0.75)
    make_audio(folder, "chapter20.mp3", durations, hours=11)

    found = discover_books(tmp_path)
    assert len(found) == 1
    assert len(found[0].files) == 20


def test_short_credits_track_does_not_flip_the_verdict(
    tmp_path: Path, durations: DurationMap
) -> None:
    """A 3-minute outlier among real books must not make them one concat."""
    folder = tmp_path / "Collection"
    for n in range(1, 6):
        make_audio(folder, f"novel{n}.m4b", durations, hours=11)
    make_audio(folder, "credits.mp3", durations, hours=0.05)

    found = discover_books(tmp_path)
    assert len(found) == 6
    assert all(len(b.files) == 1 for b in found)


def test_single_file_is_never_a_concat_candidate(
    tmp_path: Path, durations: DurationMap
) -> None:
    make_audio(tmp_path / "Book", "solo.mp3", durations, hours=0.5)

    found = discover_books(tmp_path)
    assert len(found) == 1
    assert not found[0].is_multi_file_book


# ---------------------------------------------------------------------------
# unreadable audio
# ---------------------------------------------------------------------------


def test_unprobeable_file_is_skipped_not_fatal(
    tmp_path: Path, durations: DurationMap
) -> None:
    folder = tmp_path / "Book"
    make_audio(folder, "good.mp3", durations, hours=9)
    # Written, but deliberately absent from the duration table, so the fake
    # probe raises exactly as ffprobe does on a truncated file.
    (folder / "truncated.mp3").write_bytes(b"not audio")

    found = discover_books(tmp_path)

    assert len(found) == 1
    assert [f.path.name for f in found[0].files] == ["good.mp3"]


def test_directory_with_no_readable_audio_yields_nothing(
    tmp_path: Path, durations: DurationMap
) -> None:
    (tmp_path / "Book").mkdir()
    (tmp_path / "Book" / "broken.mp3").write_bytes(b"not audio")

    assert discover_books(tmp_path) == []


def test_missing_root_yields_nothing(tmp_path: Path, durations: DurationMap) -> None:
    assert list(walk_candidates(tmp_path / "nope")) == []


# ---------------------------------------------------------------------------
# ordering
# ---------------------------------------------------------------------------


def test_files_are_ordered_for_play_not_by_filesystem(
    tmp_path: Path, durations: DurationMap
) -> None:
    folder = tmp_path / "Book"
    for name in ("chapter03.mp3", "chapter01.mp3", "chapter02.mp3"):
        make_audio(folder, name, durations, hours=0.75)

    found = discover_books(tmp_path)

    assert [f.path.name for f in found[0].files] == [
        "chapter01.mp3",
        "chapter02.mp3",
        "chapter03.mp3",
    ]


def test_split_books_are_individually_identifiable(
    tmp_path: Path, durations: DurationMap
) -> None:
    """Observed on the real tree: 19 novels in one folder share a path."""
    folder = tmp_path / "Collection"
    for n in range(1, 20):
        make_audio(folder, f"novel{n:02d}.m4b", durations, hours=12)

    found = discover_books(tmp_path)

    assert len({b.path for b in found}) == 1
    assert len({b.identity_path for b in found}) == 19


def test_concat_book_is_identified_by_its_directory(
    tmp_path: Path, durations: DurationMap
) -> None:
    folder = tmp_path / "Chaptered"
    for n in range(1, 21):
        make_audio(folder, f"chapter{n:02d}.mp3", durations, hours=0.75)

    assert discover_books(tmp_path)[0].identity_path == folder
