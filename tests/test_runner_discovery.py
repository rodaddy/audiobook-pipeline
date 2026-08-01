"""Tests for runner._find_book_directories -- which dirs count as one book.

This function had NO test coverage, which is how a collection root came to be
claimed as a single book. Measured 2026-08-01 against
CleanDesktop/tFiles/Done: one loose "$100M Offers.mp3" at the top of a
collection of six author folders reduced discovery from 11 book directories to
1, and the pipeline planned to concatenate all 76 loose files into a single
139-hour M4B named after the containing folder.
"""

from __future__ import annotations

from pathlib import Path

from audiobook_pipeline.models import CONVERTIBLE_EXTENSIONS
from audiobook_pipeline.runner import _find_book_directories


def _tree(root: Path, paths: list[str]) -> None:
    """Create empty files at each relative path under root."""
    for rel in paths:
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(b"\x00")


class TestCollectionRootIsNotABook:
    """A directory with audio AND audio-bearing subdirs is a collection."""

    def test_loose_file_at_root_does_not_swallow_the_tree(self, tmp_path):
        """The exact production failure, minimised."""
        _tree(
            tmp_path,
            [
                "Loose Extra.mp3",
                "Author A/Book One/01 - Chapter.mp3",
                "Author A/Book Two/01 - Chapter.mp3",
                "Author B/Book Three/01 - Chapter.mp3",
            ],
        )
        found = _find_book_directories(tmp_path, CONVERTIBLE_EXTENSIONS)
        names = sorted(p.relative_to(tmp_path).as_posix() for p in found)
        assert names == [
            "Author A/Book One",
            "Author A/Book Two",
            "Author B/Book Three",
        ]

    def test_clean_collection_still_finds_every_book(self, tmp_path):
        """No loose file: behaviour must be unchanged."""
        _tree(
            tmp_path,
            [
                "Author A/Book One/01 - Chapter.mp3",
                "Author B/Book Two/01 - Chapter.mp3",
            ],
        )
        found = _find_book_directories(tmp_path, CONVERTIBLE_EXTENSIONS)
        assert len(found) == 2


class TestMultiDiscStaysOneBook:
    """A book split across CD1/CD2 is ONE book, even with a file alongside."""

    def test_bare_disc_subdirs_are_split__known_gap(self, tmp_path):
        """DOCUMENTS A BUG, does not endorse it.

        The docstring on _find_book_directories claims "multi-disc structures
        like CD1/CD2 are treated as one book". They are not, when the book root
        holds no audio of its own: os.walk finds nothing at Book/, descends, and
        claims CD1 and CD2 as two separate books.

        Verified 2026-08-01 against the ORIGINAL implementation from origin/main
        as well as the current one -- this predates the collection-root fix and
        is unchanged by it. Pinned here so the behaviour cannot drift unnoticed
        and so the next person sees it is known rather than rediscovering it.
        Fixing it needs the disc-folder rule applied at descent time, which is a
        larger change than the collection-root defect this file was added for.
        """
        _tree(
            tmp_path,
            [
                "Book/CD1/01 - Chapter.mp3",
                "Book/CD2/01 - Chapter.mp3",
            ],
        )
        found = _find_book_directories(tmp_path / "Book", CONVERTIBLE_EXTENSIONS)
        assert [p.name for p in found] == ["CD1", "CD2"]

    def test_audio_beside_disc_dirs_keeps_one_book(self, tmp_path):
        """Audio in the book root AND in CD folders is still one book.

        This is the case the collection check must NOT break: the subdirs are
        parts of this book, not separate books.
        """
        _tree(
            tmp_path,
            [
                "Book/Intro.mp3",
                "Book/CD1/01 - Chapter.mp3",
                "Book/CD2/01 - Chapter.mp3",
            ],
        )
        found = _find_book_directories(tmp_path / "Book", CONVERTIBLE_EXTENSIONS)
        assert [p.name for p in found] == ["Book"]

    def test_disc_naming_variants(self, tmp_path):
        """'Disc 2', 'Part_3', 'Vol 1' are all book parts, not books."""
        for variant in ("Disc 2", "Part_3", "Vol 1", "disk4"):
            book = tmp_path / variant.replace(" ", "") / "Book"
            _tree(
                book.parent,
                ["Book/Intro.mp3", f"Book/{variant}/01 - Chapter.mp3"],
            )
            found = _find_book_directories(book, CONVERTIBLE_EXTENSIONS)
            assert [p.name for p in found] == ["Book"], variant


class TestSingleBookDirectory:
    """Pointing at one book directly still works."""

    def test_flat_book_directory(self, tmp_path):
        _tree(tmp_path, ["Book/01 - Chapter.mp3", "Book/02 - Chapter.mp3"])
        found = _find_book_directories(tmp_path / "Book", CONVERTIBLE_EXTENSIONS)
        assert [p.name for p in found] == ["Book"]

    def test_already_converted_m4b_is_not_convertible(self, tmp_path):
        """A single finished .m4b has nothing to convert."""
        _tree(tmp_path, ["Book/Book.m4b"])
        found = _find_book_directories(tmp_path / "Book", CONVERTIBLE_EXTENSIONS)
        assert found == []
