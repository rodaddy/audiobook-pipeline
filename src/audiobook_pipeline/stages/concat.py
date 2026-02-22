"""Stage 02: Concat -- generate ffmpeg input files from validated audio list.

Creates two files in the work directory:
1. files.txt -- ffmpeg concat demuxer file (list of audio files to merge)
2. metadata.txt -- FFMETADATA1 chapter file (chapter markers + book title)

These files are consumed by the convert stage to produce the final m4b.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from ..ffprobe import get_duration
from ..models import Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..manifest import Manifest

log = logger.bind(stage="concat")


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: Manifest,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs,
) -> None:
    """Generate ffmpeg concat demuxer file and FFMETADATA chapter file.

    Reads the validated audio file list from audio_files.txt and generates:
    - files.txt: ffmpeg concat demuxer format with escaped paths
    - metadata.txt: FFMETADATA1 chapter markers with cumulative timestamps

    For single-file books, writes metadata header only (no chapters).
    """
    manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.RUNNING)

    work_path = config.work_dir / book_hash
    audio_files_path = work_path / "audio_files.txt"

    # Read the validated audio file list
    if not audio_files_path.exists():
        log.error(f"audio_files.txt not found at {audio_files_path}")
        manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.FAILED)
        return

    try:
        audio_files = [
            Path(line.strip())
            for line in audio_files_path.read_text().splitlines()
            if line.strip()
        ]
    except Exception as e:
        log.error(f"Failed to read audio_files.txt: {e}")
        manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.FAILED)
        return

    if not audio_files:
        log.error("audio_files.txt is empty")
        manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.FAILED)
        return

    # Generate files.txt (ffmpeg concat demuxer format)
    files_txt_path = work_path / "files.txt"
    files_txt_lines = []
    for audio_file in audio_files:
        # Escape single quotes: path.replace("'", "'\\''")
        escaped_path = str(audio_file).replace("'", "'\\''")
        files_txt_lines.append(f"file '{escaped_path}'")

    # Generate metadata.txt (FFMETADATA1 chapter file)
    metadata_txt_path = work_path / "metadata.txt"
    book_title = source_path.name

    # For single-file books, write header only (no chapters)
    single_file = len(audio_files) == 1

    if single_file:
        metadata_lines = [
            ";FFMETADATA1",
            f"title={book_title}",
            "",
        ]
        chapter_count = 0
    else:
        # Multi-file book: generate chapter markers
        metadata_lines = [
            ";FFMETADATA1",
            f"title={book_title}",
            "",
        ]

        cumulative_ms = 0
        for audio_file in audio_files:
            try:
                duration_sec = get_duration(audio_file)
            except Exception as e:
                log.error(f"Failed to get duration for {audio_file}: {e}")
                manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.FAILED)
                return

            duration_ms = int(duration_sec * 1000)
            chapter_title = audio_file.stem

            metadata_lines.extend(
                [
                    "[CHAPTER]",
                    "TIMEBASE=1/1000",
                    f"START={cumulative_ms}",
                    f"END={cumulative_ms + duration_ms}",
                    f"title={chapter_title}",
                    "",
                ]
            )

            cumulative_ms += duration_ms

        chapter_count = len(audio_files)

    # Write files (always -- lightweight text manifests, not the conversion)
    try:
        files_txt_path.write_text("\n".join(files_txt_lines) + "\n")
        metadata_txt_path.write_text("\n".join(metadata_lines))
        log.debug(f"Wrote {len(audio_files)} entries to files.txt")
        log.debug(f"Wrote {chapter_count} chapters to metadata.txt")
    except Exception as e:
        log.error(f"Failed to write concat/metadata files: {e}")
        manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.FAILED)
        return

    # Update manifest with chapter count
    data = manifest.read(book_hash)
    if data:
        existing_metadata = data.get("metadata", {})
        manifest.update(
            book_hash,
            {
                "metadata": {
                    **existing_metadata,
                    "chapter_count": chapter_count,
                }
            },
        )

    manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.COMPLETED)
    prefix = "  CONCAT (dry-run)" if dry_run else "  CONCAT"
    click.echo(f"{prefix}: {chapter_count} chapters, files.txt + metadata.txt ready")
