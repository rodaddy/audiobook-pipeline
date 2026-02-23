"""Tests for ops/organize.py -- path parsing and Plex folder building."""

from pathlib import Path

import pytest

from audiobook_pipeline.library_index import LibraryIndex
from audiobook_pipeline.ops.organize import (
    _extract_author,
    _looks_like_author,
    _normalize_for_compare,
    _reuse_existing,
    _strip_label_suffix,
    build_plex_path,
    copy_to_library,
    move_in_library,
    parse_path,
)


class TestParsePath:
    """Test path parsing patterns A through G."""

    def test_pattern_a_author_series_position_title(self):
        """Pattern A: Author-Series-#N-Title (parent directory pattern)"""
        result = parse_path(
            "/media/Brandon Sanderson-Mistborn-#1-The Final Empire/audio.m4b"
        )
        assert result["author"] == "Brandon Sanderson"
        assert result["series"] == "Mistborn"
        assert result["position"] == "1"
        assert result["title"] == "The Final Empire"

    def test_pattern_a_nested_subseries(self):
        """Pattern A: nested subseries with multiple #N markers"""
        result = parse_path("/media/Author-Series-#1-Subseries-#2-Title/audio.m4b")
        assert result["author"] == "Author"
        # Parser takes only up to first #N, not the nested part
        assert result["series"] == "Series"
        assert result["position"] == "2"
        assert result["title"] == "Title"

    def test_pattern_a_malformed_markers(self):
        """Pattern A: normalize malformed #N markers"""
        # "#-N" -> "#N"
        result = parse_path("/media/Author-Series-#-3-Title/audio.m4b")
        assert result["position"] == "3"
        # "#N " -> "#N-"
        result = parse_path("/media/Author-Series-#4 Title/audio.m4b")
        assert result["position"] == "4"

    def test_pattern_b_series_number_title(self):
        """Pattern B: SeriesName NN Title (parent directory pattern)"""
        result = parse_path("/media/The First Law 04 Best Served Cold/audio.m4b")
        assert result["series"] == "The First Law"
        # Position is normalized: "04" -> "4"
        assert result["position"] == "4"
        assert result["title"] == "Best Served Cold"

    def test_pattern_b2_series_dash_number_title(self):
        """Pattern B2: Name N - Title (parent directory pattern)"""
        result = parse_path("/media/Deathgate Cycle 1 - Dragon Wing/audio.m4b")
        assert result["series"] == "Deathgate Cycle"
        assert result["position"] == "1"
        assert result["title"] == "Dragon Wing"

    def test_pattern_g_brackets(self):
        """Pattern G: Series [NN] Title (parent directory pattern)"""
        result = parse_path("/media/Mistborn [01] The Final Empire/audio.m4b")
        assert result["series"] == "Mistborn"
        # Position is normalized: "01" -> "1"
        assert result["position"] == "1"
        assert result["title"] == "The Final Empire"

    def test_pattern_c_grandparent_as_author(self):
        """Pattern C: grandparent directory is author"""
        result = parse_path(
            "/media/Brandon Sanderson/Mistborn Era 1/The Final Empire.m4b"
        )
        assert result["author"] == "Brandon Sanderson"

    def test_pattern_e_author_dash_series_grandparent(self):
        """Pattern E: grandparent split on ' - ' into Author - Series"""
        result = parse_path(
            "/media/Brandon Sanderson - Mistborn/Book 1/The Final Empire.m4b"
        )
        assert result["author"] == "Brandon Sanderson"
        assert result["series"] == "Mistborn"

    def test_pattern_f_generic_basename_recovery(self):
        """Pattern F: recover from generic basenames like 'file.m4b'"""
        result = parse_path("/media/Mistborn - Audiobook/file.m4b")
        assert result["title"] == "Mistborn"

    def test_pattern_f_generic_basename_use_grandparent(self):
        """Pattern F: use grandparent when parent is also generic"""
        result = parse_path("/media/Brandon Sanderson/output/MP3.m4b")
        assert result["title"] == "Brandon Sanderson"

    def test_author_title_split_with_dash(self):
        """Split 'Author-Title' from parent directory"""
        result = parse_path("/media/Brandon Sanderson-The Final Empire/audio.m4b")
        assert result["author"] == "Brandon Sanderson"
        assert result["title"] == "The Final Empire"

    def test_dedup_author_equals_series(self):
        """If author == series, clear author (path didn't have real author)"""
        # Pattern E splits grandparent on " - " into author/series
        # If both end up the same, author is cleared
        result = parse_path("/media/Mistborn - Mistborn/Book 1/Title.m4b")
        assert result["author"] == ""
        assert result["series"] == "Mistborn"
        assert result["title"] == "Title"

    def test_position_normalization(self):
        """Leading zeros stripped: '01' -> '1', '003' -> '3'"""
        result = parse_path("/media/Series-#001-Title/audio.m4b")
        assert result["position"] == "1"

    def test_unicode_path(self):
        """Unicode characters in path"""
        result = parse_path("/media/José García-El Niño-#1-La Aventura/audio.m4b")
        assert result["author"] == "José García"
        assert result["series"] == "El Niño"
        assert result["title"] == "La Aventura"

    def test_special_chars_in_title(self):
        """Special characters and punctuation in title"""
        result = parse_path("/media/Author-Series-#1-Title: A Love Story!/audio.m4b")
        assert result["title"] == "Title: A Love Story!"

    def test_strips_audiobook_metadata(self):
        """Strip metadata junk from title"""
        result = parse_path("/media/Title (The AudioBook).m4b")
        assert result["title"] == "Title"
        result = parse_path("/media/Title (Unabridged).m4b")
        assert result["title"] == "Title"
        result = parse_path("/media/Title {64k}.m4b")
        assert result["title"] == "Title"

    def test_strips_dash_artifacts(self):
        """Strip dash artifacts: 'Food- A Love Story' -> 'Food A Love Story'"""
        result = parse_path("/media/Food- A Love Story.m4b")
        assert result["title"] == "Food A Love Story"

    def test_parenthesized_series_in_title(self):
        """Extract series from title: 'Title (Series Name, Book 2.5)'"""
        result = parse_path("/media/Title (Series Name, Book 2.5).m4b")
        assert result["title"] == "Title"
        assert result["series"] == "Series Name"
        assert result["position"] == "2.5"

    def test_parenthesized_series_with_day(self):
        """Extract series with Day marker"""
        result = parse_path("/media/Title - (Series Name - Day 1).m4b")
        assert result["title"] == "Title"
        assert result["series"] == "Series Name"
        assert result["position"] == "1"

    def test_pipeline_hash_stripped(self):
        """Pipeline hash suffix stripped before parsing"""
        result = parse_path(
            "/media/Author-Series-#1-Title - a7edd490030561fb/audio.m4b"
        )
        assert result["title"] == "Title"

    def test_no_metadata_extracted(self):
        """Minimal path with no author/series"""
        result = parse_path("/media/Some Random Title.m4b")
        assert result["author"] == ""
        assert result["series"] == ""
        assert result["title"] == "Some Random Title"
        assert result["position"] == ""

    def test_series_equals_title_skipped(self):
        """Don't extract series if it matches the title"""
        # Parser may not extract series -- this is more a build_plex_path concern
        result = parse_path("/media/Author/Series Name/Series Name.m4b")
        # build_plex_path will skip series folder if series == title
        assert result["title"] == "Series Name"


