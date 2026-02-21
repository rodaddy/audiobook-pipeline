"""Tests for loguru-based pipeline logging."""

import sys
from io import StringIO
from pathlib import Path

from loguru import logger

from audiobook_pipeline.config import PipelineConfig


class TestSetupLogging:
    def setup_method(self):
        logger.remove()

    def test_setup_creates_log_dir(self, tmp_path, monkeypatch):
        for var in ["WORK_DIR", "MANIFEST_DIR", "OUTPUT_DIR", "LOG_DIR",
                     "ARCHIVE_DIR", "LOCK_DIR", "NFS_OUTPUT_DIR"]:
            monkeypatch.delenv(var, raising=False)
        log_dir = tmp_path / "logs"
        config = PipelineConfig(_env_file=None, log_dir=log_dir)
        config.setup_logging()
        assert log_dir.exists()

    def test_setup_adds_file_sink(self, tmp_path, monkeypatch):
        for var in ["WORK_DIR", "MANIFEST_DIR", "OUTPUT_DIR", "LOG_DIR",
                     "ARCHIVE_DIR", "LOCK_DIR", "NFS_OUTPUT_DIR"]:
            monkeypatch.delenv(var, raising=False)
        log_dir = tmp_path / "logs"
        config = PipelineConfig(_env_file=None, log_dir=log_dir)
        config.setup_logging()
        logger.bind(stage="test").info("hello from test")
        log_file = log_dir / "pipeline.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "hello from test" in content

    def test_stage_context_in_output(self, tmp_path, monkeypatch):
        for var in ["WORK_DIR", "MANIFEST_DIR", "OUTPUT_DIR", "LOG_DIR",
                     "ARCHIVE_DIR", "LOCK_DIR", "NFS_OUTPUT_DIR"]:
            monkeypatch.delenv(var, raising=False)
        log_dir = tmp_path / "logs"
        config = PipelineConfig(_env_file=None, log_dir=log_dir)
        config.setup_logging()
        logger.bind(stage="organize").info("organizing")
        content = (log_dir / "pipeline.log").read_text()
        assert "organize" in content

    def test_default_stage_empty(self, tmp_path, monkeypatch):
        for var in ["WORK_DIR", "MANIFEST_DIR", "OUTPUT_DIR", "LOG_DIR",
                     "ARCHIVE_DIR", "LOCK_DIR", "NFS_OUTPUT_DIR"]:
            monkeypatch.delenv(var, raising=False)
        log_dir = tmp_path / "logs"
        config = PipelineConfig(_env_file=None, log_dir=log_dir)
        config.setup_logging()
        logger.info("no stage bound")
        content = (log_dir / "pipeline.log").read_text()
        assert "no stage bound" in content
