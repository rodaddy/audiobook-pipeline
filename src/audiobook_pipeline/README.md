# audiobook_pipeline

Audiobook Pipeline -- convert, enrich, and organize audiobooks into tagged M4B files.

Core modules:
    config   -- Pipeline configuration via pydantic-settings
    cli      -- Click CLI entry point
    runner   -- Pipeline orchestration and stage execution
    ai       -- AI-assisted metadata resolution (OpenAI SDK)
    manifest -- JSON manifest state machine
    ffprobe  -- Audio file inspection via ffprobe subprocess

Subpackages:
    api        -- External API clients (Audible, fuzzy search)
    stages     -- Pipeline stages (organize, etc.)
    ops        -- File operations (path parsing, library copying)
    automation -- Cron scanner and queue processor (planned)

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