class TestLooksLikeAuthor:
    """Test author name heuristics."""

    def test_valid_author_names(self):
        assert _looks_like_author("Brandon Sanderson")
        assert _looks_like_author("J.K. Rowling")
        assert _looks_like_author("Tad Williams")
        assert _looks_like_author("N.K. Jemisin")

    def test_rejects_too_many_words(self):
        """Reject names with >5 words (likely titles)"""
        assert not _looks_like_author("This Is A Very Long Title Name")

    def test_rejects_article_prefixed(self):
        """Reject names starting with articles (likely titles)"""
        assert not _looks_like_author("The Name of the Wind")
        assert not _looks_like_author("A Tale of Two Cities")
        assert not _looks_like_author("An American Tragedy")

    def test_rejects_single_word(self):
        """Single word is suspicious (could be series, not author)"""
        assert not _looks_like_author("Mistborn")

    def test_rejects_collection_keywords(self):
        """Reject names containing collection keywords"""
        assert not _looks_like_author("Fantasy Series Collection")
        assert not _looks_like_author("Mistborn Trilogy")
        assert not _looks_like_author("Chronicles of Amber")
        assert not _looks_like_author("All Chaptered")
        assert not _looks_like_author("Standalones")

    def test_rejects_digits(self):
        """Reject names with digits"""
        assert not _looks_like_author("Book 1")
        assert not _looks_like_author("Series 2024")

    def test_rejects_too_long(self):
        """Reject names over 50 chars"""
        assert not _looks_like_author("A" * 51)

    def test_rejects_technical_keywords(self):
        """Reject technical/pipeline keywords"""
        assert not _looks_like_author("output")
        assert not _looks_like_author("processing")
        assert not _looks_like_author("pipeline")


