# audiobook_pipeline

Audiobook Pipeline -- convert, enrich, and organize audiobooks into tagged M4B files.

Core modules:
    config        -- Pipeline configuration via pydantic-settings (PIPELINE_LLM_* env vars)
    cli           -- Click CLI entry point with auto mode detection, --reorganize flag.
                     CLI flags passed as kwargs to PipelineConfig (no env pollution).
    runner        -- Pipeline orchestration, stage execution, batch progress bar.
                     Builds LibraryIndex once for batch organize mode.
    library_index -- In-memory library index for O(1) folder/file lookups in batch mode.
                     Replaces per-call iterdir() with dict-based scans via os.walk().
                     Supports cross-source dedup, dynamic registration, reorganize detection.
    ai            -- AI-assisted metadata resolution via any OpenAI-compatible endpoint
                     (LiteLLM, OpenAI, Ollama). Includes cache-busting for semantic caches,
                     conflict resolution, and Audible disambiguation.
    manifest      -- JSON manifest state machine
    ffprobe       -- Audio file inspection via ffprobe subprocess. Numeric functions raise
                     ValueError on empty ffprobe output (corrupt files, missing binary).
    sanitize      -- Filename sanitization for filesystem safety

Subpackages:
    api        -- External API clients (Audible catalog search, fuzzy scoring)
    stages     -- Pipeline stages (organize with index-aware early-skip, reorganize move)
    ops        -- File operations (path parsing, Plex library building, dedup detection,
                  move_in_library for reorganize mode with empty-dir cleanup)
    automation -- Cron scanner and queue processor (planned)

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
