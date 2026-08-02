"""Stage 02: Convert -- wraps ffmpeg MP3-to-M4B conversion as subprocess."""

from __future__ import annotations

import functools
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from ..ffprobe import count_chapters, get_codec, get_format_name, get_stream_info
from ..models import Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..pipeline_db import PipelineDB

log = logger.bind(stage="convert")


# Below this, a source is genuinely poor and re-encoding cannot make it worse
# in any way that matters -- but it is still not worth doing, so this only
# governs whether the "already good" claim holds.
PASSTHROUGH_MIN_BITRATE = 40

# Concat with -c copy requires every part to share codec, sample rate and
# channel count. Mixing them produces a file that plays as noise after the
# first boundary, so a single mismatch disqualifies the whole book.
_COPYABLE_CODECS = frozenset({"aac"})


def _read_concat_list(files_txt: Path) -> list[Path]:
    """Parse the paths back out of an ffmpeg concat demuxer file.

    Lines look like:  file '/path/to/part.m4b'
    with single quotes escaped as '\\'' by the concat stage.
    """
    out: list[Path] = []
    try:
        for line in files_txt.read_text().splitlines():
            line = line.strip()
            if not line.startswith("file "):
                continue
            raw = line[5:].strip()
            if raw.startswith("'") and raw.endswith("'"):
                raw = raw[1:-1].replace("'\\''", "'")
            out.append(Path(raw))
    except OSError as e:
        log.debug(f"Cannot read {files_txt}: {e}")
        return []
    return out


def _can_stream_copy(
    audio_files: list[Path],
    target_bitrate: int,
    config: PipelineConfig,
) -> bool:
    """True when the sources can be joined without re-encoding.

    Every part must be AAC, at a bitrate worth keeping, and identical in sample
    rate and channel count. Anything else -- mp3 sources, mixed formats, a
    bitrate so low the file is already damaged -- falls through to a real
    encode.

    Deliberately conservative: a wrong "yes" here yields a corrupt audiobook,
    while a wrong "no" only costs CPU time.
    """
    if not audio_files:
        return False

    if target_bitrate < PASSTHROUGH_MIN_BITRATE:
        log.debug(
            f"Not passthrough: {target_bitrate}k is below the "
            f"{PASSTHROUGH_MIN_BITRATE}k floor"
        )
        return False

    first: tuple[str, str] | None = None
    for f in audio_files:
        try:
            info = get_stream_info(f)
        except Exception as e:  # noqa: BLE001 -- unreadable means "do not risk it"
            log.debug(f"Not passthrough: cannot probe {f.name}: {e}")
            return False

        codec = (info.get("codec_name") or "").lower()
        if codec not in _COPYABLE_CODECS:
            log.debug(f"Not passthrough: {f.name} is {codec!r}, not AAC")
            return False

        shape = (str(info.get("sample_rate", "")), str(info.get("channels", "")))
        if first is None:
            first = shape
        elif shape != first:
            log.debug(
                f"Not passthrough: {f.name} is {shape} but the first file is {first}"
            )
            return False

    return True


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
    manifest: PipelineDB,
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
    # DON'T RE-ENCODE AUDIO THAT IS ALREADY FINE.
    #
    # Re-encoding AAC to AAC at the same bitrate is pure loss: it costs hours of
    # CPU and throws away quality on every pass, to produce a file no better
    # than the input. Measured 2026-08-01: the Coldfire trilogy was queued as
    # 34kbps aac -> 34kbps aac, and the source m4bs already carried their own
    # chapters.
    #
    # Stream-copying instead is seconds rather than hours AND bit-exact. The
    # concat demuxer still joins the parts and the chapter table still comes
    # from metadata.txt -- only the transcode is skipped.
    #
    # Skipped entirely on a dry run: deciding this means probing every source
    # file, and a dry run must not shell out at all.
    passthrough = not dry_run and _can_stream_copy(
        _read_concat_list(files_txt), target_bitrate, config
    )
    encoder = "copy" if passthrough else ("aac" if dry_run else _detect_encoder())

    codec_args = (
        ["-c:a", "copy"]
        if passthrough
        else [
            "-c:a",
            encoder,
            "-b:a",
            f"{target_bitrate}k",
            "-ac",
            str(config.channels),
        ]
    )
    if passthrough:
        log.info(
            f"Passthrough: sources are already AAC at {target_bitrate}k -- "
            f"stream-copying instead of re-encoding"
        )

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
        *codec_args,
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

    # Verify the encode preserved the chapter table CONCAT built.
    #
    # This used to compare against file_count, which silently assumed one file
    # equals one chapter. That is only true for the plainest case: it is false
    # for embedded marks (one already-chaptered m4b -> many chapters) and false
    # for Audnexus (16 encoding splits -> the book's real 17 chapters).
    # Measured 2026-08-01: Penric's Mission failed the stage precisely BECAUSE
    # the chapter data was correct -- 16 input files, 17 real chapters.
    #
    # What matters is that ffmpeg wrote what concat planned, so compare against
    # the planned count and fall back to file_count only when concat recorded
    # nothing.
    expected = (
        manifest.read(book_hash).get("metadata", {}).get("chapter_count") or file_count
    )
    if file_count > 1 or expected > 1:
        try:
            chapter_count = count_chapters(output_m4b)
            if chapter_count != expected:
                log.error(
                    f"Chapter count mismatch: concat planned {expected}, "
                    f"encoded file has {chapter_count}"
                )
                manifest.set_stage(book_hash, Stage.CONVERT, StageStatus.FAILED)
                return
            log.debug(f"Chapter table preserved through encode: {chapter_count}")
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
