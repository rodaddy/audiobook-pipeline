"""Stage 02: Convert -- wraps ffmpeg MP3-to-M4B conversion as subprocess."""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from ..ffprobe import count_chapters, get_codec, get_format_name
from ..models import Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..manifest import Manifest

log = logger.bind(stage="convert")


@functools.cache
def _detect_encoder() -> str:
    """Check if aac_at (Apple AudioToolbox) is available, fall back to aac."""
    result = subprocess.run(
        ["ffmpeg", "-encoders"],
        capture_output=True,
        text=True,
    )
    if "aac_at" in result.stdout:
        log.info("Using aac_at encoder (Apple AudioToolbox)")
        return "aac_at"
    log.info("Using default aac encoder")
    return "aac"


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: Manifest,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs,
) -> None:
    """Convert MP3/M4A/etc files to M4B audiobook with embedded metadata.

    1. Read manifest for target_bitrate and file_count
    2. Locate input files (files.txt, metadata.txt)
    3. Build ffmpeg command with encoder auto-detection
    4. Run conversion (or skip in dry_run mode)
    5. Validate output (existence, codec, format, chapter count)
    6. Update manifest with output_file and codec info
    """
    manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.RUNNING)

    # Extract thread count from kwargs (0 = use all cores)
    threads = kwargs.get("threads", 0)

    # Read manifest metadata
    data = manifest.read(book_hash)
    if data is None:
        log.error(f"Manifest not found for {book_hash}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    metadata = data.get("metadata", {})
    target_bitrate = metadata.get("target_bitrate")
    file_count = metadata.get("file_count", 1)

    if not target_bitrate:
        log.error(f"Missing target_bitrate in manifest for {book_hash}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    # Locate input files
    work_book_dir = config.work_dir / book_hash
    files_txt = work_book_dir / "files.txt"
    metadata_txt = work_book_dir / "metadata.txt"

    if not files_txt.exists():
        log.error(f"Missing files.txt for {book_hash}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    if not metadata_txt.exists():
        log.error(f"Missing metadata.txt for {book_hash}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    # Create output directory
    output_dir = work_book_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Output path
    output_m4b = output_dir / f"{source_path.name}.m4b"

    # Detect encoder
    encoder = _detect_encoder()

    # Build ffmpeg command
    cmd = [
        "ffmpeg",
        "-y",
        "-threads",
        str(threads),
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(files_txt),
        "-i",
        str(metadata_txt),
        "-map_metadata",
        "1",
        "-map",
        "0:a",
        "-c:a",
        encoder,
        "-b:a",
        f"{target_bitrate}k",
        "-ac",
        str(config.channels),
        "-movflags",
        "+faststart",
        str(output_m4b),
    ]

    if dry_run:
        log.info(f"[DRY-RUN] Would convert: {source_path.name}")
        log.info(f"[DRY-RUN] Command: {' '.join(cmd)}")
        manifest.update(
            book_hash,
            {
                "metadata": {
                    **metadata,
                    "output_file": str(output_m4b),
                    "codec": encoder,
                    "bitrate": f"{target_bitrate}k",
                }
            },
        )
        data = manifest.read(book_hash)
        if data:
            data["stages"]["convert"]["output_file"] = str(output_m4b)
            manifest.update(book_hash, data)
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.COMPLETED)
        return

    # Run ffmpeg
    log.info(f"Converting: {source_path.name}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error(f"ffmpeg failed: {result.stderr[-500:]}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    # Post-conversion validation
    if not output_m4b.exists():
        log.error(f"Output file not created: {output_m4b}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    if output_m4b.stat().st_size == 0:
        log.error(f"Output file is empty: {output_m4b}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    # Check codec
    try:
        actual_codec = get_codec(output_m4b)
        if actual_codec != "aac":
            log.error(f"Expected codec aac, got {actual_codec}")
            manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
            return
    except ValueError as exc:
        log.error(f"Failed to read codec: {exc}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    # Check format
    try:
        format_name = get_format_name(output_m4b)
        if "mov" not in format_name and "mp4" not in format_name:
            log.error(f"Expected mov/mp4 format, got {format_name}")
            manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
            return
    except Exception as exc:
        log.error(f"Failed to read format: {exc}")
        manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
        return

    # Check chapter count (for multi-file books)
    if file_count > 1:
        try:
            chapter_count = count_chapters(output_m4b)
            if chapter_count != file_count:
                log.error(
                    f"Chapter count mismatch: expected {file_count}, got {chapter_count}"
                )
                manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
                return
        except Exception as exc:
            log.error(f"Failed to count chapters: {exc}")
            manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
            return

    # Update manifest -- store output_file in both locations:
    # - stages.convert.output_file: canonical location for downstream stages
    # - metadata dict: codec/bitrate info for reference
    manifest.update(
        book_hash,
        {
            "metadata": {
                **metadata,
                "output_file": str(output_m4b),
                "codec": encoder,
                "bitrate": f"{target_bitrate}k",
            }
        },
    )
    data = manifest.read(book_hash)
    if data:
        data["stages"]["convert"]["output_file"] = str(output_m4b)
        manifest.update(book_hash, data)

    # Mark completed
    manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.COMPLETED)

    # Progress output
    click.echo(
        f"  CONVERT: {source_path.name} -> {output_m4b.name} "
        f"({target_bitrate}k {encoder})"
    )
