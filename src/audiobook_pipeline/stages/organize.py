"""Stage 07: Organize -- copy/move audiobook to Plex-compatible NFS library."""

from pathlib import Path

import click
from loguru import logger

from ..ai import disambiguate, get_client, needs_resolution, resolve
from ..api.audible import search
from ..api.search import score_results
from ..config import PipelineConfig
from ..ffprobe import extract_author_from_tags, get_tags
from ..manifest import Manifest
from ..models import Stage, StageStatus
from ..ops.organize import build_plex_path, copy_to_library, parse_path

log = logger.bind(stage="organize")


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: Manifest,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Organize an audiobook into the NFS library.

    1. Parse the source path into author/title/series metadata
    2. Gather evidence from tags + Audible search
    3. Use AI to resolve conflicts (or validate all if ai_all=True)
    4. Build the Plex-compatible destination path
    5. Copy the file to the library
    6. Update the manifest with the destination path
    """
    manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.RUNNING)

    # Find the actual audio file to copy
    source_file = _find_audio_file(source_path)
    if source_file is None:
        click.echo(f"  ERROR: No audio files found in {source_path}")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.FAILED)
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

    # Search Audible for candidates
    audible_candidates = _search_audible(
        metadata["title"], metadata["series"], config,
    )

    # Pick best Audible match via fuzzy scoring
    audible_result = None
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
        else:
            # Try AI disambiguation for low-confidence matches
            client = get_client(config.openai_base_url, config.openai_api_key)
            ai_pick = disambiguate(
                scored[:5], metadata["title"], "",
                config.openai_model, client,
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
        client = get_client(config.openai_base_url, config.openai_api_key)
        ai_metadata = resolve(
            metadata, tag_metadata,
            audible_candidates, config.openai_model, client,
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
    dest_dir = build_plex_path(config.nfs_output_dir, metadata)
    dest_file_path = dest_dir / source_file.name

    # Check if file already exists in library
    if dest_file_path.exists():
        click.echo(f"  SKIPPED {source_file.name} -- already exists at")
        click.echo(f"          {dest_file_path}")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
        return

    if dry_run:
        click.echo(f"  [DRY-RUN] Would copy {source_file.name}")
        click.echo(f"         -> {dest_file_path}")
        manifest.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
        return

    click.echo(f"  Copying {source_file.name}")
    click.echo(f"       -> {dest_file_path}")
    dest_file = copy_to_library(source_file, dest_dir, dry_run=False)

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

    audio_exts = {".m4b", ".mp3", ".m4a", ".flac"}
    audio_files = [
        f for f in source_path.rglob("*")
        if f.suffix.lower() in audio_exts
    ]
    return audio_files[0] if audio_files else None


def _search_audible(
    title: str,
    series: str,
    config: PipelineConfig,
) -> list[dict]:
    """Search Audible with multiple query strategies, dedupe by ASIN."""
    queries = [title]
    if series:
        queries.append(series)
        queries.append(f"{series} {title}")

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
