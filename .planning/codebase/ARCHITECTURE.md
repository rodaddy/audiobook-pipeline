# Architecture

**Analysis Date:** 2026-02-21

## Pattern Overview

**Overall:** Pipeline/Stage pattern with manifest-driven state machine

**Key Characteristics:**
- Sequential stage execution orchestrated by `PipelineRunner`
- JSON manifest files track per-book processing state (idempotent, resumable)
- Dual implementation: original bash scripts (`bin/`, `lib/`, `stages/`) being rewritten in Python (`src/audiobook_pipeline/`)
- Multi-source metadata resolution: path parsing + embedded tags + Audible API + AI disambiguation
- Configuration via pydantic-settings with layered resolution (.env < env vars < CLI flags)

## Layers

**CLI Layer:**
- Purpose: Parse arguments, detect mode, load config, invoke runner
- Location: `src/audiobook_pipeline/cli.py`
- Contains: Click command definition, .env file loading, auto-mode detection
- Depends on: `config`, `models`, `runner`
- Used by: Entry point `audiobook-convert` (defined in `pyproject.toml` `[project.scripts]`)

**Runner Layer:**
- Purpose: Orchestrate stage execution for a given source and mode
- Location: `src/audiobook_pipeline/runner.py`
- Contains: `PipelineRunner` class, batch directory walking, stage sequencing
- Depends on: `config`, `manifest`, `models`, `stages`, `sanitize`
- Used by: CLI layer

**Stage Layer:**
- Purpose: Implement individual pipeline stages (validate, convert, organize, etc.)
- Location: `src/audiobook_pipeline/stages/`
- Contains: Stage runner functions with signature `(source_path, book_hash, config, manifest, dry_run, verbose)`
- Depends on: `ai`, `api`, `config`, `ffprobe`, `manifest`, `ops`
- Used by: Runner layer via `get_stage_runner()` registry

**Operations Layer:**
- Purpose: Pure logic for file operations -- path parsing, library structure, dedup
- Location: `src/audiobook_pipeline/ops/`
- Contains: `parse_path()`, `build_plex_path()`, `copy_to_library()`, dedup helpers
- Depends on: `sanitize`
- Used by: Stage layer

**API Layer:**
- Purpose: External service clients for metadata lookup
- Location: `src/audiobook_pipeline/api/`
- Contains: Audible catalog search (`audible.py`), fuzzy scoring (`search.py`)
- Depends on: `httpx`, `rapidfuzz`
- Used by: Stage layer

**AI Layer:**
- Purpose: LLM-based metadata conflict resolution and disambiguation
- Location: `src/audiobook_pipeline/ai.py`
- Contains: `resolve()`, `disambiguate()`, `needs_resolution()`, `get_client()`
- Depends on: `openai` SDK (any compatible endpoint)
- Used by: Stage layer (organize stage)

**Infrastructure Layer:**
- Purpose: Cross-cutting utilities -- config, manifest, errors, sanitize, ffprobe, concurrency
- Location: `src/audiobook_pipeline/config.py`, `manifest.py`, `errors.py`, `sanitize.py`, `ffprobe.py`, `concurrency.py`
- Contains: Configuration model, JSON state machine, error hierarchy, filename safety, audio inspection, file locking
- Depends on: `pydantic-settings`, `loguru`, stdlib
- Used by: All layers

## Data Flow

**Organize Mode (primary implemented flow):**

1. CLI parses args, loads `.env`, creates `PipelineConfig` and `PipelineRunner`
2. Runner generates book hash via `generate_book_hash()` for idempotency
3. Runner creates/loads JSON manifest, iterates `STAGE_ORDER[mode]`
4. For each stage, checks manifest for completion, calls stage runner if not done
5. Organize stage: `parse_path()` extracts author/title/series/position from filesystem path
6. Organize stage: `get_tags()` + `extract_author_from_tags()` reads embedded audio metadata
7. Organize stage: `search()` queries Audible API, `score_results()` ranks matches via rapidfuzz
8. Organize stage: If metadata conflicts, `resolve()` calls LLM to pick best metadata
9. Organize stage: `build_plex_path()` constructs `Author/Series/Title/` directory, checking for near-dupes
10. Organize stage: `copy_to_library()` copies file to NFS destination
11. Manifest updated with final metadata and stage status

