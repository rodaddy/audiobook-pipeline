"""Legacy canonical-author guards on the durable index API."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.index import FolderIdentity, IndexOptions
from audiobook_pipeline.services.index import (
    LibraryIndex,
    SQLiteIndexStore,
    sqlite_connection_factory,
)


def index(tmp_path: Path) -> LibraryIndex:
    """Build one isolated durable index."""
    root = tmp_path / "library"
    options = IndexOptions()
    store = SQLiteIndexStore(
        sqlite_connection_factory(tmp_path / "index.db", options), options
    )
    return LibraryIndex(root, store)


@pytest.mark.parametrize(
    ("existing", "requested"),
    [
        ("J.R.R. Tolkien", "J. R. R. Tolkien"),
        ("R.A. Salvatore", "R. A. Salvatore"),
        ("George R. R. Martin", "George R.R. Martin"),
        ("J.K. Rowling", "J. K. Rowling"),
        ("James S.A. Corey", "James S. A. Corey"),
        ("Richard K. Morgan", "Richard Morgan"),
        ("Paul B. Thompson", "Paul Thompson"),
    ],
)
def test_canonical_author_spellings_reuse_one_folder(
    tmp_path: Path, existing: str, requested: str
) -> None:
    """Initial spacing and optional middle names must not split an author shelf."""
    library = index(tmp_path)
    library.register_folder(FolderIdentity(parent=tmp_path / "library", name=existing))
    assert library.match_author(requested) == existing


@pytest.mark.parametrize(
    ("existing", "requested"),
    [
        ("Tad Williams", "Michael Williams"),
        ("Tonya C. Cook", "Glen Cook"),
        ("Ken Liu", "Cixin Liu"),
        ("Robert Sanderson", "Brandon Sanderson"),
        ("Mark Williams", "Michael Williams"),
    ],
)
def test_shared_surname_or_initial_never_canonicalizes(
    tmp_path: Path, existing: str, requested: str
) -> None:
    """A surname alone is not identity; avoid filing one author under another."""
    library = index(tmp_path)
    library.register_folder(FolderIdentity(parent=tmp_path / "library", name=existing))
    assert library.match_author(requested) == requested


@pytest.mark.parametrize("requested", ["Tolkien", "Williams"])
def test_bare_surname_does_not_match_a_full_author_name(
    tmp_path: Path, requested: str
) -> None:
    """The public canonicalizer requires compatible given names."""
    library = index(tmp_path)
    library.register_folder(
        FolderIdentity(parent=tmp_path / "library", name="J. R. R. Tolkien")
    )
    assert library.match_author(requested) == requested
