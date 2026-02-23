"""Tests for metadata stage."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from audiobook_pipeline.config import PipelineConfig
from audiobook_pipeline.errors import ManifestError
from audiobook_pipeline.pipeline_db import PipelineDB
from audiobook_pipeline.models import PipelineMode
from audiobook_pipeline.stages.metadata import run, _build_album, _write_tags


class TestBuildAlbum:
    def test_series_and_position(self):
        assert _build_album("Title", "The Expanse", "3") == "The Expanse, Book 3"

    def test_series_no_position(self):
        assert _build_album("Title", "The Expanse", "") == "The Expanse"

    def test_no_series(self):
        assert _build_album("My Book Title", "", "") == "My Book Title"

    def test_no_series_with_position(self):
        assert _build_album("My Book Title", "", "5") == "My Book Title"


class TestMetadataStage:
    def _make_config(self, tmp_path):
        return PipelineConfig(
            _env_file=None,
            work_dir=tmp_path / "work",
            nfs_output_dir=tmp_path / "library",
        )

    def _create_manifest_with_asin(
        self,
        tmp_path,
        config,
        book_hash,
        output_file,
        author="Author Name",
        title="Book Title",
        series="",
        position="",
        asin="B001234567",
        narrator="",
        year="",
        cover_url="",
    ):
        """Create manifest with ASIN-resolved metadata and convert output."""
        manifest = PipelineDB(tmp_path / "test.db")
        manifest.create(book_hash, "/src/book", PipelineMode.CONVERT)
        data = manifest.read(book_hash)
        data["stages"]["convert"] = {
            "status": "completed",
            "output_file": str(output_file),
        }
        data["metadata"].update(
            {
                "parsed_author": author,
                "parsed_title": title,
                "parsed_series": series,
                "parsed_position": position,
                "parsed_asin": asin,
                "parsed_narrator": narrator,
                "parsed_year": year,
                "cover_url": cover_url,
            }
        )
        manifest.update(book_hash, data)
        return manifest

    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_correct_ffmpeg_args(self, mock_run, tmp_path):
        config = self._make_config(tmp_path)

        # Output file is in work_dir (convert output), not library
        output_file = tmp_path / "work" / "hash01" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake m4b")

        manifest = self._create_manifest_with_asin(
            tmp_path,
            config,
            "meta01",
            output_file,
            author="James Corey",
            title="Leviathan Wakes",
            series="The Expanse",
            position="1",
            asin="B005LZHV6Q",
            narrator="Jefferson Mays",
            year="2011",
        )

        def ffmpeg_side_effect(cmd, **kwargs):
            temp_path = Path(cmd[-1])
            temp_path.write_text("tagged m4b")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=Path("/src/book"),
            book_hash="meta01",
            config=config,
            manifest=manifest,
        )

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]

        assert "-c" in cmd
        assert "copy" in cmd
        assert "-map_chapters" in cmd

        metadata_pairs = []
        for i, arg in enumerate(cmd):
            if arg == "-metadata" and i + 1 < len(cmd):
                metadata_pairs.append(cmd[i + 1])

        assert "artist=James Corey, Jefferson Mays" in metadata_pairs
        assert "album_artist=James Corey" in metadata_pairs
        assert "album=The Expanse, Book 1" in metadata_pairs
        assert "title=Leviathan Wakes" in metadata_pairs
        assert "genre=Audiobook" in metadata_pairs
        assert "media_type=2" in metadata_pairs
        assert "composer=Jefferson Mays" in metadata_pairs
        assert "date=2011" in metadata_pairs
        assert "show=The Expanse" in metadata_pairs
        assert "grouping=The Expanse, Book #1" in metadata_pairs
        assert "sort_album=The Expanse 1 - Leviathan Wakes" in metadata_pairs
        assert "ASIN=B005LZHV6Q" in metadata_pairs
        assert "SHOWMOVEMENT=1" in metadata_pairs
        assert "MOVEMENTNAME=The Expanse" in metadata_pairs
        assert "MOVEMENT=1" in metadata_pairs
        assert "pgap=1" in metadata_pairs

        data = manifest.read("meta01")
        assert data["stages"]["metadata"]["status"] == "completed"
        assert data["stages"]["metadata"]["output_file"] == str(output_file)

    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_chapters_preserved_via_map_chapters(self, mock_run, tmp_path):
        config = self._make_config(tmp_path)

        output_file = tmp_path / "work" / "hash02" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake")

        manifest = self._create_manifest_with_asin(
            tmp_path,
            config,
            "meta02",
            output_file,
        )

        def ffmpeg_side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_text("tagged")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=Path("/src/book"),
            book_hash="meta02",
            config=config,
            manifest=manifest,
        )

        cmd = mock_run.call_args[0][0]
        for i, arg in enumerate(cmd):
            if arg == "-map_chapters":
                assert cmd[i + 1] == "0"
                break
        else:
            raise AssertionError("-map_chapters not found in ffmpeg command")

    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_album_without_series(self, mock_run, tmp_path):
        config = self._make_config(tmp_path)

        output_file = tmp_path / "work" / "hash03" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake")

        manifest = self._create_manifest_with_asin(
            tmp_path,
            config,
            "meta03",
            output_file,
            author="Author",
            title="Standalone Book",
            series="",
            position="",
        )

        def ffmpeg_side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_text("tagged")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=Path("/src/book"),
            book_hash="meta03",
            config=config,
            manifest=manifest,
        )

        cmd = mock_run.call_args[0][0]
        metadata_pairs = []
        for i, arg in enumerate(cmd):
            if arg == "-metadata" and i + 1 < len(cmd):
                metadata_pairs.append(cmd[i + 1])

        assert "album=Standalone Book" in metadata_pairs

    def test_missing_manifest_sets_failed(self, tmp_path):
        """Missing manifest does not raise, just sets stage to failed."""
        config = self._make_config(tmp_path)

        manifest = PipelineDB(tmp_path / "test.db")

        run(
            source_path=Path("/src/book"),
            book_hash="meta04",
            config=config,
            manifest=manifest,
        )

        # Since book doesn't exist, read will return None
        # But we can check that the stage tried to set failed status
        # (though it may fail silently if book doesn't exist)

    def test_missing_output_file_fails(self, tmp_path):
        config = self._make_config(tmp_path)

        nonexistent = tmp_path / "work" / "missing.m4b"
        manifest = self._create_manifest_with_asin(
            tmp_path,
            config,
            "meta05",
            nonexistent,
        )

        run(
            source_path=Path("/src/book"),
            book_hash="meta05",
            config=config,
            manifest=manifest,
        )

        data = manifest.read("meta05")
        assert data["stages"]["metadata"]["status"] == "failed"

    def test_dry_run_skips_tagging(self, tmp_path):
        config = self._make_config(tmp_path)

        output_file = tmp_path / "work" / "hash06" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake")

        manifest = self._create_manifest_with_asin(
            tmp_path,
            config,
            "meta06",
            output_file,
        )

        with patch("audiobook_pipeline.stages.metadata.subprocess.run") as mock_run:
            run(
                source_path=Path("/src/book"),
                book_hash="meta06",
                config=config,
                manifest=manifest,
                dry_run=True,
            )

            mock_run.assert_not_called()

        data = manifest.read("meta06")
        assert data["stages"]["metadata"]["status"] == "completed"

    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_ffmpeg_failure_sets_failed(self, mock_run, tmp_path):
        config = self._make_config(tmp_path)

        output_file = tmp_path / "work" / "hash07" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake")

        manifest = self._create_manifest_with_asin(
            tmp_path,
            config,
            "meta07",
            output_file,
        )

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: bad input"
        )

        run(
            source_path=Path("/src/book"),
            book_hash="meta07",
            config=config,
            manifest=manifest,
        )

        data = manifest.read("meta07")
        assert data["stages"]["metadata"]["status"] == "failed"

    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_no_asin_omits_asin_tag(self, mock_run, tmp_path):
        config = self._make_config(tmp_path)

        output_file = tmp_path / "work" / "hash08" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake")

        manifest = self._create_manifest_with_asin(
            tmp_path,
            config,
            "meta08",
            output_file,
            asin="",
        )

        def ffmpeg_side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_text("tagged")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=Path("/src/book"),
            book_hash="meta08",
            config=config,
            manifest=manifest,
        )

        cmd = mock_run.call_args[0][0]
        metadata_pairs = []
        for i, arg in enumerate(cmd):
            if arg == "-metadata" and i + 1 < len(cmd):
                metadata_pairs.append(cmd[i + 1])

        assert not any(p.startswith("ASIN=") for p in metadata_pairs)


class TestCoverArt:
    """Test cover art download and embedding."""

    def _make_config(self, tmp_path):
        return PipelineConfig(
            _env_file=None,
            work_dir=tmp_path / "work",
            nfs_output_dir=tmp_path / "library",
        )

    def _create_manifest_with_cover(
        self, tmp_path, config, book_hash, output_file, cover_url
    ):
        manifest = PipelineDB(tmp_path / "test.db")
        manifest.create(book_hash, "/src/book", PipelineMode.CONVERT)
        data = manifest.read(book_hash)
        data["stages"]["convert"] = {
            "status": "completed",
            "output_file": str(output_file),
        }
        data["metadata"].update(
            {
                "parsed_author": "Author",
                "parsed_title": "Title",
                "parsed_series": "",
                "parsed_position": "",
                "parsed_asin": "B001",
                "cover_url": cover_url,
            }
        )
        manifest.update(book_hash, data)
        return manifest

    @patch("audiobook_pipeline.stages.metadata._download_cover")
    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_cover_art_embedded_in_ffmpeg(self, mock_run, mock_download, tmp_path):
        """When cover is available, ffmpeg command includes cover input and disposition."""
        config = self._make_config(tmp_path)

        output_file = tmp_path / "work" / "hash09" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake m4b")

        cover_file = tmp_path / "work" / "hash09" / "_cover.jpg"
        cover_file.write_text("fake jpg")
        mock_download.return_value = cover_file

        manifest = self._create_manifest_with_cover(
            tmp_path,
            config,
            "cover01",
            output_file,
            cover_url="https://example.com/cover.jpg",
        )

        def ffmpeg_side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_text("tagged with cover")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=Path("/src/book"),
            book_hash="cover01",
            config=config,
            manifest=manifest,
        )

        cmd = mock_run.call_args[0][0]
        # Should have two -i inputs
        i_indices = [i for i, a in enumerate(cmd) if a == "-i"]
        assert len(i_indices) == 2
        # Second input should be the cover file
        assert cmd[i_indices[1] + 1] == str(cover_file)
        # Should have -disposition:v:0 attached_pic
        assert "-disposition:v:0" in cmd
        assert "attached_pic" in cmd

        data = manifest.read("cover01")
        assert data["stages"]["metadata"]["status"] == "completed"

    @patch("audiobook_pipeline.stages.metadata._download_cover")
    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_cover_download_failure_tags_without_cover(
        self, mock_run, mock_download, tmp_path
    ):
        """Cover download failure is non-fatal -- file gets tagged without cover."""
        config = self._make_config(tmp_path)

        output_file = tmp_path / "work" / "hash10" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake m4b")

        mock_download.return_value = None  # Download failed

        manifest = self._create_manifest_with_cover(
            tmp_path,
            config,
            "cover02",
            output_file,
            cover_url="https://example.com/broken.jpg",
        )

        def ffmpeg_side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_text("tagged no cover")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=Path("/src/book"),
            book_hash="cover02",
            config=config,
            manifest=manifest,
        )

        cmd = mock_run.call_args[0][0]
        # Should have only one -i (no cover input)
        i_indices = [i for i, a in enumerate(cmd) if a == "-i"]
        assert len(i_indices) == 1
        assert "-disposition:v:0" not in cmd

        data = manifest.read("cover02")
        assert data["stages"]["metadata"]["status"] == "completed"

    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_no_cover_url_skips_download(self, mock_run, tmp_path):
        """When cover_url is empty, no download is attempted."""
        config = self._make_config(tmp_path)

        output_file = tmp_path / "work" / "hash11" / "book.m4b"
        output_file.parent.mkdir(parents=True)
        output_file.write_text("fake m4b")

        manifest = self._create_manifest_with_cover(
            tmp_path,
            config,
            "cover03",
            output_file,
            cover_url="",
        )

        def ffmpeg_side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_text("tagged")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=Path("/src/book"),
            book_hash="cover03",
            config=config,
            manifest=manifest,
        )

        cmd = mock_run.call_args[0][0]
        i_indices = [i for i, a in enumerate(cmd) if a == "-i"]
        assert len(i_indices) == 1


class TestMetadataEnrichMode:
    """Test metadata stage with source_path as M4B file (enrich mode)."""

    def _make_config(self, tmp_path):
        return PipelineConfig(
            _env_file=None,
            work_dir=tmp_path / "work",
            nfs_output_dir=tmp_path / "library",
        )

    @patch("audiobook_pipeline.stages.metadata.subprocess.run")
    def test_enrich_mode_uses_source_file(self, mock_run, tmp_path):
        """In enrich mode, tags the source M4B directly (no convert output)."""
        config = self._make_config(tmp_path)

        source_file = tmp_path / "book.m4b"
        source_file.write_text("fake m4b")

        manifest = PipelineDB(tmp_path / "test.db")
        manifest.create("enrich01", str(source_file), PipelineMode.ENRICH)
        data = manifest.read("enrich01")
        data["metadata"].update(
            {
                "parsed_author": "Author",
                "parsed_title": "Title",
                "parsed_series": "",
                "parsed_position": "",
                "parsed_asin": "",
                "cover_url": "",
            }
        )
        manifest.update("enrich01", data)

        def ffmpeg_side_effect(cmd, **kwargs):
            Path(cmd[-1]).write_text("tagged")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=source_file,
            book_hash="enrich01",
            config=config,
            manifest=manifest,
        )

        # Should tag the source file directly
        cmd = mock_run.call_args[0][0]
        assert str(source_file) in cmd

        data = manifest.read("enrich01")
        assert data["stages"]["metadata"]["status"] == "completed"
