"""Stage 06: Organize -- copy/move audiobook to Plex-compatible NFS library.

Pure file-mover stage. Reads pre-resolved metadata (author, title, series,
position) from the manifest (set by ASIN stage). Reads the tagged file path
from the metadata stage output. Handles dedup, destination building, copy
vs move (reorganize mode), and library index registration.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from ..config import PipelineConfig
from ..manifest import Manifest
from ..models import AUDIO_EXTENSIONS, Stage, StageStatus
from ..ops.organize import build_plex_path, copy_to_library, move_in_library

if TYPE_CHECKING:
    from ..library_index import LibraryIndex

log = logger.bind(stage="organize")


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: Manifest,
    dry_run: bool = False,
    verbose: bool = False,
    index: LibraryIndex | None = None,
    reorganize: bool = False,
    **kwargs,
) -> None:
    """Organize an audiobook into the NFS library.

    1. Read pre-resolved metadata from manifest (set by ASIN stage)
    2. Find the audio file to copy (metadata output > convert output > source)
    3. Early-skip if file already exists at expected destination
    4. Build the Plex-compatible destination path
    5. Copy (or move in reorganize mode) the file to the library
    6. Update the manifest with the destination path
    """
    manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.RUNNING)

    # Read pre-resolved metadata from manifest
    data = manifest.read(book_hash)
    if not data:
        click.echo(f"  ERROR: No manifest data for {book_hash}")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.FAILED)
        return

    meta = data.get("metadata", {})
    metadata = {
        "author": meta.get("parsed_author", ""),
        "title": meta.get("parsed_title", ""),
        "series": meta.get("parsed_series", ""),
        "position": meta.get("parsed_position", ""),
    }

    # Find the audio file to organize
    # Priority: metadata stage output (tagged file) > convert output > source
    source_file = _find_source_file(data, source_path)
    if source_file is None:
        click.echo(f"  ERROR: No audio files found for {source_path}")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.FAILED)
        return

    # Cross-source dedup: scope by book directory to avoid collisions
    dedup_key = (
        f"{source_path.name}/{source_file.stem}"
        if source_path.is_dir()
        else source_file.stem
    )
    if index and index.mark_processed(dedup_key):
        click.echo(f"  SKIPPED {source_file.name} -- already processed in batch")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
        return

    # Build destination
    dest_dir = build_plex_path(config.nfs_output_dir, metadata, index=index)
    book_dir = source_path if source_path.is_dir() else None

    # Reorganize mode: check if source dir is already the correct dest
    if reorganize and book_dir:
        if book_dir.resolve() == dest_dir.resolve():
            click.echo(f"  OK {book_dir.name}/ -- already correctly placed")
            manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
            return

    # For single-file mode, check individual file
    dest_file_path = dest_dir / source_file.name
    if not reorganize:
        if index:
            already_exists = index.file_exists(dest_dir, source_file.name)
        else:
            already_exists = dest_file_path.exists()
        if already_exists:
            click.echo(f"  SKIPPED {source_file.name} -- already exists at")
            click.echo(f"          {dest_file_path}")
            manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
            return

    # Determine action: move (reorganize) or copy
    if reorganize:
        action_label = "Moving" if not dry_run else "[DRY-RUN] Would move"
        display_name = f"{book_dir.name}/" if book_dir else source_file.name
    else:
        action_label = "Copying" if not dry_run else "[DRY-RUN] Would copy"
        display_name = source_file.name

    click.echo(f"  {action_label} {display_name}")
    click.echo(f"       -> {dest_dir}")

    if dry_run:
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
        return

    if reorganize and book_dir:
        dest_file = _move_book_directory(
            book_dir, dest_dir, stop_at=config.nfs_output_dir
        )
    elif reorganize:
        dest_file = move_in_library(
            source_file, dest_dir, dry_run=False, library_root=config.nfs_output_dir
        )
    else:
        dest_file = copy_to_library(source_file, dest_dir, dry_run=False)

    # Register the new file in the index
    if index:
        index.register_new_file(dest_dir, source_file.name)

    # Update manifest
    data = manifest.read(book_hash)
    if data:
        data["stages"]["organize"]["output_file"] = str(dest_file)
        data["stages"]["organize"]["dest_dir"] = str(dest_dir)
        manifest.update(book_hash, data)

    manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
    click.echo(f"  Organized: {dest_dir.relative_to(config.nfs_output_dir)}")


def _find_source_file(data: dict, source_path: Path) -> Path | None:
    """Find the audio file to organize.

    Priority:
    1. Metadata stage output (tagged file in work_dir)
    2. Convert stage output (untagged file in work_dir)
    3. Source path audio file discovery
    """
    stages = data.get("stages", {})

    # Check metadata stage output (tagged file)
    meta_output = stages.get("metadata", {}).get("output_file", "")
    if meta_output:
        p = Path(meta_output)
        if p.is_file():
            return p

    # Check convert stage output
    convert_output = stages.get("convert", {}).get("output_file", "")
    if convert_output:
        p = Path(convert_output)
        if p.is_file():
            return p

    # Fall back to source path discovery
    return _find_audio_file(source_path)


def _move_book_directory(
    source_dir: Path, dest_dir: Path, stop_at: Path | None = None
) -> Path:
    """Move all contents from source_dir into dest_dir (recursively).

    Handles multi-chapter books (many MP3s in one dir), multi-disc books
    with CD1/CD2 subdirectories, and single-file books alike.
    Cleans up the empty source directory after moving.
    Returns the destination directory path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for item in sorted(source_dir.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(source_dir)
        dest_file = dest_dir / rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        if dest_file.exists() and dest_file.stat().st_size == item.stat().st_size:
            log.debug(f"Skip (same size): {rel}")
            continue
        shutil.move(str(item), str(dest_file))
        moved += 1
    log.info(f"Moved {moved} files: {source_dir.name} -> {dest_dir}")

    from ..ops.organize import _cleanup_empty_parents

    _cleanup_empty_parents(source_dir, stop_at=stop_at)

    return dest_dir


def _find_audio_file(source_path: Path) -> Path | None:
    """Find the primary audio file from a source path."""
    log.debug(f"_find_audio_file: source_path={source_path}")

    if source_path.is_file():
        log.debug(f"_find_audio_file: found file {source_path}")
        return source_path

    m4b_files = list(source_path.rglob("*.m4b"))
    if m4b_files:
        log.debug(f"_find_audio_file: found m4b {m4b_files[0]}")
        return m4b_files[0]

    audio_files = [
        f for f in source_path.rglob("*") if f.suffix.lower() in AUDIO_EXTENSIONS
    ]
    result = audio_files[0] if audio_files else None
    log.debug(f"_find_audio_file: result={result}")
    return result