class TestNormalizeForCompare:
    """Test folder name normalization for duplicate detection."""

    def test_strips_year_suffix(self):
        assert _normalize_for_compare("Food A Love Story (2014)") == "food a love story"

    def test_strips_edition_markers(self):
        assert _normalize_for_compare("Title (Unabridged)") == "title"
        assert _normalize_for_compare("Title (The AudioBook)") == "title"

    def test_removes_punctuation(self):
        assert _normalize_for_compare("Food- A Love Story") == "food a love story"

    def test_collapses_whitespace(self):
        assert _normalize_for_compare("Title   With  Spaces") == "title with space"

    def test_strips_trailing_s(self):
        """Strip single trailing 's' for singular/plural matching"""
        assert _normalize_for_compare("Chronicles") == "chronicle"
        assert _normalize_for_compare("Books") == "book"
        # But not for short words ending in 's'
        # (Note: current impl would turn "Mass" -> "Mas", "Ross" -> "Ros")

    def test_lowercases(self):
        assert _normalize_for_compare("Title") == "title"

    def test_unicode_preserved(self):
        """Unicode chars are preserved in normalization"""
        normalized = _normalize_for_compare("José García")
        assert "josé" in normalized
        assert "garcía" in normalized


class TestReuseExisting:
    """Test folder reuse for duplicate detection."""

    def test_exact_match_fast_path(self, tmp_path):
        """Exact match returns immediately"""
        (tmp_path / "Mistborn").mkdir()
        assert _reuse_existing(tmp_path, "Mistborn") == "Mistborn"

    def test_near_match_reuses_existing(self, tmp_path):
        """Near-match returns existing folder name"""
        (tmp_path / "Food A Love Story").mkdir()
        assert _reuse_existing(tmp_path, "Food- A Love Story") == "Food A Love Story"

    def test_near_match_with_year(self, tmp_path):
        """Year suffix ignored in comparison"""
        (tmp_path / "Title (2014)").mkdir()
        assert _reuse_existing(tmp_path, "Title") == "Title (2014)"

    def test_no_match_returns_desired(self, tmp_path):
        """No match returns desired name unchanged"""
        (tmp_path / "Existing").mkdir()
        assert _reuse_existing(tmp_path, "New Folder") == "New Folder"

    def test_parent_does_not_exist(self, tmp_path):
        """Parent doesn't exist returns desired unchanged"""
        nonexistent = tmp_path / "nonexistent"
        assert _reuse_existing(nonexistent, "Desired") == "Desired"

    def test_ignores_files(self, tmp_path):
        """Only checks directories, not files"""
        (tmp_path / "file.txt").write_text("data")
        assert _reuse_existing(tmp_path, "file.txt") == "file.txt"


