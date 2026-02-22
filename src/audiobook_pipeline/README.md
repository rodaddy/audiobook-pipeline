# audiobook_pipeline

Audiobook Pipeline -- convert, enrich, and organize audiobooks into tagged M4B files.

All modules use loguru for structured debug logging (stage-tagged via logger.bind).
Run with -v / --verbose for full DEBUG output tracing every function call.

Core modules:
    config              -- Pipeline configuration via pydantic-settings (PIPELINE_LLM_* env vars).
                           Includes parallel conversion settings (max_parallel_converts,
                           cpu_ceiling).
    cli                 -- Click CLI entry point with auto mode detection, --reorganize flag.
                           CLI flags passed as kwargs to PipelineConfig (no env pollution).
                           Logs mode detection, env loading, and flag resolution.
    runner              -- Pipeline orchestration, stage execution, batch progress bar.
                           Dispatches to ConvertOrchestrator for convert-mode batch runs.
                           Builds LibraryIndex once for batch organize mode. Logs stage
                           transitions, manifest skip reasons, and subprocess commands.
    convert_orchestrator -- CPU-aware parallel batch processor for audiobook conversion.
                           Manages ThreadPoolExecutor with dynamic thread allocation,
                           CPU load monitoring via os.getloadavg(), and per-book
                           stage sequencing (validate -> concat -> convert -> organize ->
                           cleanup). Returns BatchResult summary.
    library_index       -- In-memory library index for O(1) folder/file lookups in batch mode.
                           Replaces per-call iterdir() with dict-based scans via os.walk().
                           Supports cross-source dedup, dynamic registration, reorganize detection.
    ai                  -- AI-assisted metadata resolution via any OpenAI-compatible endpoint
                           (LiteLLM, OpenAI, Ollama). Includes cache-busting for semantic caches,
                           conflict resolution, and Audible disambiguation. Logs evidence sources,
                           resolution decisions, and parse failures.
    manifest            -- JSON manifest state machine. Logs all state transitions, I/O
                           operations, retries, and errors.
    ffprobe             -- Audio file inspection via ffprobe subprocess. Includes get_format_name()
                           for container format validation. Logs every subprocess call, tag
                           extraction, and parse result. Numeric functions raise ValueError on
                           empty ffprobe output (corrupt files, missing binary).
    sanitize            -- Filename sanitization and book hash generation. Logs truncation
                           events and hash results.
    concurrency         -- File locking and disk space checks. Logs lock acquisition and
                           space validation.

Subpackages:
    api        -- External API clients (Audible catalog search with query/result logging,
                  fuzzy scoring with match details)
    stages     -- Pipeline stages (validate, concat, convert for MP3-to-M4B conversion;
                  organize with index-aware early-skip, reorganize move; cleanup with
                  work directory removal. See stages/__init__.py for full stage docs.)
    ops        -- File operations (path parsing with pattern match logging, Plex library
                  building, dedup detection, move_in_library for reorganize mode with
                  empty-dir cleanup, author heuristic rejection reasons)
    automation -- Cron scanner and queue processor (planned)

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
