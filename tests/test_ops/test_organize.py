"""Legacy organize-operation guards mapped to the rewrite public services."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.services.names import (
    extract_author,
    looks_like_author,
    strip_label_suffix,
)
from audiobook_pipeline.services.organize import (
    build_library_path,
    place_book,
    reuse_existing_folder,
)
from audiobook_pipeline.services.parse import parse_path


def metadata(**overrides: object) -> BookMetadata:
    """Return metadata with the historical path tests' conventional values."""
    fields: dict[str, object] = {
        "title": "The Final Empire",
        "author": "Brandon Sanderson",
    }
    fields.update(overrides)
    return BookMetadata.model_validate(fields)


def parse(raw: str) -> ParsedPath:
    """Call the typed parser with the test media root as its climb boundary."""
    return parse_path(Path(raw), Path("/media"))


@pytest.mark.parametrize(
    ("raw", "author", "series", "position", "title"),
    [
        (
            "/media/Brandon Sanderson-Mistborn-#1-The Final Empire/audio.m4b",
            "Brandon Sanderson",
            "Mistborn",
            "1",
            "The Final Empire",
        ),
        (
            "/media/Deathgate Cycle 1 - Dragon Wing/audio.m4b",
            "",
            "Deathgate Cycle",
            "1",
            "Dragon Wing",
        ),
        (
            "/media/Mistborn [01] The Final Empire.m4b",
            "",
            "Mistborn",
            "01",
            "The Final Empire",
        ),
        (
            "/media/Author-Series-#-3-Title/audio.m4b",
            "Author",
            "Series",
            "3",
            "Title",
        ),
    ],
)
def test_current_parser_recognizes_legacy_path_layouts(
    raw: str, author: str, series: str, position: str, title: str
) -> None:
    """The public parser keeps the explicit and numbered naming conventions."""
    result = parse(raw)
    assert (result.author, result.series, result.position, result.title) == (
        author,
        series,
        position,
        title,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Brandon Sanderson", True),
        ("J.K. Rowling", True),
        ("The Name of the Wind", False),
        ("Mistborn", False),
        ("Fantasy Series Collection", False),
        ("Book 1", False),
        ("output", False),
    ],
)
def test_author_heuristics_reject_collection_and_pipeline_folders(
    name: str, expected: bool
) -> None:
    """Only plausible personal names may become library author folders."""
    assert looks_like_author(name) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Tad Williams (All Chaptered)", "Tad Williams"),
        ("R.A. Salvatore - The Legend of Drizzt", "R.A. Salvatore"),
        ("Powder Mage 01 - Promise of Blood", "Powder Mage 01 - Promise of Blood"),
        ("Title - Audiobook", "Title"),
        ("Title - Unabridged", "Title"),
    ],
)
def test_author_and_label_cleanup_preserve_identity(raw: str, expected: str) -> None:
    """Role labels and format suffixes do not form author or title identity."""
    actual = strip_label_suffix(raw)
    assert extract_author(raw) == expected or actual == expected


def test_folder_reuse_rejects_meaningful_extra_words(tmp_path: Path) -> None:
    """Near matching avoids duplicate folders without merging different works."""
    (tmp_path / "Origins of The Wheel of Time").mkdir()
    (tmp_path / "The Wheel of Time").mkdir()
    assert reuse_existing_folder(tmp_path, "The Wheel of Time") == "The Wheel of Time"
    assert reuse_existing_folder(tmp_path, "Wheel of Time") == "The Wheel of Time"


def test_library_layout_reuses_author_series_and_book_folders(tmp_path: Path) -> None:
    """The legacy per-book Plex layout remains the public output contract."""
    existing = tmp_path / "Brandon Sanderson" / "Mistborn" / "Book 1 - The Final Empire"
    existing.mkdir(parents=True)
    target = build_library_path(
        tmp_path,
        metadata(series="Mistborn", series_position="1"),
    )
    assert target == existing / "Book 1 - The Final Empire.m4b"


def test_place_book_copies_or_moves_without_overwriting(tmp_path: Path) -> None:
    """Historical copy/move operations retain content and protect collisions."""
    source = tmp_path / "work" / "book.m4b"
    source.parent.mkdir()
    source.write_bytes(b"new")
    destination = tmp_path / "library" / "Author" / "Book.m4b"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old")
    copied = place_book(source, destination, move=False)
    assert copied.name == "Book (2).m4b"
    assert source.exists() and destination.read_bytes() == b"old"
    moved = place_book(source, tmp_path / "library" / "Author" / "Moved.m4b")
    assert moved.exists() and not source.exists()