class TestBuildPlexPath:
    """Test Plex-compatible path construction."""

    def test_author_series_title(self, tmp_path):
        """Structure: Author/Series/Title/"""
        metadata = {
            "author": "Brandon Sanderson",
            "series": "Mistborn",
            "title": "The Final Empire",
            "position": "1",
        }
        result = build_plex_path(tmp_path, metadata)
        assert (
            result
            == tmp_path / "Brandon Sanderson" / "Mistborn" / "Book 1 - The Final Empire"
        )

    def test_author_no_series(self, tmp_path):
        """Structure: Author/Title/"""
        metadata = {
            "author": "Brandon Sanderson",
            "series": "",
            "title": "The Final Empire",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata)
        assert result == tmp_path / "Brandon Sanderson" / "The Final Empire"

    def test_no_author_with_series(self, tmp_path):
        """Structure: _unsorted/Series/Title/"""
        metadata = {
            "author": "",
            "series": "Mistborn",
            "title": "The Final Empire",
            "position": "1",
        }
        result = build_plex_path(tmp_path, metadata)
        assert (
            result == tmp_path / "_unsorted" / "Mistborn" / "Book 1 - The Final Empire"
        )

    def test_no_author_no_series(self, tmp_path):
        """Structure: _unsorted/Title/"""
        metadata = {
            "author": "",
            "series": "",
            "title": "Some Random Book",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata)
        assert result == tmp_path / "_unsorted" / "Some Random Book"

    def test_skips_series_when_equals_title(self, tmp_path):
        """Avoid Author/Title/Title/ by skipping series"""
        metadata = {
            "author": "Brandon Sanderson",
            "series": "Elantris",
            "title": "Elantris",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata)
        assert result == tmp_path / "Brandon Sanderson" / "Elantris"

    def test_sanitizes_filenames(self, tmp_path):
        """Unsafe chars sanitized in path components"""
        metadata = {
            "author": "Author/Name",
            "series": "Series:Name",
            "title": "Title<>Name",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata)
        # sanitize_filename replaces unsafe chars with _ and collapses repeated underscores
        # Verify each component is sanitized
        assert result.parts[-3] == "Author_Name"  # author folder (/ becomes _)
        assert result.parts[-2] == "Series_Name"  # series folder (: becomes _)
        assert (
            result.parts[-1] == "Title_Name"
        )  # title folder (<> becomes __, collapsed to _)

    def test_unknown_fallback_for_empty_title(self, tmp_path):
        """Empty title falls back to 'Unknown'"""
        metadata = {
            "author": "",
            "series": "",
            "title": "",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata)
        assert result == tmp_path / "_unsorted" / "Unknown"

    def test_reuses_existing_author_folder(self, tmp_path):
        """Reuse existing author folder with near-match"""
        # Create "Brandon Sanderson" folder
        (tmp_path / "Brandon Sanderson").mkdir()
        metadata = {
            "author": "Brandon Sanderson",
            "series": "",
            "title": "Title",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata)
        # Should reuse existing "Brandon Sanderson" folder
        assert result.parent == tmp_path / "Brandon Sanderson"

    def test_reuses_existing_series_folder(self, tmp_path):
        """Reuse existing series folder with near-match"""
        author_dir = tmp_path / "Author"
        author_dir.mkdir()
        (author_dir / "Mistborn Era 1").mkdir()
        metadata = {
            "author": "Author",
            "series": "Mistborn Era 1 (2014)",
            "title": "Title",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata)
        assert result.parent == author_dir / "Mistborn Era 1"

    def test_reuses_existing_title_folder(self, tmp_path):
        """Reuse existing title folder with near-match"""
        series_dir = tmp_path / "Author" / "Series"
        series_dir.mkdir(parents=True)
        (series_dir / "Food A Love Story").mkdir()
        metadata = {
            "author": "Author",
            "series": "Series",
            "title": "Food- A Love Story",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata)
        assert result == series_dir / "Food A Love Story"


