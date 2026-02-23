"""Pipeline configuration via pydantic-settings (.env + env vars)."""

import sys
import warnings
from pathlib import Path

from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    work_dir: Path = Path("/var/lib/audiobook-pipeline/work")
    output_dir: Path = Path("/var/lib/audiobook-pipeline/output")
    log_dir: Path = Path("/var/log/audiobook-pipeline")
    archive_dir: Path = Path("/var/lib/audiobook-pipeline/archive")
    lock_dir: Path = Path("/var/lib/audiobook-pipeline/locks")
    nfs_output_dir: Path = Path("/mnt/media/AudioBooks")

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
    incoming_dir: Path = Path("/mnt/media/AudioBooks/_incoming")
    queue_dir: Path = Path("/var/lib/audiobook-pipeline/queue")
    processing_dir: Path = Path("/var/lib/audiobook-pipeline/processing")
    completed_dir: Path = Path("/var/lib/audiobook-pipeline/completed")
    failed_dir: Path = Path("/var/lib/audiobook-pipeline/failed")
    pipeline_bin: str = "/opt/audiobook-pipeline/bin/audiobook-convert"
    stability_threshold: int = 120

    # -- Error recovery --
    max_retries: int = 3
    failure_webhook_url: str = ""
    failure_email: str = ""

    # -- AI (uses PIPELINE_LLM_* env vars to avoid OPENAI_* collisions) --
    pipeline_llm_base_url: str = ""
    pipeline_llm_api_key: str = ""
    pipeline_llm_model: str = "haiku"
    ai_all: bool = False
    asin_search_threshold: int = 65

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

    def setup_logging(self) -> None:
        """Configure loguru for the pipeline."""
        logger.remove()  # Remove default stderr handler

        log_format = (
            "{time:YYYY-MM-DDTHH:mm:ssZ} | {level:<8} | "
            "{extra[stage]:<12} | {message}"
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
