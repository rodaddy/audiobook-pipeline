# Audiobook Pipeline Briefing

**Last Updated:** 2026-02-22

## Project Overview

Audiobook processing pipeline for m4b/mp3 conversion, metadata enrichment via Audible API + OpenAI LLM, and Plex-compatible organization. Python-based CLI tool with AI-powered metadata resolution, fuzzy matching, and CPU-aware parallel batch processing.

## Current State

**Branch:** feat/python-rewrite (PR #5 open, awaiting review)
**Status:** Full convert mode implemented, pipeline reordered for better separation of concerns

### Recent Work (2026-02-22)

1. **Convert Mode Session (ce5c2584)** -- Implemented validate, concat, convert stages | Created ConvertOrchestrator for CPU-aware parallel batch processing | Fixed CPU monitoring and dry-run flow
2. **ASIN Stage Session (0838cfda)** -- Reordered pipeline to separate metadata resolution (ASIN stage), tagging (metadata stage), and file organization (organize stage) | Files now tagged before landing in library | Cover art embedded as attached_pic

**Pipeline Order:** validate -> concat -> convert -> asin -> metadata -> organize -> cleanup

**Test Coverage:** 37 tests covering all stages (validate, concat, convert, asin, metadata, organize)

## Architecture

```
src/audiobook_pipeline/
    __init__.py              -- Main docstring
    ai.py                    -- OpenAI SDK metadata resolution
    cli.py                   -- CLI entry point
    config.py                -- loguru setup + LLM config
    runner.py                -- Pipeline orchestration (single book)
    convert_orchestrator.py  -- CPU-aware parallel batch processing
    stages/
        validate.py          -- FFprobe validation, file discovery
        concat.py            -- FFmpeg concat lists for multi-file books
        convert.py           -- FFmpeg MP3 conversion (CBR/VBR)
        asin.py              -- Audible search + AI disambiguation + tag reading
        metadata.py          -- FFmpeg tagging + cover art embedding
        organize.py          -- Plex-compatible file organization
        archive.py           -- Source archival
        cleanup.py           -- Temp file cleanup
    api/
        audible.py           -- Audible catalog search (500/1024px covers)
        search.py            -- Fuzzy scoring + path hints
    ops/
        organize.py          -- Path parsing, Plex structure, dedup detection
```

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| openai SDK over httpx | Cleaner URL handling, built-in retries, standard interface |
| loguru over stdlib logging | Rotation, structured binding, zero-config |
| AI resolves ALL metadata | Single AI call, better consistency than piecemeal |
| Dedup via normalization | Simpler than fuzzy matching, strips years/punctuation/plurals |
| Widened search only with AI | No value in wider results without AI filtering |
| Separate ASIN/metadata/organize stages | Better separation of concerns, files tagged before library landing |
| Cover art embedded as attached_pic | FFmpeg -map 0 -map 1 -disposition:v:0 attached_pic |
| CPU-aware parallelism | psutil blocking 1s sample, conservative worker count |
| ASIN stage runs in dry-run | Metadata-only, no file changes |

## Known Issues / Warts

1. **SRoST inline series junk** -- AI didn't extract, parser can't reliably parse
2. **Subseries vs unified series** -- AI sometimes picks subseries name over unified series
3. **Underscores from sanitizer** -- Colon replacement creates underscores in folder names (cosmetic)
4. **Filenames unchanged** -- Still have original source names, would need dedicated rename logic
5. **mypy stubs missing** -- loguru and psutil (pre-existing, not new)

## Next Steps

1. E2E test convert mode with real audio files
2. Monitor batch conversion progress
3. Fix any conversion issues that surface
4. Review PR #5 and merge when ready

## Critical Context

- `.env` has PIPELINE_LLM_* env vars (not OPENAI_* to avoid collisions)
- `config.py` has `setup_logging()` -- called from cli.py after config creation
- `ai.resolve()` returns ALL metadata (author, title, series, position) -- single AI call with UUID nonce to defeat LiteLLM semantic cache
- `api/audible.search()` for Audible catalog search with fuzzy scoring
- `stages/asin.py` resolves metadata (Audible/AI/tags), writes to manifest
- `stages/metadata.py` embeds tags + cover art, writes output_file to manifest
- `stages/organize.py` pure file-mover, reads pre-resolved metadata from manifest
- `--ai-all` flag forces AI validation on every book (default: only conflicts)
- `ops/organize.py` has duplicate detection via `_reuse_existing()` at every dir level
- manifest.create() pre-completes CONVERT stage with output_file=source_path for non-convert modes -- must check is_file() not exists()

---

**Last session:** 2026-02-22 -- Batch Threading and Agent Hook Fixes (72924475)
**Done:** Monitored batch conversion (9+ books completed, 0 failures) | Optimized worker threading (4 workers, 3 threads each) | Added CONVERTIBLE_EXTENSIONS (exclude M4B-only dirs) | Implemented --resume flag | Fixed agent-aware hooks (3 PreToolUse hooks bypass for subagents) | Added targeted Bash permissions for agents
**PR:** https://github.com/rodaddy/audiobook-pipeline/pull/5 (awaiting review)
**Carry-forward:** Wait for batch conversion to complete | Review conversion results | Check for failures | Consider --status flag for monitoring
**Next:** Monitor batch completion, review results
