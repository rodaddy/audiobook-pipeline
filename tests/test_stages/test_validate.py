"""Tests for validate stage."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from audiobook_pipeline.config import PipelineConfig
from audiobook_pipeline.pipeline_db import PipelineDB
from audiobook_pipeline.models import PipelineMode, Stage, StageStatus
from audiobook_pipeline.stages.validate import run, _natural_sort_key


class TestNaturalSortKey:
    def test_numeric_ordering(self):
        paths = [Path("track10.mp3"), Path("track2.mp3"), Path("track1.mp3")]
        sorted_paths = sorted(paths, key=_natural_sort_key)
        assert [p.name for p in sorted_paths] == [
            "track1.mp3",
            "track2.mp3",
            "track10.mp3",
        ]

    def test_mixed_alpha_numeric(self):
        paths = [Path("Chapter 12.mp3"), Path("Chapter 2.mp3"), Path("Chapter 1.mp3")]
        sorted_paths = sorted(paths, key=_natural_sort_key)
        assert [p.name for p in sorted_paths] == [
            "Chapter 1.mp3",
            "Chapter 2.mp3",
            "Chapter 12.mp3",
        ]


class TestValidateStage:
    def _make_config(self, tmp_path):
        return PipelineConfig(
            _env_file=None,
            work_dir=tmp_path / "work",
        )

    def _make_source(self, tmp_path, files=None):
        src = tmp_path / "book"
        src.mkdir()
        if files is None:
            files = ["ch01.mp3", "ch02.mp3"]
        for f in files:
            (src / f).write_text("fake audio")
        return src

    @patch("audiobook_pipeline.stages.validate.get_duration", return_value=100.0)
    @patch("audiobook_pipeline.stages.validate.get_bitrate", return_value=128000)
    @patch("audiobook_pipeline.stages.validate.validate_audio_file", return_value=True)
    @patch("audiobook_pipeline.stages.validate.check_disk_space", return_value=True)
    def test_happy_path(self, mock_disk, mock_valid, mock_br, mock_dur, tmp_path):
        config = self._make_config(tmp_path)
        manifest = PipelineDB(tmp_path / "test.db")
        src = self._make_source(tmp_path)
        book_hash = "testhash123"
        manifest.create(book_hash, str(src), PipelineMode.CONVERT)

        run(source_path=src, book_hash=book_hash, config=config, manifest=manifest)

        data = manifest.read(book_hash)
        assert data["stages"]["validate"]["status"] == "completed"
        assert data["metadata"]["file_count"] == 2
        assert data["metadata"]["target_bitrate"] == 128
        assert data["metadata"]["total_duration"] == 200.0
        # Check audio_files.txt was written
        file_list = (config.work_dir / book_hash / "audio_files.txt").read_text()
        assert "ch01.mp3" in file_list
        assert "ch02.mp3" in file_list

    def test_not_a_directory(self, tmp_path):
        config = self._make_config(tmp_path)
        manifest = PipelineDB(tmp_path / "test.db")
        fake_file = tmp_path / "notadir.mp3"
        fake_file.write_text("x")
        book_hash = "testhash456"
        manifest.create(book_hash, str(fake_file), PipelineMode.CONVERT)

        run(
            source_path=fake_file, book_hash=book_hash, config=config, manifest=manifest
        )

        data = manifest.read(book_hash)
        assert data["stages"]["validate"]["status"] == "failed"

    def test_no_audio_files(self, tmp_path):
        config = self._make_config(tmp_path)
        manifest = PipelineDB(tmp_path / "test.db")
        src = self._make_source(tmp_path, files=["readme.txt"])
        book_hash = "testhash789"
        manifest.create(book_hash, str(src), PipelineMode.CONVERT)

        run(source_path=src, book_hash=book_hash, config=config, manifest=manifest)

        data = manifest.read(book_hash)
        assert data["stages"]["validate"]["status"] == "failed"

    @patch("audiobook_pipeline.stages.validate.get_duration", return_value=100.0)
    @patch("audiobook_pipeline.stages.validate.get_bitrate", return_value=256000)
    @patch("audiobook_pipeline.stages.validate.validate_audio_file", return_value=True)
    @patch("audiobook_pipeline.stages.validate.check_disk_space", return_value=True)
    def test_bitrate_capping(self, mock_disk, mock_valid, mock_br, mock_dur, tmp_path):
        config = self._make_config(tmp_path)
        manifest = PipelineDB(tmp_path / "test.db")
        src = self._make_source(tmp_path)
        book_hash = "testhashcap"
        manifest.create(book_hash, str(src), PipelineMode.CONVERT)

        run(source_path=src, book_hash=book_hash, config=config, manifest=manifest)

        data = manifest.read(book_hash)
        # max_bitrate default is 128, source is 256k, should cap
        assert data["metadata"]["target_bitrate"] == 128

    @patch("audiobook_pipeline.stages.validate.get_duration", return_value=100.0)
    @patch("audiobook_pipeline.stages.validate.get_bitrate", return_value=128000)
    @patch("audiobook_pipeline.stages.validate.validate_audio_file", return_value=True)
    @patch("audiobook_pipeline.stages.validate.check_disk_space", return_value=True)
    def test_dry_run_still_writes_file_list(
        self, mock_disk, mock_valid, mock_br, mock_dur, tmp_path
    ):
        config = self._make_config(tmp_path)
        manifest = PipelineDB(tmp_path / "test.db")
        src = self._make_source(tmp_path)
        book_hash = "testhashdry"
        manifest.create(book_hash, str(src), PipelineMode.CONVERT)

        run(
            source_path=src,
            book_hash=book_hash,
            config=config,
            manifest=manifest,
            dry_run=True,
        )

        # File list is always written (lightweight metadata for downstream stages)
        assert (config.work_dir / book_hash / "audio_files.txt").exists()
        data = manifest.read(book_hash)
        assert data["stages"]["validate"]["status"] == "completed"
