# audiobook-pipeline

Convert audio files (MP3, FLAC, etc.) to chaptered M4B audiobooks with Audible metadata.


> All LAWs (#!/bin/bash, protected branches, stack prefs) enforced via ~/.claude/CLAUDE.md and hooks.


## Quick Reference

- **Run:** `uv run audiobook-convert /path/to/audio/`
- **Config:** `.env` in project root
- **Levels:** simple | normal | ai | full (set via `PIPELINE_LEVEL` or `--level`)
- **Docs:** `docs/install.md` for full setup guide
- **Agent:** `.claude/agents/audiobook-guide.md` for interactive assistance

## Development

- Package manager: `uv` (never pip)
- Tests: `uv run pytest tests/`
- Type check: `uv run mypy src/`
- Format: `ruff format src/ tests/`

## Key Patterns

- Config via pydantic-settings: `.env` < env vars < CLI kwargs
- AI uses `PIPELINE_LLM_*` env vars (not `OPENAI_*`) to avoid collisions
- Library structure: `Author/Book (Year)/Book.m4b`
- `.author-override` marker file forces author folder for multi-author franchises
- SQLite-backed state (pipeline.db) -- no JSON manifests
