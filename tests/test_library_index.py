"""Tests for library_index.py -- in-memory library index for batch operations."""

from pathlib import Path

import pytest

from audiobook_pipeline.library_index import LibraryIndex


@pytest.fixture
def library_tree(tmp_path):
    """Create a realistic library directory tree for testing."""
    lib = tmp_path / "library"
    lib.mkdir()

    # Author with series
    sanderson = lib / "Brandon Sanderson" / "Mistborn"
    sanderson.mkdir(parents=True)
    (sanderson / "The Final Empire").mkdir()
    (sanderson / "The Final Empire" / "book.m4b").write_text("audio")
    (sanderson / "The Well of Ascension").mkdir()
    (sanderson / "The Well of Ascension" / "book.m4b").write_text("audio")

    # Author without series
    king = lib / "Stephen King"
    king.mkdir()
    (king / "The Shining").mkdir()
    (king / "The Shining" / "shining.m4b").write_text("audio")

    # Unsorted
    unsorted = lib / "_unsorted"
    unsorted.mkdir()
    (unsorted / "Random Book").mkdir()
    (unsorted / "Random Book" / "random.m4b").write_text("audio")

    return lib


class TestLibraryIndexBuild:
    """Test index construction from directory tree."""

    def test_builds_from_populated_tree(self, library_tree):
        index = LibraryIndex(library_tree)
        assert index.folder_count > 0
        assert index.file_count > 0

    def test_handles_nonexistent_root(self, tmp_path):
        index = LibraryIndex(tmp_path / "nonexistent")
        assert index.folder_count == 0
        assert index.file_count == 0

    def test_handles_empty_root(self, tmp_path):
        lib = tmp_path / "empty"
        lib.mkdir()
        index = LibraryIndex(lib)
        assert index.folder_count == 0
        assert index.file_count == 0

    def test_counts_all_files(self, library_tree):
        index = LibraryIndex(library_tree)
        # 4 .m4b files: 2 Sanderson + 1 King + 1 unsorted
        assert index.file_count == 4

    def test_counts_all_folders(self, library_tree):
        """All subdirectories should be indexed."""
        index = LibraryIndex(library_tree)
        # Brandon Sanderson, Mistborn, The Final Empire, The Well of Ascension,
        # Stephen King, The Shining, _unsorted, Random Book = 8
        assert index.folder_count == 8


class TestReuseExisting:
    """Test O(1) folder name lookup."""

    def test_exact_match(self, library_tree):
        index = LibraryIndex(library_tree)
        result = index.reuse_existing(library_tree, "Brandon Sanderson")
        assert result == "Brandon Sanderson"

    def test_near_match_returns_existing(self, tmp_path):
        """Near-match normalized lookup returns existing folder name."""
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "Food A Love Story").mkdir()
        index = LibraryIndex(lib)
        result = index.reuse_existing(lib, "Food- A Love Story")
        assert result == "Food A Love Story"

    def test_near_match_with_year(self, tmp_path):
        """Year suffix ignored in comparison."""
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / "Title (2014)").mkdir()
        index = LibraryIndex(lib)
        result = index.reuse_existing(lib, "Title")
        assert result == "Title (2014)"

    def test_no_match_returns_desired(self, library_tree):
        index = LibraryIndex(library_tree)
        result = index.reuse_existing(library_tree, "New Author Name")
        assert result == "New Author Name"

    def test_unknown_parent_returns_desired(self, library_tree):
        index = LibraryIndex(library_tree)
        unknown = library_tree / "Unknown Author"
        result = index.reuse_existing(unknown, "Some Book")
        assert result == "Some Book"

    def test_matches_series_within_author(self, library_tree):
        author_dir = library_tree / "Brandon Sanderson"
        index = LibraryIndex(library_tree)
        result = index.reuse_existing(author_dir, "Mistborn")
        assert result == "Mistborn"


