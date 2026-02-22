"""Audiobook Pipeline -- convert, enrich, and organize audiobooks into tagged M4B files.

Core modules:
    config    -- Pipeline configuration via pydantic-settings (PIPELINE_LLM_* env vars)
    cli       -- Click CLI entry point with auto mode detection. CLI flags passed as
                 kwargs to PipelineConfig (no env pollution).
    runner    -- Pipeline orchestration and stage execution
    ai        -- AI-assisted metadata resolution via any OpenAI-compatible endpoint
                 (LiteLLM, OpenAI, Ollama). Includes cache-busting for semantic caches,
                 conflict resolution, and Audible disambiguation.
    manifest  -- JSON manifest state machine
    ffprobe   -- Audio file inspection via ffprobe subprocess. Numeric functions raise
                 ValueError on empty ffprobe output (corrupt files, missing binary).
    sanitize  -- Filename sanitization for filesystem safety

Subpackages:
    api        -- External API clients (Audible catalog search, fuzzy scoring)
    stages     -- Pipeline stages (organize, etc.)
    ops        -- File operations (path parsing, Plex library building, dedup detection)
    automation -- Cron scanner and queue processor (planned)
"""
