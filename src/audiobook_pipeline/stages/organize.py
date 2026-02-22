"""Stage 07: Organize -- copy/move audiobook to Plex-compatible NFS library."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from ..ai import disambiguate, get_client, needs_resolution, resolve
from ..api.audible import search
from ..api.search import score_results
from ..config import PipelineConfig
from ..ffprobe import extract_author_from_tags, get_tags
from ..manifest import Manifest
from ..models import AUDIO_EXTENSIONS, Stage, StageStatus
from ..ops.organize import build_plex_path, copy_to_library, move_in_library, parse_path

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
) -> None:
    """Organize an audiobook into the NFS library.

    1. Parse the source path into author/title/series metadata
    2. Early-skip if file already exists at expected destination (index mode)
    3. Gather evidence from tags + Audible search
    4. Use AI to resolve conflicts (or validate all if ai_all=True)
    5. Build the Plex-compatible destination path
    6. Copy (or move in reorganize mode) the file to the library
    7. Update the manifest with the destination path
    """
    manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.RUNNING)

    # Find the actual audio file to copy
    source_file = _find_audio_file(source_path)
    if source_file is None:
        click.echo(f"  ERROR: No audio files found in {source_path}")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.FAILED)
        return

    # Cross-source dedup: skip if same stem already processed in this batch
    if index and index.mark_processed(source_file.stem):
        click.echo(f"  SKIPPED {source_file.name} -- already processed in batch")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
        return

    # Parse path into metadata
    metadata = parse_path(str(source_file))

    # Gather evidence from all sources
    tags = get_tags(source_file)
    tag_author = extract_author_from_tags(tags)
    tag_metadata = {
        "author": tag_author or "",
        "title": tags.get("title", ""),
        "album": tags.get("album", ""),
    }

    # Use tag title if path title looks like junk (matches filename stem)
    if tag_metadata["title"] and len(tag_metadata["title"]) > 3:
        if metadata["title"] == source_file.stem:
            metadata["title"] = tag_metadata["title"]

    # Search Audible for candidates -- widen the net when AI is available
    audible_candidates = _search_audible(
        metadata["title"], metadata["series"], config,
        author=metadata.get("author", ""),
        widen=bool(config.pipeline_llm_base_url),
    )

    # Pick best Audible match via fuzzy scoring
    audible_result = None
    has_ai = bool(config.pipeline_llm_base_url)
    if audible_candidates:
        scored = score_results(audible_candidates, metadata["title"], "")
        best = scored[0]

        if best["score"] >= config.asin_search_threshold:
            audible_result = {
                "author": best["author_str"],
                "asin": best["asin"],
                "title": best["title"],
                "series": best.get("series", ""),
                "position": best.get("position", ""),
            }
            log.debug(f"Audible match: {best['author_str']!r} (score={best['score']:.0f})")
        elif has_ai:
            # AI available -- let it pick from all candidates in post
            client = get_client(config.pipeline_llm_base_url, config.pipeline_llm_api_key)
            ai_pick = disambiguate(
                scored[:5], metadata["title"], "",
                config.pipeline_llm_model, client,
            )
            if ai_pick:
                audible_result = {
                    "author": ai_pick["author_str"],
                    "asin": ai_pick["asin"],
                    "title": ai_pick["title"],
                    "series": ai_pick.get("series", ""),
                    "position": ai_pick.get("position", ""),
                }
                log.debug(f"AI disambiguated: {ai_pick['author_str']!r}")

    # Decide if AI should resolve metadata
    should_resolve = config.ai_all or needs_resolution(
        metadata, tag_metadata, audible_result,
    )

    if should_resolve:
        client = get_client(config.pipeline_llm_base_url, config.pipeline_llm_api_key)
        ai_metadata = resolve(
            metadata, tag_metadata,
            audible_candidates, config.pipeline_llm_model, client,
            source_filename=source_file.name,
        )
        if ai_metadata:
            # Apply AI-resolved fields (only override non-empty values)
            for key in ("author", "title", "series", "position"):
                if key in ai_metadata and ai_metadata[key]:
                    metadata[key] = ai_metadata[key]
            log.info(f"AI resolved: author={ai_metadata.get('author', '?')!r}")
        elif audible_result and audible_result.get("author"):
            metadata["author"] = audible_result["author"]
            log.debug(f"Using Audible author: {audible_result['author']!r}")
        elif tag_author:
            metadata["author"] = tag_author
            log.debug(f"Using tag author: {tag_author!r}")
    else:
        # No AI needed -- use best available source
        if audible_result and audible_result.get("author"):
            metadata["author"] = audible_result["author"]
        elif tag_author:
            metadata["author"] = tag_author

    log.debug(
        f"Final: author={metadata['author']!r} title={metadata['title']!r} "
        f"series={metadata['series']!r} pos={metadata['position']!r}"
    )

    # Build destination
    dest_dir = build_plex_path(config.nfs_output_dir, metadata, index=index)
    dest_file_path = dest_dir / source_file.name

    # Reorganize mode: check if already in correct location
    if reorganize and index and index.is_correctly_placed(source_file, dest_file_path):
        click.echo(f"  OK {source_file.name} -- already correctly placed")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
        return

    # Check if file already exists at destination
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
    else:
        action_label = "Copying" if not dry_run else "[DRY-RUN] Would copy"

    if dry_run:
        click.echo(f"  {action_label} {source_file.name}")
        click.echo(f"         -> {dest_file_path}")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
        return

    click.echo(f"  {action_label} {source_file.name}")
    click.echo(f"       -> {dest_file_path}")

    if reorganize:
        dest_file = move_in_library(source_file, dest_dir, dry_run=False)
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
        data["metadata"].update({
            "parsed_author": metadata["author"],
            "parsed_title": metadata["title"],
            "parsed_series": metadata["series"],
            "parsed_position": metadata["position"],
        })
        manifest.update(book_hash, data)

    manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
    click.echo(f"  Organized: {dest_dir.relative_to(config.nfs_output_dir)}")


def _find_audio_file(source_path: Path) -> Path | None:
    """Find the primary audio file from a source path."""
    if source_path.is_file():
        return source_path

    # Directory -- find .m4b first, then any audio file
    m4b_files = list(source_path.rglob("*.m4b"))
    if m4b_files:
        return m4b_files[0]

    audio_files = [
        f for f in source_path.rglob("*")
        if f.suffix.lower() in AUDIO_EXTENSIONS
    ]
    return audio_files[0] if audio_files else None


def _search_audible(
    title: str,
    series: str,
    config: PipelineConfig,
    author: str = "",
    widen: bool = False,
) -> list[dict]:
    """Search Audible with multiple query strategies, dedupe by ASIN.

    When widen=True (AI available), cast a wider net with additional
    query combinations -- AI will filter the results in post.
    """
    queries = [title]
    if series:
        queries.append(series)
        queries.append(f"{series} {title}")
    if widen and author:
        queries.append(f"{author} {title}")
        if series:
            queries.append(f"{author} {series}")

    seen_asins: set[str] = set()
    all_results: list[dict] = []
    for query in queries:
        if not query:
            continue
        try:
            hits = search(query, config.audible_region)
        except Exception:
            continue
        for h in hits:
            if h["asin"] not in seen_asins:
                seen_asins.add(h["asin"])
                all_results.append(h)

    return all_results
