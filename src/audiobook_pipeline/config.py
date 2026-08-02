"""Pipeline configuration via pydantic-settings (.env + env vars)."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from .models import PipelineLevel

# Suppress pydantic-settings toml_file warning until we wire up the source hook
warnings.filterwarnings(
    "ignore",
    message=".*toml_file.*TomlConfigSettingsSource.*",
    module="pydantic_settings",
)


class PipelineConfig(BaseSettings):
    """All pipeline configuration with layered resolution:
    .env file < environment variables < constructor kwargs.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # -- Directories --
    #
    # SELF-CONTAINED BY DEFAULT. Every path below lives under DATA_DIR, which
    # defaults to ./data relative to the working directory. Clone the repo, run
    # it, and nothing is written outside that folder.
    #
    # These used to default to /var/lib/audiobook-pipeline, /var/log, and
    # /mnt/media/AudioBooks -- all 11 path defaults pointed outside the project.
    # On any machine where the user is not root, and on every Mac, a fresh clone
    # failed on permissions before converting a single book. System paths are a
    # DEPLOYMENT choice (see examples/config/server-daemon.env), not a
    # sensible default for someone trying the tool.
    data_dir: Path = Path("data")

    work_dir: Path = Path("data/work")
    output_dir: Path = Path("data/output")
    log_dir: Path = Path("data/logs")
    archive_dir: Path = Path("data/archive")
    lock_dir: Path = Path("data/locks")

    # Where finished books are filed as Author/Series/Title. Defaults inside
    # the project so a first run is safe; point it at your real library when
    # you are ready. Named nfs_* for historical reasons -- it need not be NFS.
    nfs_output_dir: Path = Path("data/library")

    # -- Encoding --
    max_bitrate: int = 128
    channels: int = 1
    codec: str = "aac"

    # -- Permissions --
    file_owner: str = ""
    file_mode: str = "644"
    dir_mode: str = "755"

    # -- Parallel conversion --
    max_parallel_converts: int = 0  # 0 = auto (CPU-based)
    cpu_ceiling: float = 80.0

    # -- Behavior --
    dry_run: bool = False
    force: bool = False
    verbose: bool = False
    cleanup_work_dir: bool = True
    log_level: str = "INFO"
    # Structured JSON sink alongside the console and rotating file. Off by
    # default: useful for an unattended server, noise for a desktop run.
    log_json: bool = False

    # -- Metadata --
    metadata_source: str = "audible"
    audible_region: str = "com"
    audnexus_region: str = "us"
    audnexus_cache_dir: str = ""
    audnexus_cache_days: int = 30
    chapter_duration_tolerance: int = 5
    metadata_skip: bool = False
    force_metadata: bool = False

    # -- Archive --
    archive_retention_days: int = 90

    # -- Automation --
    # Watch-folder workflow. Self-contained like the rest; a server install
    # overrides these to system paths (examples/config/server-daemon.env).
    incoming_dir: Path = Path("data/incoming")
    queue_dir: Path = Path("data/queue")
    processing_dir: Path = Path("data/processing")
    completed_dir: Path = Path("data/completed")
    failed_dir: Path = Path("data/failed")
    # Resolved from PATH by default rather than assuming an install prefix.
    pipeline_bin: str = "audiobook-convert"
    stability_threshold: int = 120

    # -- Error recovery --
    max_retries: int = 3
    failure_webhook_url: str = ""
    failure_email: str = ""

    # -- Pipeline level --
    pipeline_level: str = "normal"

    # -- AI (uses PIPELINE_LLM_* env vars to avoid OPENAI_* collisions) --
    pipeline_llm_base_url: str = ""
    pipeline_llm_api_key: str = ""
    pipeline_llm_model: str = "haiku"
    ai_all: bool = False
    asin_search_threshold: int = 65

    @property
    def level(self) -> PipelineLevel:
        """Parsed pipeline level enum from the pipeline_level string."""
        from .models import PipelineLevel

        return PipelineLevel(self.pipeline_level)

    @property
    def db_path(self) -> Path:
        """Path to the SQLite pipeline database."""
        return self.work_dir / "pipeline.db"

    def ensure_dirs(self) -> None:
        """Create all required directories if they don't exist."""
        for d in (
            self.work_dir,
            self.output_dir,
            self.log_dir,
            self.archive_dir,
            self.lock_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def setup_cli_logging(self, verbose: bool = False, *, stages: tuple = ()) -> None:
        """Configure logging for a short-lived CLI command.

        Console only, no log file: `audiobook-audit` reports to the terminal and
        should not leave a rotating log behind for a read-only query.

        Lives here because config owns logging -- ALL of it. cli_audit.py used
        to call logger.remove()/logger.add() itself, which is the exact pattern
        the standard forbids: a second place that decides where log lines go, so
        changing the format means finding every caller that reimplemented it.

        Args:
            verbose: DEBUG instead of INFO.
            stages: When given, only records bound to these stage names are
                shown, keeping unrelated pipeline chatter out of a report.
        """
        logger.remove()

        def _filter(record: dict) -> bool:
            record["extra"].setdefault("stage", "")
            if not stages:
                return True
            return record["extra"].get("stage", "") in stages

        logger.add(
            lambda msg: click.echo(msg, err=True),
            format="{level:<8} | {message}",
            level="DEBUG" if verbose else "INFO",
            filter=_filter,
        )

    def setup_logging(self) -> None:
        """Configure loguru for the pipeline."""
        logger.remove()  # Remove default stderr handler

        log_format = (
            "{time:YYYY-MM-DDTHH:mm:ssZ} | {level:<8} | {extra[stage]:<12} | {message}"
        )

        def _default_extra(record):
            record["extra"].setdefault("stage", "")
            return True

        logger.add(
            sys.stderr,
            format=log_format,
            level=self.log_level.upper(),
            filter=_default_extra,
        )

        self.log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(self.log_dir / "pipeline.log"),
            format=log_format,
            level="DEBUG",
            rotation="10 MB",
            retention="30 days",
            filter=_default_extra,
        )

        # Structured JSON, one object per line.
        #
        # The third required sink (_DOCS/STANDARDS-python.md ## Logging): the
        # console is what you watch, the plain file is what you tail, and this
        # is what a log platform ingests. A human-formatted line has to be
        # re-parsed with a regex to answer "which books failed at the convert
        # stage last week"; these records already carry the fields.
        #
        # Off by default -- a desktop user converting a book does not need a
        # second log file. Enabled with LOG_JSON=true for unattended installs.
        if self.log_json:
            logger.add(
                str(self.log_dir / "pipeline.jsonl"),
                level="DEBUG",
                rotation="10 MB",
                retention="30 days",
                serialize=True,
                filter=_default_extra,
            )
