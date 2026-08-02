"""Tests for logging setup: which sinks exist, and that they actually write."""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from audiobook_pipeline.config import LoggingSettings
from audiobook_pipeline.utils.logging_config import setup


class TestSinks:
    def test_file_sink_writes(self, tmp_path: Path) -> None:
        setup(LoggingSettings(file_sink=True, json_sink=False), log_dir=tmp_path)
        logger.bind(stage="convert").info("encoding a book")
        logger.remove()

        content = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
        assert "encoding a book" in content
        assert "convert" in content

    def test_log_dir_is_created(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "logs"
        setup(LoggingSettings(file_sink=True, json_sink=False), log_dir=target)
        logger.info("hello")
        logger.remove()
        assert target.is_dir()

    def test_json_sink_emits_parseable_objects(self, tmp_path: Path) -> None:
        """One JSON object per line, with the bound stage preserved in extra.

        The point of this sink is that a log platform can answer "which books
        failed at the convert stage" without a regex over human-formatted text,
        so the structured fields have to survive serialization.
        """
        setup(LoggingSettings(file_sink=False, json_sink=True), log_dir=tmp_path)
        logger.bind(stage="identify").warning("no ASIN match")
        logger.remove()

        lines = (tmp_path / "pipeline.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])["record"]
        assert record["message"] == "no ASIN match"
        assert record["level"]["name"] == "WARNING"
        assert record["extra"]["stage"] == "identify"

    def test_json_sink_off_by_default(self, tmp_path: Path) -> None:
        setup(LoggingSettings(), log_dir=tmp_path)
        logger.info("hello")
        logger.remove()
        assert not (tmp_path / "pipeline.jsonl").exists()

    def test_console_only_leaves_no_log_directory(self, tmp_path: Path) -> None:
        """A console-only run must not leave an empty logs/ folder behind."""
        target = tmp_path / "logs"
        setup(LoggingSettings(file_sink=False, json_sink=False), log_dir=target)
        logger.info("hello")
        logger.remove()
        assert not target.exists()


class TestStageBinding:
    def test_unbound_record_does_not_crash_the_formatter(self, tmp_path: Path) -> None:
        """A log call with no bound stage must still format.

        loguru raises KeyError inside its own formatter when the format string
        references an `extra` key the record lacks -- so a module that forgot
        to bind would take down the logging system while reporting an error,
        destroying the diagnostic it was called to produce.
        """
        setup(LoggingSettings(file_sink=True, json_sink=False), log_dir=tmp_path)
        logger.info("no stage bound here")
        logger.remove()

        content = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
        assert "no stage bound here" in content

    def test_bound_stage_appears_in_output(self, tmp_path: Path) -> None:
        setup(LoggingSettings(file_sink=True, json_sink=False), log_dir=tmp_path)
        logger.bind(stage="organize").info("filing book")
        logger.remove()

        content = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
        assert "organize" in content

    def test_file_sink_captures_debug_when_console_is_quiet(
        self, tmp_path: Path
    ) -> None:
        """The file sink is DEBUG regardless of the console level.

        The file exists for the investigation that happens after the console
        scrollback is gone; matching it to the console level would discard
        exactly the detail that investigation needs.
        """
        setup(
            LoggingSettings(level="CRITICAL", file_sink=True, json_sink=False),
            log_dir=tmp_path,
        )
        logger.bind(stage="probe").debug("ffprobe returned 160 chapters")
        logger.remove()

        content = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
        assert "160 chapters" in content


class TestIdempotence:
    def test_calling_setup_twice_does_not_double_lines(self, tmp_path: Path) -> None:
        """setup() replaces sinks rather than adding to them."""
        setup(LoggingSettings(file_sink=True, json_sink=False), log_dir=tmp_path)
        setup(LoggingSettings(file_sink=True, json_sink=False), log_dir=tmp_path)
        logger.bind(stage="convert").info("once")
        logger.remove()

        content = (tmp_path / "pipeline.log").read_text(encoding="utf-8")
        assert content.count("once") == 1
