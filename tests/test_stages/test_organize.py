"""Tests for stages/organize.py -- integration tests with mocked dependencies."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audiobook_pipeline.config import PipelineConfig
from audiobook_pipeline.manifest import Manifest
from audiobook_pipeline.models import PipelineMode, Stage, StageStatus
from audiobook_pipeline.stages.organize import _find_audio_file, _search_audible, run


@pytest.fixture
def mock_config(tmp_path):
    """Create a test config with isolated paths."""
    return PipelineConfig(
        _env_file=None,
        nfs_output_dir=tmp_path / "library",
        work_dir=tmp_path / "work",
        manifest_dir=tmp_path / "manifests",
        # Disable AI for most tests
        pipeline_llm_base_url="",
        pipeline_llm_api_key="",
        asin_search_threshold=70.0,
    )


@pytest.fixture
def mock_manifest(tmp_path):
    """Create a test manifest instance."""
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    return Manifest(manifest_dir)


class TestFindAudioFile:
    """Test audio file discovery from source paths."""

    def test_direct_file_path_returns_file(self, tmp_path):
        audio_file = tmp_path / "book.m4b"
        audio_file.write_text("fake audio")
        result = _find_audio_file(audio_file)
        assert result == audio_file

    def test_directory_with_m4b_returns_m4b(self, tmp_path):
        book_dir = tmp_path / "book"
        book_dir.mkdir()
        m4b = book_dir / "audiobook.m4b"
        m4b.write_text("fake audio")
        mp3 = book_dir / "other.mp3"
        mp3.write_text("fake audio")

        result = _find_audio_file(book_dir)
        assert result == m4b

    def test_directory_with_no_m4b_returns_first_audio(self, tmp_path):
        book_dir = tmp_path / "book"
        book_dir.mkdir()
        mp3 = book_dir / "audiobook.mp3"
        mp3.write_text("fake audio")

        result = _find_audio_file(book_dir)
        assert result == mp3

    def test_empty_directory_returns_none(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = _find_audio_file(empty_dir)
        assert result is None

    def test_nested_m4b_found(self, tmp_path):
        book_dir = tmp_path / "book"
        nested = book_dir / "nested"
        nested.mkdir(parents=True)
        m4b = nested / "audiobook.m4b"
        m4b.write_text("fake audio")

        result = _find_audio_file(book_dir)
        assert result == m4b


class TestSearchAudible:
    """Test Audible search query strategies."""

    @patch("audiobook_pipeline.stages.organize.search")
    def test_basic_title_search(self, mock_search, mock_config):
        mock_search.return_value = [
            {"asin": "B001", "title": "Test Book", "authors": []},
        ]
        results = _search_audible("Test Book", "", mock_config)
        assert len(results) == 1
        assert mock_search.call_count == 1

    @patch("audiobook_pipeline.stages.organize.search")
    def test_includes_series_queries(self, mock_search, mock_config):
        mock_search.return_value = []
        _search_audible("Book Title", "Series Name", mock_config)
        # Should query: title, series, "series title"
        assert mock_search.call_count == 3

    @patch("audiobook_pipeline.stages.organize.search")
    def test_widen_adds_author_queries(self, mock_search, mock_config):
        mock_search.return_value = []
        _search_audible("Book", "Series", mock_config, author="Author", widen=True)
        # Should query: title, series, series+title, author+title, author+series
        assert mock_search.call_count == 5

    @patch("audiobook_pipeline.stages.organize.search")
    def test_deduplicates_by_asin(self, mock_search, mock_config):
        # Same ASIN appears in multiple query results
        mock_search.return_value = [
            {"asin": "B001", "title": "Duplicate Book", "authors": []},
        ]
        results = _search_audible("Book", "Series", mock_config)
        # Should return only one entry even if multiple queries hit it
        unique_asins = {r["asin"] for r in results}
        assert len(unique_asins) == len(results)

    @patch("audiobook_pipeline.stages.organize.search")
    def test_handles_search_exceptions(self, mock_search, mock_config):
        # First call succeeds, second raises exception
        mock_search.side_effect = [
            [{"asin": "B001", "title": "Good", "authors": []}],
            Exception("Network error"),
        ]
        results = _search_audible("Book", "Series", mock_config)
        # Should return results from successful query
        assert len(results) == 1


class TestOrganizeStage:
    """Integration tests for the organize stage."""

    @patch("audiobook_pipeline.stages.organize.get_tags")
    @patch("audiobook_pipeline.stages.organize.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.organize.search")
    @patch("audiobook_pipeline.stages.organize.copy_to_library")
    def test_successful_organization_no_ai(
        self,
        mock_copy,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        # Setup source file
        source_file = tmp_path / "John Smith - Great Book.m4b"
        source_file.write_text("fake audio")

        # Mock ffprobe responses
        mock_get_tags.return_value = {"title": "Great Book"}
        mock_extract_author.return_value = "John Smith"

        # Mock Audible response with high-scoring match
        mock_search.return_value = [
            {
                "asin": "B001ABC",
                "title": "Great Book",
                "authors": [{"name": "John Smith"}],
                "author_str": "John Smith",
                "series": "",
                "position": "",
            },
        ]

        # Mock successful copy
        dest_file = tmp_path / "library" / "John Smith" / "Great Book" / source_file.name
        mock_copy.return_value = dest_file

        # Create manifest entry
        book_hash = "testhash123"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        # Run organize stage
        run(source_file, book_hash, mock_config, mock_manifest, dry_run=False)

        # Verify stage completed
        data = mock_manifest.read(book_hash)
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value

        # Verify copy was called
        mock_copy.assert_called_once()

    @patch("audiobook_pipeline.stages.organize.get_tags")
    @patch("audiobook_pipeline.stages.organize.extract_author_from_tags")
    def test_no_audio_file_fails_stage(
        self,
        mock_extract_author,
        mock_get_tags,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        # Empty directory, no audio files
        source_dir = tmp_path / "empty"
        source_dir.mkdir()

        book_hash = "testhash123"
        mock_manifest.create(book_hash, str(source_dir), PipelineMode.ORGANIZE)

        run(source_dir, book_hash, mock_config, mock_manifest, dry_run=False)

        # Verify stage failed
        data = mock_manifest.read(book_hash)
        assert data["stages"]["organize"]["status"] == StageStatus.FAILED.value

    @patch("audiobook_pipeline.stages.organize.get_tags")
    @patch("audiobook_pipeline.stages.organize.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.organize.search")
    def test_dry_run_skips_copy(
        self,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        mock_get_tags.return_value = {"title": "Test"}
        mock_extract_author.return_value = ""
        mock_search.return_value = []

        book_hash = "testhash123"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        # Dry run should complete without copying
        run(source_file, book_hash, mock_config, mock_manifest, dry_run=True)

        data = mock_manifest.read(book_hash)
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value
        # Verify no output_file was actually created (dry run)
        assert "output_file" not in data["stages"]["organize"]

    @patch("audiobook_pipeline.stages.organize.get_tags")
    @patch("audiobook_pipeline.stages.organize.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.organize.search")
    @patch("audiobook_pipeline.stages.organize.build_plex_path")
    def test_existing_file_skipped(
        self,
        mock_build_path,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        # Create the destination file to simulate existing
        dest_dir = tmp_path / "library" / "_unsorted" / "book"
        dest_dir.mkdir(parents=True)
        dest_file = dest_dir / source_file.name
        dest_file.write_text("fake audio")

        # Mock to return the existing dest_dir
        mock_build_path.return_value = dest_dir

        mock_get_tags.return_value = {"title": "book"}
        mock_extract_author.return_value = ""
        mock_search.return_value = []

        book_hash = "testhash123"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        run(source_file, book_hash, mock_config, mock_manifest, dry_run=False)

        # Should complete and detect existing file
        data = mock_manifest.read(book_hash)
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value

    @patch("audiobook_pipeline.stages.organize.get_client")
    @patch("audiobook_pipeline.stages.organize.resolve")
    @patch("audiobook_pipeline.stages.organize.get_tags")
    @patch("audiobook_pipeline.stages.organize.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.organize.search")
    @patch("audiobook_pipeline.stages.organize.copy_to_library")
    def test_ai_resolution_when_ai_all_enabled(
        self,
        mock_copy,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        mock_resolve,
        mock_get_client,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        # Enable AI
        mock_config.pipeline_llm_base_url = "http://test:4000"
        mock_config.pipeline_llm_api_key = "test-key"
        mock_config.ai_all = True

        source_file = tmp_path / "ambiguous.m4b"
        source_file.write_text("fake audio")

        mock_get_tags.return_value = {}
        mock_extract_author.return_value = ""
        mock_search.return_value = []

        # Mock AI client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock AI resolution
        mock_resolve.return_value = {
            "author": "AI Resolved Author",
            "title": "AI Resolved Title",
            "series": "",
            "position": "",
        }

        dest_file = tmp_path / "library" / "AI Resolved Author" / "AI Resolved Title" / "ambiguous.m4b"
        mock_copy.return_value = dest_file

        book_hash = "testhash123"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        run(source_file, book_hash, mock_config, mock_manifest, dry_run=False)

        # Verify AI was called
        mock_resolve.assert_called_once()
        mock_get_client.assert_called()

        # Verify metadata was updated with AI results
        data = mock_manifest.read(book_hash)
        assert data["metadata"]["parsed_author"] == "AI Resolved Author"
        assert data["metadata"]["parsed_title"] == "AI Resolved Title"

    @patch("audiobook_pipeline.stages.organize.parse_path")
    @patch("audiobook_pipeline.stages.organize.get_tags")
    @patch("audiobook_pipeline.stages.organize.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.organize.search")
    @patch("audiobook_pipeline.stages.organize.copy_to_library")
    def test_uses_tag_title_when_path_title_is_junk(
        self,
        mock_copy,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        mock_parse_path,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        # Source file has junk filename that matches stem (file.m4b)
        source_file = tmp_path / "file.m4b"
        source_file.write_text("fake audio")

        # parse_path returns the junk filename as title
        mock_parse_path.return_value = {
            "author": "",
            "title": "file",  # Matches source_file.stem
            "series": "",
            "position": "",
        }

        # But tags have good title
        mock_get_tags.return_value = {"title": "Actual Book Title"}
        mock_extract_author.return_value = "Tag Author"
        mock_search.return_value = []

        dest_file = tmp_path / "library" / "Tag Author" / "Actual Book Title" / "file.m4b"
        mock_copy.return_value = dest_file

        book_hash = "testhash123"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        run(source_file, book_hash, mock_config, mock_manifest, dry_run=False)

        # Verify tag title was used
        data = mock_manifest.read(book_hash)
        assert data["metadata"]["parsed_title"] == "Actual Book Title"
        assert data["metadata"]["parsed_author"] == "Tag Author"

    @patch("audiobook_pipeline.stages.organize.get_client")
    @patch("audiobook_pipeline.stages.organize.disambiguate")
    @patch("audiobook_pipeline.stages.organize.get_tags")
    @patch("audiobook_pipeline.stages.organize.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.organize.search")
    @patch("audiobook_pipeline.stages.organize.score_results")
    @patch("audiobook_pipeline.stages.organize.copy_to_library")
    def test_ai_disambiguation_on_low_score(
        self,
        mock_copy,
        mock_score,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        mock_disambiguate,
        mock_get_client,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        # Enable AI but not ai_all
        mock_config.pipeline_llm_base_url = "http://test:4000"
        mock_config.pipeline_llm_api_key = "test-key"
        mock_config.ai_all = False

        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        mock_get_tags.return_value = {}
        mock_extract_author.return_value = ""

        # Mock Audible candidates
        candidates = [
            {"asin": "B001", "title": "Book A", "authors": [], "author_str": "Author A", "series": "", "position": ""},
            {"asin": "B002", "title": "Book B", "authors": [], "author_str": "Author B", "series": "", "position": ""},
        ]
        mock_search.return_value = candidates

        # Mock low fuzzy score (below threshold)
        mock_score.return_value = [
            {**candidates[0], "score": 65.0},  # Below 70 threshold
            {**candidates[1], "score": 60.0},
        ]

        # Mock AI client
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # AI picks the second option
        mock_disambiguate.return_value = {**candidates[1], "score": 60.0}

        dest_file = tmp_path / "library" / "_unsorted" / "Book B" / "book.m4b"
        mock_copy.return_value = dest_file

        book_hash = "testhash123"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        run(source_file, book_hash, mock_config, mock_manifest, dry_run=False)

        # Verify AI disambiguate was called
        mock_disambiguate.assert_called_once()
