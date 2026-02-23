"""Tests for cli.py -- Click CLI interface."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from audiobook_pipeline.cli import main
from audiobook_pipeline.models import PipelineMode


@pytest.fixture(autouse=True)
def _use_tmp_dirs(tmp_path, monkeypatch):
    """Point all directory config to tmp_path so tests don't need /var/lib permissions."""
    for var in (
        "WORK_DIR",
        "MANIFEST_DIR",
        "OUTPUT_DIR",
        "LOG_DIR",
        "ARCHIVE_DIR",
        "LOCK_DIR",
    ):
        monkeypatch.setenv(var, str(tmp_path / var.lower()))


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    """Prevent CLI from loading the project .env file."""
    monkeypatch.setattr("audiobook_pipeline.cli._find_config_file", lambda: None)


class TestHelpOutput:
    def test_help_flag(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Convert, enrich, and organize" in result.output
        assert "--mode" in result.output
        assert "--dry-run" in result.output
        assert "--asin" in result.output

    def test_mode_choices(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "convert" in result.output
        assert "enrich" in result.output
        assert "metadata" in result.output
        assert "organize" in result.output


class TestModeAutoDetect:
    @patch("audiobook_pipeline.cli.PipelineRunner")
    def test_directory_defaults_to_convert(self, mock_runner_cls, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, [str(tmp_path), "--dry-run"])
        assert result.exit_code == 0, result.output + str(result.exception or "")
        mode_arg = mock_runner_cls.call_args.kwargs.get("mode")
        assert mode_arg == PipelineMode.CONVERT

    @patch("audiobook_pipeline.cli.PipelineRunner")
    def test_m4b_file_defaults_to_enrich(self, mock_runner_cls, tmp_path):
        m4b = tmp_path / "book.m4b"
        m4b.write_bytes(b"\x00")
        runner = CliRunner()
        result = runner.invoke(main, [str(m4b), "--dry-run"])
        assert result.exit_code == 0, result.output + str(result.exception or "")
        mode_arg = mock_runner_cls.call_args.kwargs.get("mode")
        assert mode_arg == PipelineMode.ENRICH

    def test_unknown_extension_fails(self, tmp_path):
        txt = tmp_path / "notes.txt"
        txt.write_text("hello")
        runner = CliRunner()
        result = runner.invoke(main, [str(txt)])
        assert result.exit_code != 0
        assert "Cannot auto-detect mode" in result.output


class TestDryRun:
    @patch("audiobook_pipeline.cli.PipelineRunner")
    def test_dry_run_sets_config(self, mock_runner_cls, tmp_path, monkeypatch):
        """--dry-run flag propagates to PipelineConfig."""
        monkeypatch.delenv("DRY_RUN", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, [str(tmp_path), "--dry-run"])
        assert result.exit_code == 0, result.output + str(result.exception or "")
        config = mock_runner_cls.call_args.kwargs.get("config")
        assert config.dry_run is True


class TestLevelFlag:
    @patch("audiobook_pipeline.cli.PipelineRunner")
    def test_level_flag_overrides_config(self, mock_runner_cls, tmp_path, monkeypatch):
        monkeypatch.delenv("PIPELINE_LEVEL", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, [str(tmp_path), "--level", "simple", "--dry-run"])
        assert result.exit_code == 0, result.output + str(result.exception or "")
        config = mock_runner_cls.call_args.kwargs.get("config")
        assert config.pipeline_level == "simple"

    @patch("audiobook_pipeline.cli.PipelineRunner")
    def test_reorganize_forces_ai_level(self, mock_runner_cls, tmp_path, monkeypatch):
        monkeypatch.delenv("PIPELINE_LEVEL", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, [str(tmp_path), "--reorganize", "--dry-run"])
        assert result.exit_code == 0, result.output + str(result.exception or "")
        config = mock_runner_cls.call_args.kwargs.get("config")
        # --reorganize forces level to "ai" or "full"
        assert config.pipeline_level in ("ai", "full")

    @patch("audiobook_pipeline.cli.PipelineRunner")
    def test_ai_all_forces_ai_level(self, mock_runner_cls, tmp_path, monkeypatch):
        monkeypatch.delenv("PIPELINE_LEVEL", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, [str(tmp_path), "--ai-all", "--dry-run"])
        assert result.exit_code == 0, result.output + str(result.exception or "")
        config = mock_runner_cls.call_args.kwargs.get("config")
        # --ai-all forces level to "ai" or "full"
        assert config.pipeline_level in ("ai", "full")

    @patch("audiobook_pipeline.cli.PipelineRunner")
    def test_simple_level_disables_ai(self, mock_runner_cls, tmp_path, monkeypatch):
        monkeypatch.delenv("PIPELINE_LEVEL", raising=False)
        monkeypatch.delenv("AI_ALL", raising=False)
        runner = CliRunner()
        result = runner.invoke(main, [str(tmp_path), "--level", "simple", "--dry-run"])
        assert result.exit_code == 0, result.output + str(result.exception or "")
        config = mock_runner_cls.call_args.kwargs.get("config")
        assert config.ai_all is False
