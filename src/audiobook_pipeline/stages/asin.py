"""Stage 04: ASIN -- resolve audiobook metadata via Audible, AI, and tags.

Searches the Audible catalog, scores results with fuzzy matching,
uses AI disambiguation when scores are low, and falls back to embedded
tags or path-parsed metadata. Writes parsed_author, parsed_title,
parsed_series, parsed_position, parsed_asin, parsed_narrator,
parsed_year, cover_url, and expanded metadata (parsed_subtitle,
parsed_description, parsed_publisher, parsed_copyright, parsed_language,
parsed_genre) to the manifest.

Runs before metadata tagging so the file can be tagged before it lands
in the library. Also runs in reorganize mode (before ORGANIZE stage)
to populate metadata for correct library placement.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from ..ai import disambiguate, get_client, needs_resolution, resolve
from ..api.audible import search
from ..api.search import score_results
from ..ffprobe import extract_author_from_tags, get_tags
from ..models import Stage, StageStatus
from ..ops.organize import parse_path

if TYPE_CHECKING:
    from ..config import PipelineConfig
    from ..pipeline_db import PipelineDB

log = logger.bind(stage="asin")


def run(
    source_path: Path,
    book_hash: str,
    config: PipelineConfig,
    manifest: PipelineDB,
    dry_run: bool = False,
    verbose: bool = False,
    **kwargs,
) -> None:
    """Resolve audiobook metadata and persist to manifest.

    1. Parse the source path into author/title/series metadata
    2. Read embedded tags via ffprobe
    3. Search Audible with multiple query strategies
    4. Score results with fuzzy matching, AI disambiguate if below threshold
    5. AI resolution when sources conflict (or ai_all=True)
    6. Best-source fallback for author
    7. Persist parsed metadata + cover_url to manifest
    """
    manifest.set_stage(book_hash, Stage.ASIN, StageStatus.RUNNING)

    # Determine the audio file to inspect for tags
    # In convert mode, use the convert output; otherwise use source_path
    data = manifest.read(book_hash)
    if not data:
        click.echo(f"  ERROR: No manifest data for {book_hash}")
        manifest.set_stage(book_hash, Stage.ASIN, StageStatus.FAILED)
        return

    convert_output = data.get("stages", {}).get("convert", {}).get("output_file", "")
    tag_file: Path | None
    if convert_output and Path(convert_output).exists():
        tag_file = Path(convert_output)
    elif source_path.is_file():
        tag_file = source_path
    else:
        # Directory -- find an audio file to read tags from
        tag_file = _find_tag_file(source_path)

    # Parse path into metadata
    if source_path.is_dir():
        # Use directory name for richer context even if audio is nested
        if tag_file:
            parse_target = source_path / tag_file.name
        else:
            parse_target = source_path / source_path.name
        metadata = parse_path(str(parse_target), source_dir=source_path)
    else:
        metadata = parse_path(str(source_path))

    # Gather evidence from tags
    tag_author = ""
    tag_metadata = {"author": "", "title": "", "album": ""}
    if tag_file and tag_file.exists():
        try:
            tags = get_tags(tag_file)
            tag_author = extract_author_from_tags(tags)
        except FileNotFoundError:
            raise  # ffprobe missing -- fail fast
        except Exception as e:
            log.warning(f"Failed to read tags from {tag_file.name}: {e}")
            tags = {}
            tag_author = ""
        else:
            tag_metadata = {
                "author": tag_author or "",
                "title": tags.get("title", ""),
                "album": tags.get("album", ""),
            }

    # Use tag title if path title looks like junk
    source_stem = tag_file.stem if tag_file else source_path.stem
    if tag_metadata["title"] and len(tag_metadata["title"]) > 3:
        if metadata["title"] == source_stem:
            metadata["title"] = tag_metadata["title"]

    # Search Audible for candidates
    audible_candidates = _search_audible(
        metadata["title"],
        metadata["series"],
        config,
        author=metadata.get("author", ""),
        widen=bool(config.pipeline_llm_base_url),
    )

    # Pick best Audible match via fuzzy scoring
    audible_result = None
    cover_url = ""
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
                "narrator": best.get("narrator_str", ""),
                "year": best.get("year", ""),
            }
            cover_url = best.get("cover_url", "")
            log.debug(
                f"Audible match: {best['author_str']!r} (score={best['score']:.0f})"
            )
        elif has_ai:
            client = get_client(
                config.pipeline_llm_base_url, config.pipeline_llm_api_key
            )
            ai_pick = disambiguate(
                scored[:5],
                metadata["title"],
                "",
                config.pipeline_llm_model,
                client,
            )
            if ai_pick:
                audible_result = {
                    "author": ai_pick["author_str"],
                    "asin": ai_pick["asin"],
                    "title": ai_pick["title"],
                    "series": ai_pick.get("series", ""),
                    "position": ai_pick.get("position", ""),
                    "narrator": ai_pick.get("narrator_str", ""),
                    "year": ai_pick.get("year", ""),
                }
                cover_url = ai_pick.get("cover_url", "")
                log.debug(f"AI disambiguated: {ai_pick['author_str']!r}")

    # Decide if AI should resolve metadata
    should_resolve = config.ai_all or needs_resolution(
        metadata,
        tag_metadata,
        audible_result,
    )

    if should_resolve:
        client = get_client(config.pipeline_llm_base_url, config.pipeline_llm_api_key)
        ai_metadata = resolve(
            metadata,
            tag_metadata,
            audible_candidates,
            config.pipeline_llm_model,
            client,
            source_filename=source_stem,
            source_directory=str(source_path),
        )
        if ai_metadata:
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
        # No AI needed -- apply best available source
        if audible_result and audible_result.get("author"):
            metadata["author"] = audible_result["author"]
            # Also apply series/position from Audible if not already set
            for key in ("title", "series", "position"):
                if audible_result.get(key) and not metadata.get(key):
                    metadata[key] = audible_result[key]
        elif tag_author:
            metadata["author"] = tag_author

    # Canonicalize author against library index (alias DB + surname matching)
    index = kwargs.get("index")
    if index and metadata["author"]:
        metadata["author"] = index.match_author(metadata["author"])

    log.debug(
        f"Final: author={metadata['author']!r} title={metadata['title']!r} "
        f"series={metadata['series']!r} pos={metadata['position']!r}"
    )

    # Persist to manifest
    data = manifest.read(book_hash)
    if data:
        # Find the best Audible candidate for extended metadata
        best_candidate = _find_best_candidate(audible_result, audible_candidates)

        data["metadata"].update(
            {
                "parsed_author": metadata["author"],
                "parsed_title": metadata["title"],
                "parsed_series": metadata["series"],
                "parsed_position": metadata["position"],
                "parsed_asin": (audible_result["asin"] if audible_result else ""),
                "parsed_narrator": (
                    audible_result.get("narrator", "") if audible_result else ""
                ),
                "parsed_year": (
                    audible_result.get("year", "") if audible_result else ""
                ),
                "cover_url": cover_url,
                "parsed_subtitle": best_candidate.get("subtitle", ""),
                "parsed_description": best_candidate.get("publisher_summary", ""),
                "parsed_publisher": best_candidate.get("publisher_name", ""),
                "parsed_copyright": best_candidate.get("copyright", ""),
                "parsed_language": best_candidate.get("language", ""),
                "parsed_genre": best_candidate.get("genre", ""),
            }
        )
        manifest.update(book_hash, data)

    # Download and cache cover art in the database (no NFS temp files)
    if cover_url and not dry_run:
        try:
            import httpx

            resp = httpx.get(cover_url, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
            manifest.store_cover(book_hash, resp.content)
            log.info(f"Cover art cached: {len(resp.content)} bytes")
        except Exception as e:
            log.warning(f"Cover art download failed (non-fatal): {e}")

    manifest.set_stage(book_hash, Stage.ASIN, StageStatus.COMPLETED)
    click.echo(f"  ASIN resolved: {metadata['author']!r} - {metadata['title']!r}")


def _find_best_candidate(
    audible_result: dict | None,
    audible_candidates: list[dict],
) -> dict:
    """Find the best Audible candidate for extended metadata fields.

    If we have a resolved result with an ASIN, find the matching candidate
    from the full search results (which has the extended fields). Falls
    back to empty dict if no match.
    """
    if not audible_result or not audible_result.get("asin"):
        return {}
    asin = audible_result["asin"]
    for candidate in audible_candidates:
        if candidate.get("asin") == asin:
            return candidate
    return {}


def _find_tag_file(source_path: Path) -> Path | None:
    """Find an audio file in a directory to read tags from."""
    from ..models import AUDIO_EXTENSIONS

    m4b_files = list(source_path.rglob("*.m4b"))
    if m4b_files:
        return max(m4b_files, key=lambda f: f.stat().st_size)
    audio_files = [
        f for f in source_path.rglob("*") if f.suffix.lower() in AUDIO_EXTENSIONS
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
    log.debug(
        f"_search_audible: title={title!r} series={series!r} "
        f"author={author!r} widen={widen}"
    )

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

    log.debug(f"_search_audible: found {len(all_results)} unique results")
    return all_results
