"""Stage 05: Metadata -- tag M4B files with artist, album, genre, ASIN, and cover art.

Runs after ASIN resolution (reads parsed metadata from manifest).
Uses ffmpeg -c copy to write tags without re-encoding, preserving chapters.
Downloads cover art from Audible and embeds it as attached_pic.
Writes to a temp file in the same directory and atomically replaces the original.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from ..models import Stage, StageStatus

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..manifest import Manifest

log = logger.bind(stage="metadata")


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: Manifest,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs,
) -> None:
    """Tag an M4B file with metadata from the manifest.

    Reads parsed_author, parsed_title, parsed_series, parsed_position,
    parsed_asin, and cover_url from the manifest metadata (set by ASIN stage).
    Reads the output file path from stages.convert.output_file (work_dir),
    falling back to source_path for enrich/metadata modes.

    Tags written: artist, album_artist, album, title, genre, ASIN.
    Album is set to "series, Book N" if series exists, otherwise title.

    Downloads cover art from cover_url and embeds via ffmpeg -disposition
    attached_pic. Cover download failure is non-fatal.

    Uses ffmpeg -c copy (no re-encode) with -map_chapters 0 to preserve
    chapter markers. Writes to a temp file and atomically replaces.
    """
    manifest.set_stage(book_hash, Stage.METADATA, StageStatus.RUNNING)

    # Read manifest data
    data = manifest.read(book_hash)
    if not data:
        click.echo(f"  ERROR: No manifest data for {book_hash}")
        manifest.set_stage(book_hash, Stage.METADATA, StageStatus.FAILED)
        return

    meta = data.get("metadata", {})

    # Find the output file to tag -- prefer convert stage output (in work_dir),
    # then fall back to source_path (enrich/metadata modes)
    output_file = _find_output_file(data, source_path)
    if output_file is None:
        click.echo("  ERROR: No output file found for metadata tagging")
        manifest.set_stage(book_hash, Stage.METADATA, StageStatus.FAILED)
        return

    if not output_file.exists():
        click.echo(f"  ERROR: Output file not found: {output_file}")
        manifest.set_stage(book_hash, Stage.METADATA, StageStatus.FAILED)
        return

    # Build tag values from manifest metadata
    author = meta.get("parsed_author", "")
    title = meta.get("parsed_title", "")
    series = meta.get("parsed_series", "")
    position = meta.get("parsed_position", "")
    asin = meta.get("parsed_asin", "")
    narrator = meta.get("parsed_narrator", "")
    year = meta.get("parsed_year", "")
    cover_url = meta.get("cover_url", "")

    album = _build_album(title, series, position)

    tags: dict[str, str] = {
        "artist": author,
        "album_artist": author,
        "album": album,
        "title": title,
        "genre": "Audiobook",
        "media_type": "2",
    }
    if narrator:
        tags["composer"] = narrator
    if year:
        tags["date"] = year
    if series:
        tags["show"] = series
        tags["grouping"] = series

    log.debug(
        f"Tagging {output_file.name}: artist={author!r} album={album!r} "
        f"asin={asin!r}"
    )

    if dry_run:
        click.echo(f"  [DRY-RUN] Would tag {output_file.name}:")
        for k, v in tags.items():
            click.echo(f"    {k}={v}")
        if cover_url:
            click.echo(f"    cover_url={cover_url}")
        manifest.set_stage(book_hash, Stage.METADATA, StageStatus.COMPLETED)
        return

    # Download cover art (non-fatal on failure)
    cover_path = None
    if cover_url:
        cover_path = _download_cover(cover_url, output_file.parent)

    # Write tags via ffmpeg
    success = _write_tags(output_file, tags, cover_path=cover_path)

    # Clean up cover temp file
    if cover_path and cover_path.exists():
        cover_path.unlink(missing_ok=True)

    if not success:
        manifest.set_stage(book_hash, Stage.METADATA, StageStatus.FAILED)
        return

    # Record the tagged file path in manifest for downstream stages
    data = manifest.read(book_hash)
    if data:
        data["stages"]["metadata"]["output_file"] = str(output_file)
        manifest.update(book_hash, data)

    manifest.set_stage(book_hash, Stage.METADATA, StageStatus.COMPLETED)
    cover_note = " +cover" if cover_path else ""
    click.echo(f"  Tagged: {output_file.name} (artist={author!r}{cover_note})")


def _find_output_file(data: dict, source_path: Path) -> Path | None:
    """Find the M4B file to tag.

    Priority:
    1. Convert stage output (file in work_dir, convert mode)
    2. source_path if it's an M4B file (enrich/metadata mode)
    3. First M4B in source_path directory
    """
    stages = data.get("stages", {})

    # Check convert stage output
    convert_output = stages.get("convert", {}).get("output_file", "")
    if convert_output:
        p = Path(convert_output)
        if p.is_file():
            return p

    # Check metadata.output_file (from previous metadata run)
    meta_output = data.get("metadata", {}).get("output_file", "")
    if meta_output:
        p = Path(meta_output)
        if p.is_file():
            return p

    # source_path is a file
    if source_path.is_file() and source_path.suffix.lower() == ".m4b":
        return source_path

    # source_path is a directory -- find M4B
    if source_path.is_dir():
        m4b_files = list(source_path.rglob("*.m4b"))
        if m4b_files:
            return m4b_files[0]

    return None


def _download_cover(url: str, dest_dir: Path) -> Path | None:
    """Download cover art image to a temp file. Returns path or None on failure."""
    import httpx

    cover_path = dest_dir / "_cover.jpg"
    log.debug(f"Downloading cover art: {url}")

    try:
        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        cover_path.write_bytes(resp.content)
        log.info(f"Cover art downloaded: {len(resp.content)} bytes")
        return cover_path
    except Exception as e:
        log.warning(f"Cover art download failed: {e}")
        return None


def _build_album(title: str, series: str, position: str) -> str:
    """Build album tag from series/position or fall back to title.

    Examples:
        series="The Expanse", position="3" -> "The Expanse, Book 3"
        series="The Expanse", position=""  -> "The Expanse"
        series="", position=""             -> title
    """
    if series and position:
        return f"{series}, Book {position}"
    if series:
        return series
    return title


def _write_tags(
    filepath: Path,
    tags: dict[str, str],
    cover_path: Path | None = None,
) -> bool:
    """Write metadata tags to an M4B file using ffmpeg.

    Uses -c copy (no re-encode) and -map_chapters 0 to preserve chapters.
    If cover_path is provided, embeds it as attached_pic.
    Writes to a temp file in the same directory, then atomically replaces
    the original.

    Returns True on success, False on failure.
    """
    temp_file = filepath.with_suffix(".m4b.tmp")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(filepath),
    ]

    # Add cover art as second input
    if cover_path and cover_path.exists():
        cmd.extend(["-i", str(cover_path)])
        # Map audio from first input (skip data/subtitle streams), cover from second
        cmd.extend(["-map", "0:a", "-map", "1"])
        cmd.extend(["-c", "copy"])
        cmd.extend(["-disposition:v:0", "attached_pic"])
    else:
        cmd.extend(["-c", "copy"])

    cmd.extend(["-map_chapters", "0"])

    for key, value in tags.items():
        cmd.extend(["-metadata", f"{key}={value}"])

    # Force ipod/m4b format -- ffmpeg can't guess from .m4b.tmp extension
    cmd.extend(["-f", "ipod"])
    cmd.append(str(temp_file))

    log.debug(f"ffmpeg command: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        click.echo(f"  ERROR: ffmpeg timed out tagging {filepath.name}")
        temp_file.unlink(missing_ok=True)
        return False

    if result.returncode != 0:
        click.echo(f"  ERROR: ffmpeg failed tagging {filepath.name}")
        log.error(f"ffmpeg stderr: {result.stderr[-500:]}")
        temp_file.unlink(missing_ok=True)
        return False

    # Atomic replace
    try:
        temp_file.replace(filepath)
    except OSError as e:
        click.echo(f"  ERROR: Failed to replace {filepath.name}: {e}")
        temp_file.unlink(missing_ok=True)
        return False

    return True
