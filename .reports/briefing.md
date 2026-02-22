# Audiobook Pipeline Briefing

**Last Updated:** 2026-02-21

## Project Overview

Audiobook processing pipeline for m4b/mp3 conversion, metadata enrichment via Audible API + OpenAI LLM, and Plex-compatible organization. Python-based CLI tool with AI-powered metadata resolution and fuzzy matching.

## Current State

**Branch:** feat/python-rewrite (ready for PR to main)
**Status:** Feature-complete, E2E tested on 65 live books

### Recent Work (2026-02-21)

Python rewrite now production-ready after two sessions:

1. **Restructure Session** -- Migrated to loguru logging, openai SDK, consolidated api/ layer (audible.py, search.py), added ai.py for metadata resolution
2. **Edge Cases Session** -- Hardened path parser (author heuristics, Pattern G, parenthesized series, title cleanup), added duplicate folder detection, widened Audible search with AI, live tested 65 books

**Live Test Results:** 65/65 books organized to `/Volumes/media_files/AudioBooks` with no duplicates, no errors

**Pre-commit Hooks:** Now generating requirements.txt from pyproject.toml

## Architecture

```
src/audiobook_pipeline/
    __init__.py         -- Main docstring
    ai.py               -- OpenAI SDK metadata resolution
    cli.py              -- CLI entry point
    config.py           -- loguru setup + OpenAI config
    runner.py           -- Pipeline orchestration
    stages/
        organize.py     -- Audible search + AI resolution + Plex organization
    api/
        audible.py      -- Audible catalog search
        search.py       -- Fuzzy scoring + path hints
    ops/
        organize.py     -- Path parsing, Plex structure, dedup detection
```

## Key Decisions

| Decision | Rationale |
|----------|-----------|
| openai SDK over httpx | Cleaner URL handling, built-in retries, standard interface |
| loguru over stdlib logging | Rotation, structured binding, zero-config |
| AI resolves ALL metadata | Single AI call, better consistency than piecemeal |
| Dedup via normalization | Simpler than fuzzy matching, strips years/punctuation/plurals |
| Widened search only with AI | No value in wider results without AI filtering |

## Known Issues / Warts

1. **SRoST inline series junk** -- AI didn't extract, parser can't reliably parse
2. **Subseries vs unified series** -- AI sometimes picks subseries name over unified series
3. **Underscores from sanitizer** -- Colon replacement creates underscores in folder names (cosmetic)
4. **Filenames unchanged** -- Still have original source names, would need dedicated rename logic

## Next Steps

1. Create PR for feat/python-rewrite -> main
2. Consider improvements for known issues (not blockers)
3. Add test cases for edge patterns discovered during live testing

## Critical Context

- `.env` has OPENAI_BASE_URL/KEY/MODEL (standard OpenAI env var names)
- `config.py` has `setup_logging()` -- called from cli.py after config creation
- `ai.resolve()` returns ALL metadata (author, title, series, position) -- single AI call
- `api/audible.search()` for Audible catalog search with fuzzy scoring
- `stages/organize.py` uses `ai` + `api` modules (no sys.path hack)
- `--ai-all` flag forces AI validation on every book (default: only conflicts)
- `ops/organize.py` has duplicate detection via `_reuse_existing()` at every dir level

---

**Last session:** 2026-02-22 -- Convert Mode with CPU-Aware Parallelism (ce5c2584)
**Done:** Implemented full convert pipeline (validate, concat, convert stages) | Created ConvertOrchestrator for CPU-aware parallel batch processing | Fixed CPU monitoring (psutil blocking 1s sample) + dry-run flow | Created 26 tests | Launched batch conversion of 142 books from Original/ (~27+ completed)
**Commits:** 99545f5, 4c31442, 5e0a0d4
**PR:** https://github.com/rodaddy/audiobook-pipeline/pull/5 (awaiting review)
**Blockers:** Batch conversion still running (background task b3e58c2)
**Carry-forward:** Run NewBooks/ batch after Original/ completes | Review failed books and fix issues | Consider filtering parent dirs (no audio) earlier | Add ASIN/metadata stages for convert mode | PR #5 awaiting review
**Next:** Monitor batch conversion progress, run NewBooks/ batch, fix any conversion issues
