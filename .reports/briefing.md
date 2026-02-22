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

**Last session:** 2026-02-22 -- PR Creation (f681e9e3)
**Done:** Pushed feat/python-rewrite branch to origin | Created PR #5 with comprehensive test plan | All adversarial review findings fixed (78096ea)
**PR:** https://github.com/rodaddy/audiobook-pipeline/pull/5
**Stats:** 18 commits, 76 files changed, ~10,400 lines added
**Blockers:** None
**Carry-forward:** PR awaiting review | Consider LOW findings from adversarial review if desired (L1-L6, UI1-UI4) | Monitor for review feedback
**Next:** Python rewrite formally proposed for merge to main