**Batch Mode:**
- When `source_path` is a directory and mode is `organize`, runner walks tree for audio files
- Each file processed independently with its own manifest entry
- Errors caught per-file, processing continues

**State Management:**
- Per-book JSON manifests in `config.manifest_dir` (keyed by 16-char SHA256 hash)
- Each manifest tracks all 8 stage statuses, metadata, errors, retry count
- Atomic writes via `tempfile.mkstemp()` + `os.replace()` for crash safety
- Manifest format is backward-compatible with the original bash implementation

## Key Abstractions

**PipelineMode:**
- Purpose: Defines which stages run for a given invocation
- Examples: `convert` (full pipeline), `enrich` (ASIN+metadata+organize), `organize` (organize+cleanup)
- Pattern: `StrEnum` with `STAGE_ORDER` dict mapping mode to ordered stage list

**Stage:**
- Purpose: Individual processing step in the pipeline
- Examples: `validate`, `concat`, `convert`, `asin`, `metadata`, `organize`, `archive`, `cleanup`
- Pattern: `StrEnum` with lazy-loaded runner functions via `get_stage_runner()`

**Manifest:**
- Purpose: Persistent per-book state machine enabling resume and idempotency
- Examples: `src/audiobook_pipeline/manifest.py`
- Pattern: JSON files with dotted-path field access, atomic writes, stage status tracking

**Metadata dict:**
- Purpose: Standard metadata representation passed between stages
- Examples: `{"author": "...", "title": "...", "series": "...", "position": "..."}`
- Pattern: Plain dict with string values, empty string = not determined

## Entry Points

**CLI (`audiobook-convert`):**
- Location: `src/audiobook_pipeline/cli.py` -> `main()`
- Triggers: User invocation via `audiobook-convert <path> [--mode] [--dry-run] [--force] [--verbose] [--ai-all]`
- Responsibilities: Parse args, load config, create runner, invoke pipeline

**Bash Entry Point (legacy):**
- Location: `bin/audiobook-convert`
- Triggers: Direct shell invocation (original bash implementation)
- Responsibilities: Same pipeline but implemented in bash, sources `lib/*.sh` and calls `stages/*.sh`

**Automation Scripts (legacy bash):**
- Location: `bin/cron-scanner.sh`, `bin/queue-processor.sh`, `bin/readarr-hook.sh`
- Triggers: Cron jobs, Readarr webhooks
- Responsibilities: Watch incoming directory, queue files, invoke pipeline

## Error Handling

**Strategy:** Exception hierarchy with error categorization (transient vs permanent)

**Patterns:**
- `PipelineError` base class with subclasses: `ConfigError`, `ManifestError`, `StageError`, `ExternalToolError`
- `StageError` carries stage name, exit code, and `ErrorCategory` (transient/permanent)
- `ExternalToolError` wraps subprocess failures (ffmpeg, ffprobe)
- Exit codes 2-3 are permanent (bad input), all others are transient (retriable)
- `categorize_exit_code()` in `src/audiobook_pipeline/errors.py` maps codes to categories
- Batch processing catches exceptions per-file, continues processing remaining files
- Manifest tracks `last_error` and `retry_count` for recovery

## Cross-Cutting Concerns

**Logging:** Loguru with structured format `{time} | {level} | {stage} | {message}`. Each module binds a `stage` extra field. Dual output: stderr + rotating file (`pipeline.log`). Configured in `PipelineConfig.setup_logging()`.

**Validation:** Pydantic-settings for config validation. Audio file validation via ffprobe subprocess. Path/filename sanitization in `src/audiobook_pipeline/sanitize.py`.

**Authentication:** No user auth. Audible API is unauthenticated. LLM endpoint uses `PIPELINE_LLM_API_KEY` env var (can be "not-needed" for local proxies).

**Concurrency:** Global file lock (`fcntl.flock`) prevents multiple pipeline instances. Lock file at `config.lock_dir/pipeline.lock`. Disk space check before processing.

**Idempotency:** Book hash (SHA256 of path + size) keys manifest. Completed stages are skipped unless `--force`. File copy skipped if destination exists with same size.

---

*Architecture analysis: 2026-02-21*
