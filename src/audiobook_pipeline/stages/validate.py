"""Validation stage -- verifies source audio and prepares conversion metadata."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from ..concurrency import check_disk_space
from ..ffprobe import (
    duration_to_timestamp,
    get_bitrate,
    get_duration,
    validate_audio_file,
)
from ..models import AUDIO_EXTENSIONS, Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..pipeline_db import PipelineDB

log = logger.bind(stage="validate")


def _natural_sort_key(p: Path) -> list:
    """Extract numeric/text parts for natural sorting of filenames."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", p.name)]


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: PipelineDB,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Validate source directory and prepare conversion metadata.

    Discovers audio files, validates them with ffprobe, computes target bitrate
    and duration, checks disk space, and writes a file list for later stages.
    Sets stage to FAILED and returns (without raising) on validation errors.
    """
    manifest.set_stage(book_hash, Stage.VALIDATE, StageStatus.RUNNING)

    # Verify source is a directory
    if not source_path.is_dir():
        log.error(f"Source path is not a directory: {source_path}")
        manifest.set_stage(book_hash, Stage.VALIDATE, StageStatus.FAILED)
        return

    # Find all audio files (excluding .m4b since we're converting TO m4b)
    valid_extensions = AUDIO_EXTENSIONS - {".m4b"}
    all_files = [
        f
        for f in source_path.rglob("*")
        if f.is_file() and f.suffix.lower() in valid_extensions
    ]

    if not all_files:
        log.error(f"No audio files found in {source_path}")
        manifest.set_stage(book_hash, Stage.VALIDATE, StageStatus.FAILED)
        return

    # Natural sort by filename
    all_files.sort(key=_natural_sort_key)
    log.debug(f"Found {len(all_files)} potential audio files")

    # Check disk space before processing
    if not check_disk_space(source_path, config.work_dir):
        log.error("Insufficient disk space for conversion")
        manifest.set_stage(book_hash, Stage.VALIDATE, StageStatus.FAILED)
        return

    # Validate each file with ffprobe
    valid_files: list[Path] = []
    for f in all_files:
        if validate_audio_file(f):
            valid_files.append(f)
        else:
            log.warning(f"Skipping invalid audio file: {f.name}")

    if not valid_files:
        log.error("No valid audio files found after validation")
        manifest.set_stage(book_hash, Stage.VALIDATE, StageStatus.FAILED)
        return

    log.info(f"Validated {len(valid_files)} of {len(all_files)} files")

    # Detect bitrate from first valid file
    try:
        first_bitrate_bps = get_bitrate(valid_files[0])
        target_bitrate = min(first_bitrate_bps // 1000, config.max_bitrate)
        log.debug(
            f"Detected bitrate: {first_bitrate_bps} bps, target: {target_bitrate}k"
        )
    except (ValueError, OSError) as e:
        log.warning(f"Failed to detect bitrate from {valid_files[0].name}: {e}")
        target_bitrate = config.max_bitrate

    # Sum durations
    total_duration = 0.0
    for f in valid_files:
        try:
            total_duration += get_duration(f)
        except (ValueError, OSError) as e:
            log.warning(f"Failed to get duration for {f.name}: {e}")

    log.debug(
        f"Total duration: {total_duration:.2f}s ({duration_to_timestamp(total_duration)})"
    )

    # Create work directory and write file list (always -- it's lightweight metadata)
    work_dir = config.work_dir / book_hash
    work_dir.mkdir(parents=True, exist_ok=True)
    file_list_path = work_dir / "audio_files.txt"
    file_list_path.write_text("\n".join(str(f.resolve()) for f in valid_files) + "\n")
    log.debug(f"Wrote file list to {file_list_path}")

    prefix = "  VALIDATE (dry-run)" if dry_run else "  VALIDATE"
    click.echo(
        f"{prefix}: {len(valid_files)} files, "
        f"{duration_to_timestamp(total_duration)}, target {target_bitrate}k"
    )

    # Update manifest with metadata
    data = manifest.read(book_hash)
    existing_metadata = data.get("metadata", {}) if data else {}

    manifest.update(
        book_hash,
        {
            "metadata": {
                **existing_metadata,
                "target_bitrate": target_bitrate,
                "file_count": len(valid_files),
                "total_duration": total_duration,
            }
        },
    )

    manifest.set_stage(book_hash, Stage.VALIDATE, StageStatus.COMPLETED)
