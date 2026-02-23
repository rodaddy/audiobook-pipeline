# audiobook_pipeline

Audiobook Pipeline -- convert, enrich, and organize audiobooks into tagged M4B files.

All modules use loguru for structured debug logging (stage-tagged via logger.bind).
Run with -v / --verbose for full DEBUG output tracing every function call.

Usage:
    # Convert directory of MP3s to M4B (auto-detects convert mode)
    uv run audiobook-convert /path/to/audiobook-mp3s/

    # Batch convert multiple books with CPU-aware parallelism
    uv run audiobook-convert --mode convert /path/to/incoming/

    # Enrich existing M4B with metadata (auto-detects enrich mode)
    uv run audiobook-convert /path/to/book.m4b

    # Organize library in-place (move misplaced books)
    uv run audiobook-convert --reorganize /Volumes/AudioBooks/

    # Preview changes without executing
    uv run audiobook-convert --dry-run --verbose /path/to/book/

    # Force re-processing
    uv run audiobook-convert --force /path/to/book/

    # Specify config file
    uv run audiobook-convert -c /path/to/custom.env /path/to/book/

CLI flags:
    -m, --mode {convert,enrich,metadata,organize}  Pipeline mode (auto-detected if omitted)
    --dry-run                                      Preview without making changes
    --force                                        Re-process even if completed
    -v, --verbose                                  Enable DEBUG logging
    -c, --config PATH                              Path to .env file
    --ai-all                                       Run AI validation on all books
    --reorganize                                   Move misplaced books (implies --ai-all)
    --verify                                       Run data quality checks after processing
    --no-lock                                      Skip file locking (batch mode)
    --asin TEXT                                    Override ASIN discovery

Modes:
    convert   -- Directory input: MP3/FLAC/etc -> M4B (validate -> concat -> convert ->
                 asin -> metadata -> organize -> cleanup)
    enrich    -- M4B input: resolve ASIN, tag metadata, organize (asin -> metadata ->
                 organize -> cleanup)
    metadata  -- Resolve and apply metadata only, no file move (asin -> metadata -> cleanup)
    organize  -- Resolve ASIN, tag metadata, then move into library structure
                 (asin -> metadata -> organize)

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
                           CPU load monitoring via psutil.cpu_percent(), and per-book
                           stage sequencing (validate -> concat -> convert -> asin ->
                           metadata -> organize -> cleanup). Cleans work dirs on
                           retag-in-place early exit and in dry-run mode. Returns
                           BatchResult summary.
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
                  asin for Audible resolution, AI disambiguation, and cover URL capture;
                  metadata tagging via ffmpeg -c copy with chapter preservation and
                  cover art embedding; organize as pure file-mover with index-aware
                  early-skip and reorganize mode; cleanup with work directory removal.
                  See stages/__init__.py for full stage docs.)
    ops        -- File operations (path parsing with pattern match logging and
                  author-only directory fallback, Plex library building, dedup detection,
                  move_in_library for reorganize mode with empty-dir cleanup, author
                  heuristic rejection reasons, filename renaming with year stripping
                  and series position prefix)
    automation -- Cron scanner and queue processor (planned)

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
