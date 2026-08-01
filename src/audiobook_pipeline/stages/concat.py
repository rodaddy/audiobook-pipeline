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
from ..models import AUDIO_EXTENSIONS, Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..pipeline_db import PipelineDB

log = logger.bind(stage="concat")

# Parsed titles that name front matter rather than the book. The sorted-first
# file in a book directory is frequently one of these ("00 - Intro.mp3"), and
# searching Audible for "Intro" finds nothing, costing the book its chapters.
_FRONT_MATTER_TITLES = frozenset(
    {
        "intro",
        "introduction",
        "outro",
        "prologue",
        "preface",
        "foreword",
        "opening credits",
        "end credits",
        "credits",
        "dedication",
        "epilogue",
        "afterword",
        "acknowledgments",
        "acknowledgements",
        "",
    }
)


def _remote_chapters(
    source_path: Path,
    local_duration_sec: float,
    config: PipelineConfig,
) -> list[tuple[int, int, str]]:
    """Look up chapter timings for a book whose audio carries none.

    Resolves the ASIN from the path the same way the asin stage does, then asks
    Audnexus. Returns [] on any miss -- unknown book, wrong edition, network
    failure -- and the caller keeps its file-boundary chapters.

    Done HERE rather than in the asin stage because concat runs first (see
    STAGE_ORDER) and the chapter table has to exist before the encode. The
    lookup is skipped entirely when the audio already has embedded marks, so
    the usual case costs no network call.
    """
    # Imported inside the function: this path is only reached for books with no
    # embedded chapters, and importing the API clients at module scope would
    # pull httpx into every concat run that does not need it.
    from ..api.audnexus import get_chapters
    from ..api.search import score_results
    from ..ops.organize import parse_path

    audio = next(
        (
            f
            for f in sorted(source_path.rglob("*"))
            if f.suffix.lower() in AUDIO_EXTENSIONS
        ),
        None,
    )
    if audio is None:
        return []

    # Parse a FILE path, which is what parse_path expects -- handed a bare
    # directory it treats the parent as the book and returns the raw folder
    # name ("Powder Mage 02 - The Crimson Campaign"), which then matches a
    # 10.78h omnibus instead of the 20.10h novel.
    #
    # The remaining trap is that the sorted-first file is often front matter:
    # "00 - Intro.mp3" parses as the title "Intro". So prefer a file whose
    # parsed title is not obvious front matter, and fall back to the first.
    meta: dict = {}
    for candidate in sorted(source_path.rglob("*")):
        if candidate.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        parsed = parse_path(str(candidate), source_dir=source_path)
        if parsed.get("title", "").strip().lower() not in _FRONT_MATTER_TITLES:
            meta = parsed
            break
    if not meta:
        meta = parse_path(str(audio), source_dir=source_path)

    title = meta.get("title", "")
    author = meta.get("author", "")
    if not title:
        log.debug("No parsed title -- skipping remote chapter lookup")
        return []

    from ..api.audible import search

    try:
        candidates = search(f"{author} {title}".strip(), config.audible_region)
    except Exception as e:  # noqa: BLE001 -- a lookup miss must never fail concat
        log.warning(f"Audible search failed during chapter lookup: {e}")
        return []

    if not candidates:
        log.info(f"No Audible match for {title!r} -- keeping file-boundary chapters")
        return []

    best = score_results(candidates, title, author)[0]
    if best["score"] < config.asin_search_threshold:
        log.info(
            f"Best Audible match for {title!r} scored {best['score']:.0f}, below "
            f"threshold {config.asin_search_threshold} -- not trusting its chapters"
        )
        return []

    remote = get_chapters(
        best["asin"], local_duration_sec, region=config.audnexus_region
    )
    if not remote:
        return []

    log.info(
        f"Using {len(remote)} Audnexus chapters for {title!r} (ASIN {best['asin']})"
    )
    return [(c["start_ms"], c["end_ms"], c["title"]) for c in remote]


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
    provenance = "file-boundary"
    cumulative_ms = 0
    any_embedded = False
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
            any_embedded = True
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

    if any_embedded:
        provenance = "embedded"
    else:
        # Nothing in the audio carries marks, so every "chapter" above is just
        # a file boundary -- an encoding split, not a chapter. Measured
        # 2026-08-01: Promise of Blood is 19 files of ~60 minutes, which is 19
        # useless chapters for a book that really has 42.
        #
        # Only now is it worth a network call. EMBEDDED MARKS ALWAYS WIN and
        # are never replaced by remote data: they came from the actual file,
        # while Audnexus is describing an edition we are inferring is the same
        # one.
        remote = _remote_chapters(source_path, cumulative_ms / 1000.0, config)
        if remote:
            chapters = remote
            provenance = "audnexus"

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
                    # WHERE the chapters came from, recorded so a later reader
                    # can tell an authoritative table from an inferred one:
                    #   embedded      -- read out of the source audio itself
                    #   audnexus      -- fetched for a matched ASIN/edition
                    #   file-boundary -- one chapter per input file, which for
                    #                    encoding splits is not really chapters
                    # Without this, all three look identical in the output and
                    # a re-run would treat a guess as ground truth.
                    "chapter_source": provenance,
                }
            },
        )

    manifest.set_stage(book_hash, Stage.CONCAT, StageStatus.COMPLETED)
    prefix = "  CONCAT (dry-run)" if dry_run else "  CONCAT"
    click.echo(f"{prefix}: {chapter_count} chapters, files.txt + metadata.txt ready")
