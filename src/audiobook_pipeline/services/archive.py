"""Archive original sources only after the finished M4B passes validation."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.lifecycle import ArchivedSource
from audiobook_pipeline.utils.ffmpeg import probe

log = logger.bind(stage="archive")


class ArchiveError(ValueError):
    """An original cannot safely move into the configured archive."""

    def __init__(self, path: Path) -> None:
        """Build an error that identifies the unsafe archive path."""
        super().__init__(f"cannot safely archive source at {path}")


class FinishedM4BError(FileNotFoundError):
    """The output gate rejected the file that would justify archiving."""

    def __init__(self, path: Path) -> None:
        """Build an error that identifies the final M4B that failed its gate."""
        super().__init__(f"finished M4B failed validation: {path}")


SIZE_TOLERANCE = 0.10


def _has_supported_container(format_name: str) -> bool:
    """Whether ffprobe reports an MP4-family container."""
    return "mov" in format_name or "mp4" in format_name


def _has_plausible_size(path: Path, duration_ms: int, bit_rate: int | None) -> bool:
    """Check file bytes against its duration and declared stream bit rate."""
    if bit_rate is None:
        return False
    expected = duration_ms * bit_rate / 8_000
    ratio = path.stat().st_size / expected if expected else 0
    return 1 - SIZE_TOLERANCE <= ratio <= 1 + SIZE_TOLERANCE


def validate_m4b(path: Path, *, source_duration_ms: int) -> None:
    """Require a plausible AAC M4B matching the original source duration."""
    if not path.is_file() or path.stat().st_size == 0:
        raise FinishedM4BError(path)
    if path.suffix.lower() != ".m4b":
        raise FinishedM4BError(path)
    result = probe(path)
    if result.stream.codec not in {"aac", "aac_latm"}:
        raise FinishedM4BError(path)
    if not _has_supported_container(result.format_name):
        raise FinishedM4BError(path)
    if (
        abs(result.duration_ms - source_duration_ms) / source_duration_ms
        > SIZE_TOLERANCE
    ):
        raise FinishedM4BError(path)
    if not _has_plausible_size(path, result.duration_ms, result.stream.bit_rate):
        raise FinishedM4BError(path)


def _file_count(path: Path) -> int:
    """Count original files for the durable archive receipt."""
    if path.is_file():
        return 1
    return sum(child.is_file() for child in path.rglob("*"))


def _archive_path(source: Path, root: Path) -> Path:
    """Construct one direct child of the configured archive root."""
    source_root = source.resolve()
    archive_root = root.resolve()
    if source_root.is_relative_to(archive_root):
        raise ArchiveError(source)
    return archive_root / source.name


def archive_source(
    source: Path,
    final_m4b: Path,
    archive_root: Path,
    *,
    source_duration_ms: int,
) -> ArchivedSource:
    """Verify the final M4B, then move source originals under ``archive_root``."""
    validate_m4b(final_m4b, source_duration_ms=source_duration_ms)
    destination = _archive_path(source, archive_root)
    if destination.exists() and not source.exists():
        return ArchivedSource(
            source_path=source,
            archive_path=destination,
            original_count=_file_count(destination),
        )
    if destination.exists():
        raise ArchiveError(destination)
    if not source.exists():
        raise FileNotFoundError(source)

    original_count = _file_count(source)
    archive_root.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    log.info("archived {} original file(s) to {}", original_count, destination)
    return ArchivedSource(
        source_path=source,
        archive_path=destination,
        original_count=original_count,
    )
