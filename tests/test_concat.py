"""Tests for joining files and building the chapter table."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.services import concat
from audiobook_pipeline.services.concat import (
    SOURCE_EMBEDDED,
    SOURCE_FILES,
    _title_from_filename,
    build_chapters,
    concat_files,
    write_concat_list,
)
from audiobook_pipeline.utils.ffmpeg import FfmpegError

MINUTE_MS = 60 * 1000


def book_of(tmp_path: Path, *names: str, minutes: int = 45) -> BookDirectory:
    """A multi-file book whose files each run `minutes` long."""
    folder = tmp_path / "Book"
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for name in names:
        path = folder / name
        path.write_bytes(b"audio")
        files.append(AudioFile(path=path, duration_ms=minutes * MINUTE_MS))
    return BookDirectory(path=folder, files=tuple(files))


@pytest.fixture
def embedded(monkeypatch: pytest.MonkeyPatch) -> dict[str, ChapterSet]:
    """Control what chapters each source file reports as embedded."""
    table: dict[str, ChapterSet] = {}

    def fake_probe(path: Path, **_: object) -> object:
        if path.name == "unreadable.mp3":
            raise FfmpegError("ffprobe", f"cannot read {path}")
        return type("Probed", (), {"chapters": table.get(path.name, ChapterSet())})()

    monkeypatch.setattr(concat, "probe", fake_probe)
    return table


# ---------------------------------------------------------------------------
# titles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("01 - The Long Road.mp3", "The Long Road"),
        ("01. The Long Road.mp3", "The Long Road"),
        ("01_The_Long_Road.mp3", "The Long Road"),
        ("Track 03 - Homeland.mp3", "Homeland"),
        ("Chapter 12 - The End.mp3", "The End"),
        ("The Long Road.mp3", "The Long Road"),
    ],
)
def test_leading_index_is_stripped_from_titles(filename: str, expected: str) -> None:
    assert _title_from_filename(Path(filename)) == expected


def test_bare_number_filename_keeps_its_number(tmp_path: Path) -> None:
    """Stripping would leave nothing, and a blank chapter is worse."""
    assert _title_from_filename(Path("01.mp3")) == "01"


def test_title_is_sanitized(tmp_path: Path) -> None:
    assert _title_from_filename(Path("01 - Cause: Effect.mp3")) == "Cause Effect"


# ---------------------------------------------------------------------------
# embedded chapters win -- defect 2
# ---------------------------------------------------------------------------


def test_embedded_chapters_are_preserved_not_replaced(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    """Defect 2: 47 real chapter names became 3 called Part 1/2/3."""
    book = book_of(tmp_path, "part1.mp3", "part2.mp3", minutes=45)
    for name in ("part1.mp3", "part2.mp3"):
        embedded[name] = ChapterSet(
            chapters=tuple(
                Chapter(
                    start_ms=i * 10 * MINUTE_MS,
                    end_ms=(i + 1) * 10 * MINUTE_MS,
                    title=f"{name} chapter {i}",
                )
                for i in range(4)
            )
        )

    result = build_chapters(book)

    assert result.source == SOURCE_EMBEDDED
    assert len(result.chapters) == 8
    assert result.chapters[0].title == "part1.mp3 chapter 0"


def test_embedded_offsets_are_shifted_by_preceding_durations(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    book = book_of(tmp_path, "part1.mp3", "part2.mp3", minutes=45)
    # TWO marks per file, so the offsets cannot be reproduced by falling back
    # to file boundaries -- which would give one mark per file at 0 and 45m.
    for name in ("part1.mp3", "part2.mp3"):
        embedded[name] = ChapterSet(
            chapters=(
                Chapter(start_ms=0, end_ms=20 * MINUTE_MS, title=f"{name} a"),
                Chapter(
                    start_ms=20 * MINUTE_MS, end_ms=45 * MINUTE_MS, title=f"{name} b"
                ),
            )
        )

    result = build_chapters(book)

    assert result.source == SOURCE_EMBEDDED
    assert [c.start_ms for c in result.chapters] == [
        0,
        20 * MINUTE_MS,
        45 * MINUTE_MS,
        65 * MINUTE_MS,
    ]
    assert result.chapters[-1].end_ms == 90 * MINUTE_MS


def test_partial_embedded_marks_fall_back_to_file_boundaries(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    """Marks in SOME files would interleave two numbering schemes."""
    book = book_of(tmp_path, "part1.mp3", "part2.mp3")
    embedded["part1.mp3"] = ChapterSet(
        chapters=(Chapter(start_ms=0, end_ms=1000, title="Real Chapter"),)
    )

    result = build_chapters(book)

    assert result.source == SOURCE_FILES
    assert len(result.chapters) == 2


def test_unreadable_source_falls_back_to_file_boundaries(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    book = book_of(tmp_path, "part1.mp3", "unreadable.mp3")
    result = build_chapters(book)
    assert result.source == SOURCE_FILES


# ---------------------------------------------------------------------------
# file-boundary chapters
# ---------------------------------------------------------------------------


def test_one_chapter_per_file_titled_from_the_filename(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    book = book_of(tmp_path, "01 - Homeland.mp3", "02 - Exile.mp3", minutes=45)

    result = build_chapters(book)

    assert result.source == SOURCE_FILES
    assert [c.title for c in result.chapters] == ["Homeland", "Exile"]


def test_boundary_offsets_are_contiguous(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    book = book_of(tmp_path, "a.mp3", "b.mp3", "c.mp3", minutes=45)

    chapters = build_chapters(book).chapters

    assert [c.start_ms for c in chapters] == [0, 45 * MINUTE_MS, 90 * MINUTE_MS]
    assert chapters[-1].end_ms == 135 * MINUTE_MS


def test_chapter_table_is_never_empty_for_a_multi_file_book(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    book = book_of(tmp_path, "a.mp3", "b.mp3")
    assert not build_chapters(book).is_empty


# ---------------------------------------------------------------------------
# the concat list
# ---------------------------------------------------------------------------


def test_concat_list_quotes_apostrophes(tmp_path: Path) -> None:
    """ "Foo's Story" is common enough to be a real failure, not a contrived one."""
    book = book_of(tmp_path, "Foo's Story.mp3")

    written = write_concat_list(book.files, tmp_path / "work")

    assert "Foo''s Story.mp3" in written.read_text(encoding="utf-8")


def test_concat_list_preserves_play_order(tmp_path: Path) -> None:
    book = book_of(tmp_path, "01.mp3", "02.mp3", "03.mp3")

    lines = write_concat_list(book.files, tmp_path / "work").read_text().splitlines()

    assert [Path(line.split("'")[1]).name for line in lines] == [
        "01.mp3",
        "02.mp3",
        "03.mp3",
    ]


# ---------------------------------------------------------------------------
# the join itself
# ---------------------------------------------------------------------------


def test_concat_copies_streams_rather_than_re_encoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-encoding here costs hours and a generation of quality for nothing."""
    captured: list[list[str]] = []

    def record(args: list[str], **_: object) -> str:
        captured.append(args)
        return ""

    monkeypatch.setattr(concat, "run_ffmpeg", record)

    book = book_of(tmp_path, "a.mp3", "b.mp3")
    concat_files(book, tmp_path / "out" / "joined.m4b")

    assert captured
    assert "-c" in captured[0]
    assert captured[0][captured[0].index("-c") + 1] == "copy"