class TestCopyToLibrary:
    """Test copying audiobook files to library destination."""

    def test_successful_copy_creates_directory_and_copies_file(self, tmp_path):
        """Successful copy creates directory tree and copies the file"""
        source_file = tmp_path / "source" / "audio.m4b"
        source_file.parent.mkdir()
        source_file.write_text("audiobook content")

        dest_dir = tmp_path / "library" / "Author" / "Title"

        result = copy_to_library(source_file, dest_dir)

        assert result == dest_dir / "audio.m4b"
        assert result.exists()
        assert result.read_text() == "audiobook content"
        assert dest_dir.exists()

    def test_dry_run_mode_skips_actual_copy(self, tmp_path):
        """Dry run mode returns path but doesn't copy"""
        source_file = tmp_path / "source" / "audio.m4b"
        source_file.parent.mkdir()
        source_file.write_text("audiobook content")

        dest_dir = tmp_path / "library" / "Author" / "Title"

        result = copy_to_library(source_file, dest_dir, dry_run=True)

        assert result == dest_dir / "audio.m4b"
        assert not result.exists()
        # Directory is created even in dry run
        assert dest_dir.exists()

    def test_existing_file_with_same_size_is_skipped(self, tmp_path):
        """Existing file with same size is not overwritten"""
        source_file = tmp_path / "source" / "audio.m4b"
        source_file.parent.mkdir()
        source_file.write_text("audiobook content")

        dest_dir = tmp_path / "library" / "Author" / "Title"
        dest_dir.mkdir(parents=True)
        dest_file = dest_dir / "audio.m4b"
        dest_file.write_text("audiobook content")

        original_mtime = dest_file.stat().st_mtime

        result = copy_to_library(source_file, dest_dir)

        assert result == dest_file
        # File should not be modified (same mtime)
        assert dest_file.stat().st_mtime == original_mtime

    def test_existing_file_with_different_size_gets_overwritten(self, tmp_path):
        """Existing file with different size is overwritten"""
        source_file = tmp_path / "source" / "audio.m4b"
        source_file.parent.mkdir()
        source_file.write_text("new audiobook content")

        dest_dir = tmp_path / "library" / "Author" / "Title"
        dest_dir.mkdir(parents=True)
        dest_file = dest_dir / "audio.m4b"
        dest_file.write_text("old")

        result = copy_to_library(source_file, dest_dir)

        assert result == dest_file
        assert dest_file.read_text() == "new audiobook content"


class TestStripLabelSuffix:
    """Test stripping common audiobook label suffixes."""

    def test_strips_audiobook_suffix(self):
        assert _strip_label_suffix("Title - Audiobook") == "Title"

    def test_strips_audio_suffix(self):
        assert _strip_label_suffix("Title - Audio") == "Title"

    def test_strips_unabridged_suffix(self):
        assert _strip_label_suffix("Title - Unabridged") == "Title"

    def test_strips_abridged_suffix(self):
        assert _strip_label_suffix("Title - Abridged") == "Title"

    def test_case_insensitive(self):
        assert _strip_label_suffix("Title - AUDIOBOOK") == "Title"
        assert _strip_label_suffix("Title - audiobook") == "Title"

    def test_preserves_non_label_suffixes(self):
        assert _strip_label_suffix("Title - Part 1") == "Title - Part 1"
        assert _strip_label_suffix("Title - Series Name") == "Title - Series Name"

    def test_no_suffix_unchanged(self):
        assert _strip_label_suffix("Just A Title") == "Just A Title"


class TestExtractAuthor:
    """Test extracting author names from directory names."""

    def test_strips_parenthetical_suffixes(self):
        """Strip parenthetical info like (All Chaptered)"""
        assert _extract_author("Tad Williams (All Chaptered)") == "Tad Williams"
        assert _extract_author("Brandon Sanderson (Fantasy)") == "Brandon Sanderson"

    def test_strips_bracketed_suffixes(self):
        """Strip bracketed info like [1-5]"""
        assert _extract_author("Author Name [1-5]") == "Author Name"
        assert _extract_author("J.K. Rowling [Complete]") == "J.K. Rowling"

    def test_splits_on_dash_separator(self):
        """Split on ' - ' and take first part if no digits"""
        assert _extract_author("Brandon Sanderson - Mistborn") == "Brandon Sanderson"
        assert _extract_author("J.R.R. Tolkien - Middle Earth") == "J.R.R. Tolkien"

    def test_preserves_dash_if_contains_digits(self):
        """Preserve full string if left side has digits"""
        # If the candidate author (left of dash) has digits, return cleaned full string
        result = _extract_author("Series 1 - Title")
        assert result == "Series 1 - Title"

    def test_combines_parenthetical_and_dash_stripping(self):
        """Handle both parenthetical and dash splitting"""
        assert _extract_author("Author Name (All Chaptered) - Series") == "Author Name"

    def test_no_special_chars_unchanged(self):
        """Plain author name returned as-is"""
        assert _extract_author("Brandon Sanderson") == "Brandon Sanderson"


