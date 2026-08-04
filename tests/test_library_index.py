"""Legacy library-index behavior guards on the durable index API."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.index import (
    DestinationIdentity,
    IndexedFile,
    IndexOptions,
    SourceIdentity,
)
from audiobook_pipeline.services.index import (
    LibraryIndex,
    SQLiteIndexStore,
    sqlite_connection_factory,
)


def index(tmp_path: Path) -> LibraryIndex:
    """Build one index with a disposable durable store."""
    root = tmp_path / "library"
    options = IndexOptions()
    store = SQLiteIndexStore(
        sqlite_connection_factory(tmp_path / "index.db", options), options
    )
    return LibraryIndex(root, store)


@pytest.fixture
def library_tree(tmp_path: Path) -> Path:
    """Create the historical author/series/library layout."""
    library = tmp_path / "library"
    for relative in (
        "Brandon Sanderson/Mistborn/The Final Empire/book.m4b",
        "Brandon Sanderson/Mistborn/The Well of Ascension/book.m4b",
        "Stephen King/The Shining/shining.m4b",
        "_unsorted/Random Book/random.m4b",
    ):
        path = library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
    return library


def test_scan_preserves_historical_folder_and_file_counts(library_tree: Path) -> None:
    """A populated library remains searchable without changing its contents."""
    library = index(library_tree.parent)
    library.scan()
    assert (library.folder_count, library.file_count) == (8, 4)


@pytest.mark.parametrize(
    ("desired", "expected"),
    [
        ("Brandon Sanderson", "Brandon Sanderson"),
        ("Food- A Love Story", "Food A Love Story"),
        ("Title", "Title (2014)"),
        ("New Author Name", "New Author Name"),
    ],
)
def test_folder_reuse_keeps_existing_spelling(
    tmp_path: Path, desired: str, expected: str
) -> None:
    """Exact, punctuation, and year variants reuse siblings; new names remain new."""
    root = tmp_path / "library"
    root.mkdir()
    for name in {"Brandon Sanderson", "Food A Love Story", "Title (2014)"}:
        (root / name).mkdir()
    library = index(tmp_path)
    library.scan()
    assert library.reuse_existing(root, desired) == expected


def test_unknown_parent_and_nested_series_are_safe(library_tree: Path) -> None:
    """Only registered direct children are reused."""
    library = index(library_tree.parent)
    library.scan()
    assert library.reuse_existing(library_tree / "Unknown", "Book") == "Book"
    assert (
        library.reuse_existing(library_tree / "Brandon Sanderson", "Mistborn")
        == "Mistborn"
    )


def test_file_registration_and_source_dedup_are_independent(tmp_path: Path) -> None:
    """A durable destination and an in-run source claim have distinct identities."""
    library = index(tmp_path)
    destination = DestinationIdentity(
        directory=tmp_path / "library" / "Author" / "Book", filename="book.m4b"
    )
    library.register_destination(IndexedFile(destination=destination))
    assert library.file_exists(destination)
    assert not library.mark_processed(SourceIdentity(stem="new book"))
    assert library.mark_processed(SourceIdentity(stem="new book"))
    assert not library.mark_processed(SourceIdentity(stem="another book"))


def test_correct_placement_uses_the_actual_filesystem_object(tmp_path: Path) -> None:
    """A missing or different destination must not be mistaken for a completed move."""
    library = index(tmp_path)
    source = tmp_path / "library" / "Author" / "Book" / "book.m4b"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    same = DestinationIdentity(directory=source.parent, filename=source.name)
    different = DestinationIdentity(
        directory=tmp_path / "library" / "Other", filename=source.name
    )
    assert library.is_correctly_placed(source, same)
    assert not library.is_correctly_placed(source, different)