def test_concat_allows_paths_outside_the_list_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without -safe 0 the demuxer refuses the absolute paths we write."""
    captured: list[list[str]] = []

    def record(args: list[str], **_: object) -> str:
        captured.append(args)
        return ""

    monkeypatch.setattr(concat, "run_ffmpeg", record)

    book = book_of(tmp_path, "a.mp3", "b.mp3")
    concat_files(book, tmp_path / "out" / "joined.m4b")

    assert captured[0][captured[0].index("-safe") + 1] == "0"


def test_concat_returns_the_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(concat, "run_ffmpeg", lambda args, **_: "")
    book = book_of(tmp_path, "a.mp3", "b.mp3")
    output = tmp_path / "out" / "joined.m4b"

    assert concat_files(book, output) == output


# ---------------------------------------------------------------------------
# uninformative filenames -- every case below came off the real source tree
# ---------------------------------------------------------------------------


def test_trailing_part_marker_is_stripped() -> None:
    assert _title_from_filename(Path("The Autumn Republic Part 01 of 19.mp3")) == (
        "The Autumn Republic"
    )
    assert _title_from_filename(Path("Forsworn (1 of 5).mp3")) == "Forsworn"
    assert _title_from_filename(Path("Promise of Blood01-19.mp3")) == (
        "Promise of Blood"
    )


def test_production_note_is_stripped() -> None:
    assert _title_from_filename(Path("The Autumn Republic (Unabridged).mp3")) == (
        "The Autumn Republic"
    )


def test_split_by_size_source_gets_numbered_chapters(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    """19 files all named after the book carry no per-chapter information."""
    names = [f"Promise of Blood Part {n:02d} of 19.mp3" for n in range(1, 20)]
    book = book_of(tmp_path, *names)

    titles = [c.title for c in build_chapters(book).chapters]

    assert titles[:3] == ["Chapter 1", "Chapter 2", "Chapter 3"]
    assert len(set(titles)) == 19


def test_surviving_disc_number_does_not_count_as_a_name(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    """ "The Crimson Campaign 01/02/03" is unique but says nothing."""
    names = [f"The Crimson Campaign {n:02d} Part 1 of 7.mp3" for n in range(1, 8)]
    book = book_of(tmp_path, *names)

    assert next(c.title for c in build_chapters(book).chapters) == "Chapter 1"


def test_mostly_repeated_titles_are_numbered(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    """ "Intro" plus seven identical "Forsworn" is not a chapter list."""
    names = [
        "00 - Intro.mp3",
        *[f"{n:02d} - Forsworn ({n} of 7).mp3" for n in range(1, 8)],
    ]
    book = book_of(tmp_path, *names)

    assert next(c.title for c in build_chapters(book).chapters) == "Chapter 1"


def test_real_chapter_names_are_still_used(
    tmp_path: Path, embedded: dict[str, ChapterSet]
) -> None:
    """The numbering fallback must not eat genuinely-named chapters."""
    book = book_of(
        tmp_path,
        "01 - Homeland.mp3",
        "02 - Exile.mp3",
        "03 - Sojourn.mp3",
        "04 - The Crystal Shard.mp3",
    )

    assert [c.title for c in build_chapters(book).chapters] == [
        "Homeland",
        "Exile",
        "Sojourn",
        "The Crystal Shard",
    ]
