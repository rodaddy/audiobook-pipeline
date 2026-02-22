"""Tests for ASIN resolution stage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from audiobook_pipeline.config import PipelineConfig
from audiobook_pipeline.errors import ManifestError
from audiobook_pipeline.manifest import Manifest
from audiobook_pipeline.models import PipelineMode, Stage, StageStatus
from audiobook_pipeline.stages.asin import run, _search_audible


@pytest.fixture
def mock_config(tmp_path):
    return PipelineConfig(
        _env_file=None,
        nfs_output_dir=tmp_path / "library",
        work_dir=tmp_path / "work",
        manifest_dir=tmp_path / "manifests",
        pipeline_llm_base_url="",
        pipeline_llm_api_key="",
        asin_search_threshold=70.0,
    )


@pytest.fixture
def mock_manifest(tmp_path):
    manifest_dir = tmp_path / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    return Manifest(manifest_dir)


class TestSearchAudible:
    """Test Audible search query strategies (moved from test_organize)."""

    @patch("audiobook_pipeline.stages.asin.search")
    def test_basic_title_search(self, mock_search, mock_config):
        mock_search.return_value = [
            {"asin": "B001", "title": "Test Book", "authors": [], "cover_url": ""},
        ]
        results = _search_audible("Test Book", "", mock_config)
        assert len(results) == 1
        assert mock_search.call_count == 1

    @patch("audiobook_pipeline.stages.asin.search")
    def test_includes_series_queries(self, mock_search, mock_config):
        mock_search.return_value = []
        _search_audible("Book Title", "Series Name", mock_config)
        assert mock_search.call_count == 3

    @patch("audiobook_pipeline.stages.asin.search")
    def test_widen_adds_author_queries(self, mock_search, mock_config):
        mock_search.return_value = []
        _search_audible("Book", "Series", mock_config, author="Author", widen=True)
        assert mock_search.call_count == 5

    @patch("audiobook_pipeline.stages.asin.search")
    def test_deduplicates_by_asin(self, mock_search, mock_config):
        mock_search.return_value = [
            {"asin": "B001", "title": "Dup", "authors": [], "cover_url": ""},
        ]
        results = _search_audible("Book", "Series", mock_config)
        unique_asins = {r["asin"] for r in results}
        assert len(unique_asins) == len(results)

    @patch("audiobook_pipeline.stages.asin.search")
    def test_handles_search_exceptions(self, mock_search, mock_config):
        mock_search.side_effect = [
            [{"asin": "B001", "title": "Good", "authors": [], "cover_url": ""}],
            Exception("Network error"),
        ]
        results = _search_audible("Book", "Series", mock_config)
        assert len(results) == 1


class TestAsinStage:
    """Test ASIN resolution stage."""

    @patch("audiobook_pipeline.stages.asin.get_tags")
    @patch("audiobook_pipeline.stages.asin.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.asin.search")
    @patch("audiobook_pipeline.stages.asin.score_results")
    def test_audible_match_writes_manifest(
        self,
        mock_score,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        source_file = tmp_path / "John Smith - Great Book.m4b"
        source_file.write_text("fake audio")

        mock_get_tags.return_value = {"title": "Great Book"}
        mock_extract_author.return_value = "John Smith"

        candidates = [
            {
                "asin": "B001ABC",
                "title": "Great Book",
                "authors": ["John Smith"],
                "author_str": "John Smith",
                "series": "A Series",
                "position": "1",
                "cover_url": "https://example.com/cover.jpg",
            },
        ]
        mock_search.return_value = candidates
        mock_score.return_value = [{**candidates[0], "score": 85.0}]

        book_hash = "asin01"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        run(source_file, book_hash, mock_config, mock_manifest)

        data = mock_manifest.read(book_hash)
        assert data["stages"]["asin"]["status"] == StageStatus.COMPLETED.value
        assert data["metadata"]["parsed_author"] == "John Smith"
        assert data["metadata"]["parsed_title"] == "Great Book"
        assert data["metadata"]["parsed_series"] == "A Series"
        assert data["metadata"]["parsed_position"] == "1"
        assert data["metadata"]["parsed_asin"] == "B001ABC"
        assert data["metadata"]["cover_url"] == "https://example.com/cover.jpg"

    @patch("audiobook_pipeline.stages.asin.get_tags")
    @patch("audiobook_pipeline.stages.asin.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.asin.search")
    def test_no_audible_falls_back_to_tags(
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

        mock_get_tags.return_value = {"title": "Tag Title"}
        mock_extract_author.return_value = "Tag Author"
        mock_search.return_value = []

        book_hash = "asin02"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        run(source_file, book_hash, mock_config, mock_manifest)

        data = mock_manifest.read(book_hash)
        assert data["stages"]["asin"]["status"] == StageStatus.COMPLETED.value
        assert data["metadata"]["parsed_author"] == "Tag Author"
        assert data["metadata"]["parsed_asin"] == ""
        assert data["metadata"]["cover_url"] == ""

    @patch("audiobook_pipeline.stages.asin.parse_path")
    @patch("audiobook_pipeline.stages.asin.get_client")
    @patch("audiobook_pipeline.stages.asin.disambiguate")
    @patch("audiobook_pipeline.stages.asin.get_tags")
    @patch("audiobook_pipeline.stages.asin.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.asin.search")
    @patch("audiobook_pipeline.stages.asin.score_results")
    def test_ai_disambiguation_on_low_score(
        self,
        mock_score,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        mock_disambiguate,
        mock_get_client,
        mock_parse_path,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        mock_config.pipeline_llm_base_url = "http://test:4000"
        mock_config.pipeline_llm_api_key = "test-key"

        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        mock_parse_path.return_value = {
            "author": "",
            "title": "book",
            "series": "",
            "position": "",
        }
        mock_get_tags.return_value = {}
        mock_extract_author.return_value = ""

        candidates = [
            {
                "asin": "B001",
                "title": "Book A",
                "authors": ["Author A"],
                "author_str": "Author A",
                "series": "",
                "position": "",
                "cover_url": "",
            },
            {
                "asin": "B002",
                "title": "Book B",
                "authors": ["Author B"],
                "author_str": "Author B",
                "series": "",
                "position": "",
                "cover_url": "",
            },
        ]
        mock_search.return_value = candidates
        mock_score.return_value = [
            {**candidates[0], "score": 65.0},
            {**candidates[1], "score": 60.0},
        ]

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_disambiguate.return_value = {**candidates[1], "score": 60.0}

        book_hash = "asin03"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        run(source_file, book_hash, mock_config, mock_manifest)

        mock_disambiguate.assert_called_once()
        data = mock_manifest.read(book_hash)
        assert data["metadata"]["parsed_author"] == "Author B"

    @patch("audiobook_pipeline.stages.asin.get_tags")
    @patch("audiobook_pipeline.stages.asin.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.asin.search")
    def test_dry_run_still_resolves(
        self,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        """ASIN stage runs in dry-run (metadata-only, no file changes)."""
        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        mock_get_tags.return_value = {"title": "Test Book"}
        mock_extract_author.return_value = "Test Author"
        mock_search.return_value = []

        book_hash = "asin04"
        mock_manifest.create(book_hash, str(source_file), PipelineMode.ORGANIZE)

        run(source_file, book_hash, mock_config, mock_manifest, dry_run=True)

        data = mock_manifest.read(book_hash)
        assert data["stages"]["asin"]["status"] == StageStatus.COMPLETED.value
        assert data["metadata"]["parsed_author"] == "Test Author"

    def test_missing_manifest_raises(self, tmp_path, mock_config):
        manifest = Manifest(mock_config.manifest_dir)
        mock_config.manifest_dir.mkdir(parents=True, exist_ok=True)

        with pytest.raises(ManifestError):
            run(
                source_path=Path("/src/book"),
                book_hash="asin05",
                config=mock_config,
                manifest=manifest,
            )

    @patch("audiobook_pipeline.stages.asin.get_tags")
    @patch("audiobook_pipeline.stages.asin.extract_author_from_tags")
    @patch("audiobook_pipeline.stages.asin.search")
    @patch("audiobook_pipeline.stages.asin.score_results")
    def test_reads_convert_output_for_tags(
        self,
        mock_score,
        mock_search,
        mock_extract_author,
        mock_get_tags,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        """In convert mode, reads tags from convert output file."""
        source_dir = tmp_path / "mp3s"
        source_dir.mkdir()
        (source_dir / "track01.mp3").write_text("fake mp3")

        # Simulate convert output
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        convert_output = work_dir / "audiobook.m4b"
        convert_output.write_text("fake m4b")

        book_hash = "asin06"
        mock_manifest.create(book_hash, str(source_dir), PipelineMode.CONVERT)
        data = mock_manifest.read(book_hash)
        data["stages"]["convert"] = {
            "status": "completed",
            "output_file": str(convert_output),
        }
        mock_manifest.update(book_hash, data)

        mock_get_tags.return_value = {"title": "From Convert"}
        mock_extract_author.return_value = "Convert Author"
        mock_search.return_value = []

        run(source_dir, book_hash, mock_config, mock_manifest)

        # Should have read tags from convert output
        mock_get_tags.assert_called_once_with(convert_output)
        data = mock_manifest.read(book_hash)
        assert data["metadata"]["parsed_author"] == "Convert Author"
