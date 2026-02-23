"""Tests for stages/organize.py -- slimmed to pure file-mover.

Organize now reads pre-resolved metadata from manifest (set by ASIN stage)
and source file from metadata/convert stage output. No more Audible/AI mocking.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from audiobook_pipeline.config import PipelineConfig
from audiobook_pipeline.library_index import LibraryIndex
from audiobook_pipeline.pipeline_db import PipelineDB
from audiobook_pipeline.models import STAGE_ORDER, PipelineMode, Stage, StageStatus
from audiobook_pipeline.stages.organize import _find_audio_file, run


@pytest.fixture
def mock_config(tmp_path):
    return PipelineConfig(
        _env_file=None,
        nfs_output_dir=tmp_path / "library",
        work_dir=tmp_path / "work",
        pipeline_llm_base_url="",
        pipeline_llm_api_key="",
    )


@pytest.fixture
def mock_manifest(tmp_path):
    db_path = tmp_path / "work" / "pipeline.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return PipelineDB(db_path)


def _setup_manifest_with_metadata(
    manifest,
    book_hash,
    source_path,
    output_file=None,
    author="Test Author",
    title="Test Book",
    series="",
    position="",
):
    """Create a manifest with ASIN-resolved metadata and optional output file."""
    manifest.create(book_hash, str(source_path), PipelineMode.ORGANIZE)
    data = manifest.read(book_hash)
    data["metadata"].update(
        {
            "parsed_author": author,
            "parsed_title": title,
            "parsed_series": series,
            "parsed_position": position,
            "parsed_asin": "",
            "cover_url": "",
        }
    )
    if output_file:
        data["stages"]["metadata"] = {
            "status": "completed",
            "output_file": str(output_file),
        }
    manifest.update(book_hash, data)
    return manifest


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


class TestOrganizeStage:
    """Integration tests for the slimmed organize stage."""

    @patch("audiobook_pipeline.stages.organize.copy_to_library")
    def test_successful_organization(
        self,
        mock_copy,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        dest_file = (
            tmp_path / "library" / "John Smith" / "Great Book" / source_file.name
        )
        mock_copy.return_value = dest_file

        _setup_manifest_with_metadata(
            mock_manifest,
            "hash01",
            source_file,
            author="John Smith",
            title="Great Book",
        )

        run(source_file, "hash01", mock_config, mock_manifest, dry_run=False)

        data = mock_manifest.read("hash01")
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value
        mock_copy.assert_called_once()

    @patch("audiobook_pipeline.stages.organize.copy_to_library")
    def test_uses_metadata_stage_output(
        self,
        mock_copy,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        """Organize should copy the tagged file from metadata stage output."""
        source_dir = tmp_path / "mp3s"
        source_dir.mkdir()

        # Metadata stage output (tagged file in work_dir)
        tagged_file = tmp_path / "work" / "hash02" / "book.m4b"
        tagged_file.parent.mkdir(parents=True)
        tagged_file.write_text("tagged m4b")

        dest_file = tmp_path / "library" / "Author" / "Title" / "book.m4b"
        mock_copy.return_value = dest_file

        _setup_manifest_with_metadata(
            mock_manifest,
            "hash02",
            source_dir,
            output_file=tagged_file,
            author="Author",
            title="Title",
        )

        run(source_dir, "hash02", mock_config, mock_manifest, dry_run=False)

        # Verify the tagged file was copied (not the source)
        mock_copy.assert_called_once()
        copied_file = mock_copy.call_args[0][0]
        assert copied_file == tagged_file

    def test_no_audio_file_fails_stage(
        self,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        source_dir = tmp_path / "empty"
        source_dir.mkdir()

        _setup_manifest_with_metadata(mock_manifest, "hash03", source_dir)

        run(source_dir, "hash03", mock_config, mock_manifest, dry_run=False)

        data = mock_manifest.read("hash03")
        assert data["stages"]["organize"]["status"] == StageStatus.FAILED.value

    @patch("audiobook_pipeline.stages.organize.copy_to_library")
    def test_dry_run_skips_copy(
        self,
        mock_copy,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        _setup_manifest_with_metadata(mock_manifest, "hash04", source_file)

        run(source_file, "hash04", mock_config, mock_manifest, dry_run=True)

        data = mock_manifest.read("hash04")
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value
        mock_copy.assert_not_called()

    @patch("audiobook_pipeline.stages.organize.build_plex_path")
    def test_existing_file_skipped(
        self,
        mock_build_path,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        dest_dir = tmp_path / "library" / "_unsorted" / "book"
        dest_dir.mkdir(parents=True)
        # Dest file uses metadata title (Test Book), not source filename
        dest_file = dest_dir / "Test Book.m4b"
        dest_file.write_text("fake audio")

        mock_build_path.return_value = dest_dir

        _setup_manifest_with_metadata(mock_manifest, "hash05", source_file)

        run(source_file, "hash05", mock_config, mock_manifest, dry_run=False)

        data = mock_manifest.read("hash05")
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value

    def test_reads_metadata_from_manifest(
        self,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        """Verify organize reads pre-resolved metadata, not doing its own resolution."""
        source_file = tmp_path / "junk-filename.m4b"
        source_file.write_text("fake audio")

        _setup_manifest_with_metadata(
            mock_manifest,
            "hash06",
            source_file,
            author="Correct Author",
            title="Correct Title",
            series="Good Series",
            position="3",
        )

        with patch("audiobook_pipeline.stages.organize.copy_to_library") as mock_copy:
            with patch(
                "audiobook_pipeline.stages.organize.build_plex_path"
            ) as mock_build:
                mock_build.return_value = (
                    tmp_path / "library" / "Correct Author" / "Correct Title"
                )
                mock_copy.return_value = tmp_path / "library" / "out.m4b"

                run(source_file, "hash06", mock_config, mock_manifest, dry_run=False)

                # Verify build_plex_path received the pre-resolved metadata
                call_args = mock_build.call_args
                metadata_arg = call_args[0][1]
                assert metadata_arg["author"] == "Correct Author"
                assert metadata_arg["title"] == "Correct Title"
                assert metadata_arg["series"] == "Good Series"
                assert metadata_arg["position"] == "3"


class TestOrganizeWithIndex:
    """Test organize stage with LibraryIndex for batch operations."""

    @patch("audiobook_pipeline.stages.organize.build_plex_path")
    @patch("audiobook_pipeline.stages.organize.copy_to_library")
    def test_index_early_skip_existing_file(
        self,
        mock_copy,
        mock_build_path,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        source_file = tmp_path / "Great Book.m4b"
        source_file.write_text("fake audio")

        lib = mock_config.nfs_output_dir
        dest_dir = lib / "_unsorted" / "Great Book"
        dest_dir.mkdir(parents=True)
        (dest_dir / "Great Book.m4b").write_text("fake audio")

        mock_build_path.return_value = dest_dir
        index = LibraryIndex(lib)

        _setup_manifest_with_metadata(
            mock_manifest, "hash07", source_file, title="Great Book"
        )

        run(
            source_file,
            "hash07",
            mock_config,
            mock_manifest,
            dry_run=False,
            index=index,
        )

        data = mock_manifest.read("hash07")
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value
        mock_copy.assert_not_called()

    def test_cross_source_dedup(
        self,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        lib = mock_config.nfs_output_dir
        lib.mkdir(parents=True, exist_ok=True)
        index = LibraryIndex(lib)

        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake audio")

        index.mark_processed("book")

        _setup_manifest_with_metadata(mock_manifest, "hash08", source_file)

        run(
            source_file,
            "hash08",
            mock_config,
            mock_manifest,
            dry_run=False,
            index=index,
        )

        data = mock_manifest.read("hash08")
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value


class TestOrganizeStageOrder:
    """Verify that reorganize mode includes ASIN + METADATA stages."""

    def test_organize_mode_includes_asin_and_metadata(self):
        stages = STAGE_ORDER[PipelineMode.ORGANIZE]
        assert Stage.ASIN in stages
        assert Stage.METADATA in stages
        assert Stage.ORGANIZE in stages
        # ASIN must come before METADATA, METADATA before ORGANIZE
        assert stages.index(Stage.ASIN) < stages.index(Stage.METADATA)
        assert stages.index(Stage.METADATA) < stages.index(Stage.ORGANIZE)


class TestBuildLibraryFilename:
    """Test filename construction to avoid doubling."""

    def test_strips_series_from_title(self):
        from audiobook_pipeline.stages.organize import _build_library_filename

        # Source filename has series embedded: "The Wheel of Time Book 11 - Knife of Dreams.m4b"
        result = _build_library_filename(
            "The Wheel of Time Book 11 - Knife of Dreams.m4b",
            {
                "title": "The Wheel of Time Book 11 - Knife of Dreams",
                "series": "The Wheel of Time",
                "position": "11",
            },
        )
        assert result == "Book 11 - Knife of Dreams.m4b"

    def test_clean_title_no_doubling(self):
        from audiobook_pipeline.stages.organize import _build_library_filename

        # Normal case: title is already clean
        result = _build_library_filename(
            "source.m4b",
            {
                "title": "Knife of Dreams",
                "series": "The Wheel of Time",
                "position": "11",
            },
        )
        assert result == "Book 11 - Knife of Dreams.m4b"

    def test_no_series_no_prefix(self):
        from audiobook_pipeline.stages.organize import _build_library_filename

        result = _build_library_filename(
            "source.m4b",
            {"title": "Standalone Book", "series": "", "position": ""},
        )
        assert result == "Standalone Book.m4b"


class TestOrganizeReorganize:
    """Test reorganize mode (move instead of copy)."""

    @patch("audiobook_pipeline.stages.organize.move_in_library")
    def test_reorganize_uses_move(
        self,
        mock_move,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        lib = mock_config.nfs_output_dir
        lib.mkdir(parents=True, exist_ok=True)

        source_file = tmp_path / "John Smith - Great Book.m4b"
        source_file.write_text("fake audio")

        index = LibraryIndex(lib)

        dest_file = lib / "John Smith" / "Great Book" / source_file.name
        mock_move.return_value = dest_file

        _setup_manifest_with_metadata(
            mock_manifest,
            "hash09",
            source_file,
            author="John Smith",
            title="Great Book",
        )

        run(
            source_file,
            "hash09",
            mock_config,
            mock_manifest,
            dry_run=False,
            index=index,
            reorganize=True,
        )

        mock_move.assert_called_once()
        data = mock_manifest.read("hash09")
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value

    def test_reorganize_skips_correctly_placed(
        self,
        tmp_path,
        mock_config,
        mock_manifest,
    ):
        lib = mock_config.nfs_output_dir

        dest = lib / "_unsorted" / "book"
        dest.mkdir(parents=True)
        source_file = dest / "book.m4b"
        source_file.write_text("fake audio")

        index = LibraryIndex(lib)

        _setup_manifest_with_metadata(mock_manifest, "hash10", source_file)

        run(
            source_file,
            "hash10",
            mock_config,
            mock_manifest,
            dry_run=False,
            index=index,
            reorganize=True,
        )

        data = mock_manifest.read("hash10")
        assert data["stages"]["organize"]["status"] == StageStatus.COMPLETED.value
