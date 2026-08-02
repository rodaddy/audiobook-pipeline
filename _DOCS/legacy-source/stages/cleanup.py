"""Cleanup stage -- removes work_dir temp files after conversion."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from ..models import Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..pipeline_db import PipelineDB

log = logger.bind(stage="cleanup")


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: PipelineDB,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs,
) -> None:
    """Cleanup stage -- removes temporary work directory for this book.

    In convert mode, removes work_dir/book_hash (contains audio_files.txt,
    files.txt, metadata.txt, and output/ directory after the m4b has been
    moved to the library).
    For organize mode, this is a no-op.
    """
    work_book_dir = config.work_dir / book_hash

    if work_book_dir.exists() and config.cleanup_work_dir:
        if dry_run:
            log.info(f"[DRY-RUN] Would remove work dir: {work_book_dir}")
        else:
            shutil.rmtree(work_book_dir)
            log.debug(f"Removed work dir: {work_book_dir}")
    else:
        log.debug("Cleanup stage (no work dir to clean)")

    manifest.set_stage(book_hash, Stage.CLEANUP, StageStatus.COMPLETED)
