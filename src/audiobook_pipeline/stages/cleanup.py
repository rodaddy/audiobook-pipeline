"""Cleanup stage -- removes temporary files (stub for organize mode)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ..models import Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..manifest import Manifest

log = logger.bind(stage="cleanup")


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: Manifest,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Cleanup stage -- currently a no-op for organize-only mode.

    In convert mode, this would remove temporary files from work_dir.
    For organize mode, there are no temporary files to clean up.
    """
    log.debug("Cleanup stage (no-op for organize mode)")
    manifest.set_stage(book_hash, Stage.CLEANUP, StageStatus.COMPLETED)
