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

from ..ffprobe import get_duration, read_chapters
from ..models import Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..pipeline_db import PipelineDB

log = logger.bind(stage="concat")


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: PipelineDB,
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

    # Build the chapter list.
    #
    # Chapters come from TWO sources and both matter:
    #   1. Marks already EMBEDDED in a source file. A book that arrives as one
    #      finished M4B carries its chapters inside the container.
    #   2. FILE BOUNDARIES, when a book arrives as one file per chapter.
    #
    # Only (2) used to be implemented, and the single-file branch wrote a
    # header with no chapters at all. Measured 2026-08-01: "The Martian.m4b"
    # has 160 embedded chapters and this stage emitted zero, flattening an
    # 11-hour book into one unnavigable block. Hormozi's "$100M Leads" (29)
    # and each Legend of Drizzt book (40) hit the same path.
    #
    # Preferring embedded marks per file also fixes the multi-file case: a
    # 19-part book whose parts each carry their own chapters kept only 19
    # boundaries and discarded the rest.
    metadata_lines = [
        ";FFMETADATA1",
        f"title={book_title}",
        "",
    ]

    chapters: list[tuple[int, int, str]] = []
    cumulative_ms = 0
    for audio_file in audio_files:
        try:
            duration_sec = get_duration(audio_file)
        except Exception as e:
            log.error(f"Failed to get duration for {audio_file}: {e}")
            manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.FAILED)
            return

        duration_ms = int(duration_sec * 1000)
        embedded = read_chapters(audio_file)

        if embedded:
            # Offset each embedded mark by this file's position in the concat.
            # Clamped to the measured duration so a bad END in the source
            # cannot push a chapter past the end of the joined file.
            for ch in embedded:
                start = cumulative_ms + min(ch["start_ms"], duration_ms)
                end = cumulative_ms + min(ch["end_ms"], duration_ms)
                if end > start:
                    chapters.append((start, end, ch["title"]))
            log.debug(f"{audio_file.name}: {len(embedded)} embedded chapters")
        else:
            # No marks inside: the file itself is one chapter.
            chapters.append(
                (cumulative_ms, cumulative_ms + duration_ms, audio_file.stem)
            )

        cumulative_ms += duration_ms

    for start_ms, end_ms, chapter_title in chapters:
        metadata_lines.extend(
            [
                "[CHAPTER]",
                "TIMEBASE=1/1000",
                f"START={start_ms}",
                f"END={end_ms}",
                f"title={chapter_title}",
                "",
            ]
        )

    chapter_count = len(chapters)

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
