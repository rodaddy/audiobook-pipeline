"""Encoding a joined file into a chaptered M4B.

Purpose:
    The last step that touches the audio. Produces the file that lands in the
    library: AAC in an MP4 container, with the chapter table written in and the
    faststart atom moved to the front.

NEVER ENCODE ABOVE THE SOURCE
    A 64 kbps MP3 re-encoded at 128 kbps is not better audio -- it is the same
    audio in a file twice the size, plus a second generation of lossy artifacts.
    The target bitrate is ``min(configured, source)``, so the setting is a
    CEILING rather than a target. Measured on this library: sources run from
    32 kbps up, and treating 128 as a floor would have roughly doubled the
    footprint of every low-bitrate book while making all of them sound worse.

WHY -movflags +faststart IS NOT OPTIONAL
    Without it the MP4 index sits at the END of the file. A player streaming
    over the network must read the whole file before it can start, and Plex
    reports the book as unplayable rather than as slow. It costs one extra pass
    at write time and nothing afterwards.

WHY THE CHAPTERS GO IN HERE AND NOT AFTER
    Writing chapters into a finished M4B means rewriting the container. Passing
    them to the encode that is already rewriting it is free, and it removes a
    whole step in which a book can end up on disk without its chapters.

Example:
    >>> _target_bitrate(configured=128, source_bps=64_000)
    64

See Also:
    - audiobook_pipeline.utils.ffmpeg: build_chapter_metadata, run_ffmpeg
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from audiobook_pipeline.config import EncodingSettings
from audiobook_pipeline.models.chapter import ChapterSet
from audiobook_pipeline.utils.ffmpeg import (
    build_chapter_metadata,
    probe,
    run_ffmpeg,
)

log = logger.bind(stage="convert")

#: Encoders that are already AAC in an MP4 container. A source in this set with
#: an acceptable bitrate needs no re-encode at all -- see `can_stream_copy`.
_AAC_CODECS = frozenset({"aac", "aac_latm"})


def _target_bitrate(*, configured: int, source_bps: int | None) -> int:
    """Choose the encode bitrate for one book.

    Args:
        configured: The configured ceiling, in kbps.
        source_bps: The source's bitrate in BITS per second, or None when the
            probe did not report one.

    Returns:
        Bitrate in kbps, never above the source's. An unreported source
        bitrate falls back to the ceiling: a source we cannot measure is the
        one case where guessing low would degrade audio for no reason.
    """
    if source_bps is None:
        return configured
    return min(configured, max(source_bps // 1000, 1))


def can_stream_copy(
    *, codec: str, source_bps: int | None, configured_kbps: int
) -> bool:
    """Whether the source can be copied instead of re-encoded.

    An AAC source already at or below the ceiling is already the thing we would
    produce. Re-encoding it costs hours of CPU and a generation of quality to
    arrive at a slightly worse copy of the input.

    Args:
        codec: The source's audio codec.
        source_bps: Source bitrate in bits per second, or None if unknown.
        configured_kbps: The configured ceiling in kbps.

    Returns:
        True when a stream copy is correct. False when the bitrate is unknown
        -- "cannot measure it" is not "it is fine", and copying an unmeasured
        stream is how a 320 kbps source ends up in the library untouched.
    """
    if codec.lower() not in _AAC_CODECS:
        return False
    if source_bps is None:
        return False
    return source_bps // 1000 <= configured_kbps


def _write_chapter_file(chapters: ChapterSet, work_dir: Path) -> Path | None:
    """Write the chapter table as an FFMETADATA1 file.

    Args:
        chapters: The table to write.
        work_dir: Directory to write into.

    Returns:
        Path to the metadata file, or None when there are no chapters -- an
        empty table would tell ffmpeg to write an empty chapter list, which
        REPLACES whatever the source carried rather than leaving it alone.
    """
    if chapters.is_empty:
        return None

    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / "chapters.txt"
    path.write_text(build_chapter_metadata(chapters.chapters), encoding="utf-8")
    return path


def _encode_args(*, bitrate_kbps: int, settings: EncodingSettings) -> list[str]:
    """Codec arguments for a re-encode.

    Args:
        bitrate_kbps: Target bitrate.
        settings: Encoding configuration.

    Returns:
        ffmpeg arguments selecting codec, bitrate, and channel count.
    """
    return [
        "-c:a",
        settings.codec,
        "-b:a",
        f"{bitrate_kbps}k",
        "-ac",
        str(settings.channels),
    ]


def convert_to_m4b(
    source: Path,
    output: Path,
    chapters: ChapterSet,
    settings: EncodingSettings,
    *,
    work_dir: Path | None = None,
) -> Path:
    """Encode one file into a chaptered M4B.

    Args:
        source: The joined (or single) input file.
        output: Where to write the M4B.
        chapters: The chapter table to embed.
        settings: Encoding configuration.
        work_dir: Where to write the temporary chapter file. Defaults to the
            output's directory.

    Returns:
        The output path.

    Raises:
        FfmpegError: The probe or the encode failed.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    probed = probe(source)

    chapter_file = _write_chapter_file(chapters, work_dir or output.parent)
    copying = can_stream_copy(
        codec=probed.stream.codec,
        source_bps=probed.stream.bit_rate,
        configured_kbps=settings.max_bitrate,
    )
    bitrate = _target_bitrate(
        configured=settings.max_bitrate, source_bps=probed.stream.bit_rate
    )

    args = ["-i", str(source)]
    if chapter_file is not None:
        # -map_metadata 1 takes metadata from input 1 (the chapter file). Input
        # 0's own metadata is NOT carried over by that flag, which is fine here
        # -- the tagging stage writes the real tags afterwards.
        args += ["-i", str(chapter_file), "-map_metadata", "1"]

    args += ["-map", "0:a"]
    args += (
        ["-c:a", "copy"]
        if copying
        else _encode_args(bitrate_kbps=bitrate, settings=settings)
    )
    # Without faststart the MP4 index lands at the end of the file and a
    # streaming player reports the book as unplayable rather than as slow.
    args += ["-movflags", "+faststart", str(output)]

    log.info(
        "{} {} -> {} ({} chapter(s))",
        "copying" if copying else f"encoding at {bitrate}k",
        source.name,
        output.name,
        len(chapters.chapters),
    )
    run_ffmpeg(args)
    return output
