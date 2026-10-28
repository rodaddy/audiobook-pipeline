"""Build safe non-recursive source signatures for the watch inbox."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.book import SOURCE_EXTENSIONS
from audiobook_pipeline.models.watch import ObservationEntry

log = logger.bind(component="watch")


def candidate_entries(source: Path) -> tuple[ObservationEntry, ...] | None:
    """Return a non-recursive signature for one discovery-compatible candidate."""
    if source.name.startswith(".") or source.is_symlink():
        return None
    if source.is_file():
        return _file_entry(source)
    if not source.is_dir():
        return None
    try:
        children = tuple(sorted(source.iterdir(), key=lambda path: path.name.lower()))
    except OSError:
        log.warning("Watch candidate observation failed: directory_unavailable")
        return None
    if any(child.is_symlink() for child in children):
        return None
    entries = tuple(entry for child in children for entry in (_file_entry(child) or ()))
    return entries or None


def _file_entry(path: Path) -> tuple[ObservationEntry, ...] | None:
    """Build one direct source-audio signature entry without probing or recursion."""
    if path.name.startswith(".") or path.is_symlink() or not path.is_file():
        return None
    if path.suffix.lower() not in SOURCE_EXTENSIONS:
        return None
    try:
        stat = path.stat()
    except OSError:
        log.warning("Watch candidate observation failed: stat_unavailable")
        return None
    return (
        ObservationEntry(
            name=path.name, size_bytes=stat.st_size, modified_ns=stat.st_mtime_ns
        ),
    )