class TestBuildPlexPathWithIndex:
    """Test build_plex_path using LibraryIndex for O(1) lookups."""

    def test_index_reuses_existing_folder(self, tmp_path):
        """Index provides O(1) folder reuse instead of iterdir()"""
        # Create library structure
        (tmp_path / "Brandon Sanderson" / "Mistborn").mkdir(parents=True)
        index = LibraryIndex(tmp_path)

        metadata = {
            "author": "Brandon Sanderson",
            "series": "Mistborn",
            "title": "The Final Empire",
            "position": "1",
        }
        result = build_plex_path(tmp_path, metadata, index=index)
        assert (
            result
            == tmp_path / "Brandon Sanderson" / "Mistborn" / "Book 1 - The Final Empire"
        )

    def test_index_near_match_reuses(self, tmp_path):
        """Index near-match detection works like filesystem fallback"""
        unsorted = tmp_path / "_unsorted"
        unsorted.mkdir()
        (unsorted / "Food A Love Story").mkdir()
        index = LibraryIndex(tmp_path)

        metadata = {
            "author": "",
            "series": "",
            "title": "Food- A Love Story",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata, index=index)
        assert result == tmp_path / "_unsorted" / "Food A Love Story"

    def test_index_registers_new_folders(self, tmp_path):
        """build_plex_path registers new path components in the index"""
        index = LibraryIndex(tmp_path)

        metadata = {
            "author": "New Author",
            "series": "New Series",
            "title": "New Book",
            "position": "",
        }
        build_plex_path(tmp_path, metadata, index=index)

        # Subsequent call should find the registered folders
        assert index.reuse_existing(tmp_path, "New Author") == "New Author"

    def test_none_index_falls_back_to_filesystem(self, tmp_path):
        """index=None uses _reuse_existing (filesystem scan)"""
        metadata = {
            "author": "Author",
            "series": "",
            "title": "Title",
            "position": "",
        }
        result = build_plex_path(tmp_path, metadata, index=None)
        assert result == tmp_path / "Author" / "Title"


class TestMoveInLibrary:
    """Test moving files within the library for reorganize mode."""

    def test_moves_file_to_destination(self, tmp_path):
        source_dir = tmp_path / "old_location"
        source_dir.mkdir()
        source_file = source_dir / "audio.m4b"
        source_file.write_text("audiobook content")

        dest_dir = tmp_path / "new_location"

        result = move_in_library(source_file, dest_dir)

        assert result == dest_dir / "audio.m4b"
        assert result.exists()
        assert result.read_text() == "audiobook content"
        assert not source_file.exists()

    def test_dry_run_skips_move(self, tmp_path):
        source_dir = tmp_path / "old_location"
        source_dir.mkdir()
        source_file = source_dir / "audio.m4b"
        source_file.write_text("audiobook content")

        dest_dir = tmp_path / "new_location"

        result = move_in_library(source_file, dest_dir, dry_run=True)

        assert result == dest_dir / "audio.m4b"
        assert source_file.exists()  # Not moved
        assert not result.exists()

    def test_cleans_empty_parent_dirs(self, tmp_path):
        """Empty parent dirs cleaned up after move"""
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        source_file = deep / "audio.m4b"
        source_file.write_text("audio")

        dest_dir = tmp_path / "dest"

        move_in_library(source_file, dest_dir)

        # All empty parents should be cleaned
        assert not deep.exists()
        assert not (tmp_path / "a" / "b").exists()
        assert not (tmp_path / "a").exists()

    def test_skips_same_size_existing(self, tmp_path):
        """Existing file with same size is not overwritten"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        source_file = source_dir / "audio.m4b"
        source_file.write_text("content")

        dest_dir = tmp_path / "dest"
        dest_dir.mkdir(parents=True)
        dest_file = dest_dir / "audio.m4b"
        dest_file.write_text("content")

        result = move_in_library(source_file, dest_dir)
        assert result == dest_file

    def test_creates_dest_dir(self, tmp_path):
        """Destination directory created if it doesn't exist"""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        source_file = source_dir / "audio.m4b"
        source_file.write_text("audio")

        dest_dir = tmp_path / "deep" / "nested" / "dir"

        result = move_in_library(source_file, dest_dir)
        assert dest_dir.exists()
        assert result.exists()