class TestFileExists:
    """Test file existence checks."""

    def test_existing_file_found(self, library_tree):
        index = LibraryIndex(library_tree)
        dest_dir = library_tree / "Brandon Sanderson" / "Mistborn" / "The Final Empire"
        assert index.file_exists(dest_dir, "book.m4b") is True

    def test_nonexistent_file_not_found(self, library_tree):
        index = LibraryIndex(library_tree)
        dest_dir = library_tree / "Brandon Sanderson" / "Mistborn" / "The Final Empire"
        assert index.file_exists(dest_dir, "other.m4b") is False

    def test_wrong_dir_not_found(self, library_tree):
        index = LibraryIndex(library_tree)
        wrong_dir = library_tree / "Brandon Sanderson"
        assert index.file_exists(wrong_dir, "book.m4b") is False


class TestMarkProcessed:
    """Test cross-source dedup within a batch."""

    def test_first_call_returns_false(self, library_tree):
        index = LibraryIndex(library_tree)
        assert index.mark_processed("new_book") is False

    def test_second_call_returns_true(self, library_tree):
        index = LibraryIndex(library_tree)
        index.mark_processed("book_stem")
        assert index.mark_processed("book_stem") is True

    def test_different_stems_independent(self, library_tree):
        index = LibraryIndex(library_tree)
        index.mark_processed("book_a")
        assert index.mark_processed("book_b") is False


class TestDynamicRegistration:
    """Test registering new content during batch processing."""

    def test_register_new_folder(self, library_tree):
        index = LibraryIndex(library_tree)
        index.register_new_folder(library_tree, "New Author")
        result = index.reuse_existing(library_tree, "New Author")
        assert result == "New Author"

    def test_register_new_folder_near_match(self, library_tree):
        index = LibraryIndex(library_tree)
        index.register_new_folder(library_tree, "Food A Love Story")
        result = index.reuse_existing(library_tree, "Food- A Love Story")
        assert result == "Food A Love Story"

    def test_register_new_file(self, library_tree):
        index = LibraryIndex(library_tree)
        new_dir = library_tree / "New Author" / "New Book"
        index.register_new_file(new_dir, "audio.m4b")
        assert index.file_exists(new_dir, "audio.m4b") is True

    def test_folder_count_increases(self, library_tree):
        index = LibraryIndex(library_tree)
        before = index.folder_count
        index.register_new_folder(library_tree, "Another Author")
        assert index.folder_count == before + 1

    def test_file_count_increases(self, library_tree):
        index = LibraryIndex(library_tree)
        before = index.file_count
        index.register_new_file(library_tree / "dir", "file.m4b")
        assert index.file_count == before + 1


class TestIsCorrectlyPlaced:
    """Test reorganize mode placement detection."""

    def test_same_path_is_correct(self, library_tree):
        index = LibraryIndex(library_tree)
        path = library_tree / "Brandon Sanderson" / "Mistborn" / "The Final Empire" / "book.m4b"
        assert index.is_correctly_placed(path, path) is True

    def test_different_path_is_incorrect(self, library_tree):
        index = LibraryIndex(library_tree)
        source = library_tree / "_unsorted" / "Random Book" / "book.m4b"
        dest = library_tree / "Some Author" / "Random Book" / "book.m4b"
        assert index.is_correctly_placed(source, dest) is False

    def test_resolved_symlinks_match(self, library_tree):
        """Symlinks to the same file are correctly placed."""
        index = LibraryIndex(library_tree)
        real = library_tree / "Stephen King" / "The Shining" / "shining.m4b"
        # Same resolved path
        assert index.is_correctly_placed(real, real) is True

    def test_handles_nonexistent_paths(self, library_tree):
        index = LibraryIndex(library_tree)
        source = library_tree / "nonexistent" / "book.m4b"
        dest = library_tree / "other" / "book.m4b"
        # Should not raise, just return False
        assert index.is_correctly_placed(source, dest) is False
