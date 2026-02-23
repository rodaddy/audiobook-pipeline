# Session: Audiobook Pipeline Python Restructure

**Date:** 2026-02-21
**Branch:** feat/python-rewrite (parent repo), main (audiobook-pipeline submodule)
**Goal:** Restructure audiobook pipeline from legacy Python scripts into proper Python app with loguru, openai SDK, consolidated api/ layer

## What Was Done

### Completed (Tasks 1-7)

1. **pyproject.toml** -- Added `loguru>=0.7` and `openai>=1.0` to dependencies. Removed broken `audiobook-scan`/`audiobook-queue` entry points. Added `pre-commit>=3.0` to dev deps. Bumped version to 1.1.0.

2. **Config file rename** -- Renamed `config.env.example` to `.env.example` with OPENAI_* vars and AI_ALL setting. Updated `.gitignore` to reference `.env` instead of `config.env`.

3. **config.py rewritten** -- Added `openai_base_url`, `openai_api_key`, `openai_model`, `ai_all`, `asin_search_threshold` fields. Added `setup_logging()` method using loguru with stderr + file sinks, rotation, retention.

4. **logging.py deleted** -- Replaced entirely by loguru in config.py. `test_logging.py` rewritten to test loguru configuration.

5. **api/audible.py created** -- Absorbed `search_audible()` from `python/asin_search.py`. Clean function using httpx, returns structured results with loguru logging.

6. **api/search.py created** -- Absorbed `score_results()`, `parse_source_path()`, `_strip_series_numbers()` from `python/asin_search.py`. Rapidfuzz scoring (title 60%, author 30%, position 10%).

7. **ai.py created** -- New top-level AI module using `openai` SDK. Functions: `get_client()`, `needs_resolution()`, `resolve()` (ALL metadata, not just author), `disambiguate()`. Replaces raw httpx calls.

8. **stages/organize.py rewritten** -- Removed sys.path hack, imports from api/ and ai.py, uses loguru. AI resolves all metadata. config.ai_all controls behavior.

### Not Yet Done (Tasks 9-13)

9. **cli.py update** -- Remove config.env refs, add --ai-all flag, call setup_logging()
10. **runner.py loguru migration** -- Replace click.echo() with loguru for structured logging
11. **__init__.py docstrings** -- In progress by agent
12. **Pre-commit hooks** -- scripts/gen-readme.py, scripts/gen-requirements.py, .pre-commit-config.yaml
13. **python/ deletion + verification** -- Delete legacy dir, run full test suite

## Architecture After Changes

```
src/audiobook_pipeline/
    __init__.py
    ai.py              NEW -- OpenAI SDK metadata resolution
    cli.py             (needs --ai-all flag)
    config.py          UPDATED -- loguru setup + OpenAI fields
    runner.py           (needs loguru migration)
    stages/
        organize.py    (needs rewrite to use ai.py + api/)
    api/
        __init__.py    UPDATED with docstring
        audible.py     NEW -- Audible catalog search
        search.py      NEW -- Fuzzy scoring + path hints
    ops/
        organize.py    (unchanged -- path parsing, Plex structure)
```

## Key Decisions

- **openai SDK** over raw httpx for AI calls -- cleaner URL handling, built-in retries, standard interface
- **loguru** over stdlib logging -- rotation, structured binding, zero-config
- **AI resolves ALL metadata** (author, title, series, position) not just author -- single AI call, better consistency
- **ai_all flag** -- lets user force AI validation on every book, not just conflicts
- **OPENAI_BASE_URL/OPENAI_API_KEY** -- standard OpenAI env vars replace custom AI_API_URL/AI_API_KEY

## Files Modified

| File | Action |
|------|--------|
| `pyproject.toml` | Updated deps, version, removed broken scripts |
| `.gitignore` | config.env -> .env |
| `config.env.example` | Renamed to `.env.example`, added OPENAI_* vars |
| `src/audiobook_pipeline/config.py` | Added OpenAI fields, loguru setup |
| `src/audiobook_pipeline/logging.py` | DELETED |
| `src/audiobook_pipeline/ai.py` | NEW |
| `src/audiobook_pipeline/api/audible.py` | NEW |
| `src/audiobook_pipeline/api/search.py` | NEW |
| `src/audiobook_pipeline/api/__init__.py` | Updated with docstring |
| `tests/test_logging.py` | Rewritten for loguru |

## Resume Instructions

The full plan is in the plan mode transcript. To continue:

1. Read this session file for context
2. Check `TaskList` -- tasks 8-13 remain
3. Task 8 (organize.py rewrite) is the critical path -- depends on all completed tasks
4. Task 9 (cli.py) and 10 (runner.py) can be done in parallel after 8
5. Run `uv sync` first -- deps already installed but verify
6. Run `uv run pytest tests/ -v` to verify completed work passes

## Verification Commands

```bash
uv sync
uv run pytest tests/ -v
uv run audiobook-convert --help
uv run audiobook-convert /path/to/output --mode organize --dry-run --force --verbose
```
