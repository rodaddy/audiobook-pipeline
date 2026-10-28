"""Remove only one completed book's scratch directory."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.lifecycle import CleanupResult

log = logger.bind(stage="cleanup")


class CleanupError(ValueError):
    """A requested cleanup target is not one book's scratch directory."""

    def __init__(self, path: str | Path) -> None:
        """Build an error that identifies the unsafe cleanup path."""
        super().__init__(f"cleanup target is outside the book work directory: {path}")


def _book_work_dir(work_root: Path, book_hash: str) -> Path:
    """Resolve and confine the cleanup target to one direct child of work root."""
    if Path(book_hash).name != book_hash:
        raise CleanupError(book_hash)
    root = work_root.resolve()
    target = (root / book_hash).resolve()
    if target.parent != root:
        raise CleanupError(target)
    return target


def cleanup_work_dir(
    work_root: Path, book_hash: str, *, enabled: bool, dry_run: bool
) -> CleanupResult:
    """Remove a completed book's scratch tree when cleanup is enabled."""
    target = _book_work_dir(work_root, book_hash)
    if not enabled or dry_run or not target.exists():
        return CleanupResult(work_dir=target, removed=False, attempted=False)
    shutil.rmtree(target)
    log.info("removed scratch directory {}", target)
    return CleanupResult(work_dir=target, removed=True, attempted=True)
