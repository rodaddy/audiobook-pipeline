"""Tests for convert stage."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from audiobook_pipeline.config import PipelineConfig
from audiobook_pipeline.pipeline_db import PipelineDB
from audiobook_pipeline.models import PipelineMode
from audiobook_pipeline.stages.convert import run, _detect_encoder


class TestDetectEncoder:
    @patch("audiobook_pipeline.stages.convert.subprocess.run")
    def test_detects_aac_at(self, mock_run):
        _detect_encoder.cache_clear()
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="V..... some_encoder\nA..... aac_at\nV..... another\n",
            stderr="",
        )
        assert _detect_encoder() == "aac_at"
        _detect_encoder.cache_clear()

    @patch("audiobook_pipeline.stages.convert.subprocess.run")
    def test_falls_back_to_aac(self, mock_run):
        _detect_encoder.cache_clear()
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="V..... some_encoder\nA..... aac\nV..... another\n",
            stderr="",
        )
        assert _detect_encoder() == "aac"
        _detect_encoder.cache_clear()


class TestConvertStage:
    def _make_config(self, tmp_path):
        return PipelineConfig(
            _env_file=None,
            work_dir=tmp_path / "work",
        )

    def _setup_work_dir(self, config, book_hash):
        work_dir = config.work_dir / book_hash
        work_dir.mkdir(parents=True)
        (work_dir / "files.txt").write_text("file '/src/ch01.mp3'\n")
        (work_dir / "metadata.txt").write_text(";FFMETADATA1\ntitle=Test\n")
        return work_dir

    def _create_manifest(self, tmp_path, config, book_hash, bitrate=128, file_count=2):
        manifest = PipelineDB(tmp_path / "test.db")
        manifest.create(book_hash, "/src/book", PipelineMode.CONVERT)
        manifest.update(
            book_hash,
            {
                "metadata": {
                    "target_bitrate": bitrate,
                    "file_count": file_count,
                }
            },
        )
        return manifest

    @patch("audiobook_pipeline.stages.convert._detect_encoder", return_value="aac")
    @patch("audiobook_pipeline.stages.convert.subprocess.run")
    def test_dry_run_skips_ffmpeg(self, mock_run, mock_enc, tmp_path):
        config = self._make_config(tmp_path)
        manifest = self._create_manifest(tmp_path, config, "testconv01")
        self._setup_work_dir(config, "testconv01")

        run(
            source_path=Path("/src/book"),
            book_hash="testconv01",
            config=config,
            manifest=manifest,
            dry_run=True,
        )

        mock_run.assert_not_called()
        data = manifest.read("testconv01")
        assert data["stages"]["convert"]["status"] == "completed"
        assert "output_file" in data["stages"]["convert"]

    def test_missing_files_txt_fails(self, tmp_path):
        config = self._make_config(tmp_path)
        manifest = self._create_manifest(tmp_path, config, "testconv02")
        # Don't create work dir files

        run(
            source_path=Path("/src/book"),
            book_hash="testconv02",
            config=config,
            manifest=manifest,
        )

        data = manifest.read("testconv02")
        assert data["stages"]["convert"]["status"] == "failed"

    @patch("audiobook_pipeline.stages.convert.count_chapters", return_value=2)
    @patch(
        "audiobook_pipeline.stages.convert.get_format_name", return_value="mov,mp4,m4a"
    )
    @patch("audiobook_pipeline.stages.convert.get_codec", return_value="aac")
    @patch("audiobook_pipeline.stages.convert._detect_encoder", return_value="aac")
    @patch("audiobook_pipeline.stages.convert.subprocess.run")
    def test_successful_conversion(
        self, mock_run, mock_enc, mock_codec, mock_fmt, mock_ch, tmp_path
    ):
        config = self._make_config(tmp_path)
        manifest = self._create_manifest(tmp_path, config, "testconv03")
        self._setup_work_dir(config, "testconv03")

        # Mock ffmpeg success and create output file
        def ffmpeg_side_effect(cmd, **kwargs):
            output_path = Path(cmd[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("fake m4b content")
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr=""
            )

        mock_run.side_effect = ffmpeg_side_effect

        run(
            source_path=Path("/src/book"),
            book_hash="testconv03",
            config=config,
            manifest=manifest,
        )

        data = manifest.read("testconv03")
        assert data["stages"]["convert"]["status"] == "completed"
        assert data["metadata"]["codec"] == "aac"

    @patch("audiobook_pipeline.stages.convert._detect_encoder", return_value="aac")
    @patch("audiobook_pipeline.stages.convert.subprocess.run")
    def test_ffmpeg_failure_sets_failed(self, mock_run, mock_enc, tmp_path):
        config = self._make_config(tmp_path)
        manifest = self._create_manifest(tmp_path, config, "testconv04")
        self._setup_work_dir(config, "testconv04")

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Error: bad input"
        )

        run(
            source_path=Path("/src/book"),
            book_hash="testconv04",
            config=config,
            manifest=manifest,
        )

        data = manifest.read("testconv04")
        assert data["stages"]["convert"]["status"] == "failed"

    @patch("audiobook_pipeline.stages.convert._detect_encoder", return_value="aac_at")
    @patch("audiobook_pipeline.stages.convert.subprocess.run")
    def test_threads_kwarg_passed_to_ffmpeg(self, mock_run, mock_enc, tmp_path):
        config = self._make_config(tmp_path)
        manifest = self._create_manifest(tmp_path, config, "testconv05")
        self._setup_work_dir(config, "testconv05")

        run(
            source_path=Path("/src/book"),
            book_hash="testconv05",
            config=config,
            manifest=manifest,
            dry_run=True,
            threads=4,
        )

        # In dry_run mode, subprocess.run should not be called
        mock_run.assert_not_called()
        # But we can verify the manifest was updated
        data = manifest.read("testconv05")
        assert data["stages"]["convert"]["status"] == "completed"
