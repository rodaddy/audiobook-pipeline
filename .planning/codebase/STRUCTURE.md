# Codebase Structure

**Analysis Date:** 2026-02-21

## Directory Layout

```
audiobook-pipeline/
├── bin/                    # Executable entry points (bash legacy + Python CLI)
├── lib/                    # Bash library functions (legacy, being replaced)
├── stages/                 # Bash stage scripts (legacy, being replaced)
├── src/                    # Python package source
│   └── audiobook_pipeline/ # Main Python package
│       ├── api/            # External API clients
│       ├── automation/     # Cron/queue automation (planned)
│       ├── ops/            # File operations and path logic
│       └── stages/         # Pipeline stage implementations
├── tests/                  # Python test suite
│   ├── test_api/           # API client tests
│   ├── test_ops/           # Operations tests
│   └── test_stages/        # Stage tests
├── scripts/                # Build/dev helper scripts
├── docs/                   # Development documentation
│   └── development/        # Phase planning docs
├── logrotate.d/            # Log rotation configs
├── .planning/              # GSD planning documents
│   └── codebase/           # Architecture analysis (this file)
└── .reports/               # Session reports
```

## Directory Purposes

**`src/audiobook_pipeline/`:**
- Purpose: Core Python package -- all pipeline logic lives here
- Contains: Modules for config, CLI, runner, AI, manifest, ffprobe, sanitize, concurrency
- Key files:
  - `cli.py`: Click CLI entry point
  - `runner.py`: Pipeline orchestration
  - `config.py`: `PipelineConfig` pydantic-settings model
  - `models.py`: Enums (`PipelineMode`, `Stage`, `StageStatus`), stage ordering constants
  - `ai.py`: LLM-based metadata resolution
  - `manifest.py`: JSON state machine for per-book tracking
  - `ffprobe.py`: Audio file inspection wrappers
  - `sanitize.py`: Filename sanitization, book hash generation
  - `concurrency.py`: File locking, disk space checks
  - `errors.py`: Exception hierarchy

**`src/audiobook_pipeline/api/`:**
- Purpose: External API clients for metadata lookup
- Contains: Audible catalog search, fuzzy scoring
- Key files:
  - `audible.py`: HTTP client for Audible catalog API
  - `search.py`: rapidfuzz scoring and path hint extraction

**`src/audiobook_pipeline/stages/`:**
- Purpose: Individual pipeline stage implementations
- Contains: Stage runner functions, stage registry
- Key files:
  - `__init__.py`: `get_stage_runner()` registry (lazy imports)
  - `organize.py`: Organize stage -- metadata gathering, AI resolution, library copy

**`src/audiobook_pipeline/ops/`:**
- Purpose: Pure file operation logic separated from stage orchestration
- Contains: Path parsing, Plex folder building, dedup detection
- Key files:
  - `organize.py`: `parse_path()`, `build_plex_path()`, `copy_to_library()`, pattern A-G parsers

**`src/audiobook_pipeline/automation/`:**
- Purpose: Planned automation components (not yet implemented in Python)
- Contains: Empty -- placeholder `__init__.py` only

**`bin/`:**
- Purpose: Executable scripts (both legacy bash and referenced by pyproject.toml)
- Contains: Main pipeline script, cron scanner, queue processor, Readarr hook
- Key files:
  - `audiobook-convert`: Original bash implementation (12K lines)
  - `cron-scanner.sh`: Watches incoming directory for new files
  - `queue-processor.sh`: Processes queued audiobooks
  - `readarr-hook.sh`: Readarr download webhook handler

**`lib/`:**
- Purpose: Bash library functions sourced by `bin/audiobook-convert`
- Contains: Modular bash functions for each concern
- Key files: `core.sh`, `manifest.sh`, `organize.sh`, `audible.sh`, `asin.sh`, `metadata.sh`, `ffmpeg.sh`, `sanitize.sh`, `concurrency.sh`, `error-recovery.sh`, `archive.sh`, `audnexus.sh`

**`stages/`:**
- Purpose: Bash stage scripts called by the legacy pipeline
- Contains: Numbered stage scripts matching `Stage` enum
- Key files: `01-validate.sh` through `09-cleanup.sh`

**`tests/`:**
- Purpose: pytest test suite for the Python package
- Contains: Unit tests organized by module
- Key files: `test_cli.py`, `test_config.py`, `test_models.py`, `test_manifest.py`, `test_ffprobe.py`, `test_sanitize.py`, `test_errors.py`, `test_concurrency.py`, `test_logging.py`

**`scripts/`:**
- Purpose: Development helper scripts
- Key files:
  - `gen-readme.py`: Generate README files from `__init__.py` docstrings
  - `gen-requirements.py`: Generate requirements.txt from pyproject.toml

## Key File Locations

**Entry Points:**
- `src/audiobook_pipeline/cli.py`: Python CLI entry point (`audiobook-convert` command)
- `bin/audiobook-convert`: Legacy bash entry point

**Configuration:**
- `src/audiobook_pipeline/config.py`: `PipelineConfig` class (all settings)
- `.env`: Runtime configuration (gitignored)
- `.env.example`: Template for required environment variables
- `pyproject.toml`: Package metadata, dependencies, build config

**Core Logic:**
- `src/audiobook_pipeline/runner.py`: Pipeline orchestration
- `src/audiobook_pipeline/stages/organize.py`: Main implemented stage
- `src/audiobook_pipeline/ops/organize.py`: Path parsing and library building
- `src/audiobook_pipeline/ai.py`: AI metadata resolution

**Testing:**
- `tests/test_*.py`: Unit tests (co-located by module name)
- `tests/test_api/`: API client tests
- `tests/test_ops/`: Operations tests
- `tests/test_stages/`: Stage tests

**State/Data:**
- Manifests: JSON files in `config.manifest_dir` (default `/var/lib/audiobook-pipeline/manifests/`)
- Logs: `config.log_dir/pipeline.log` (default `/var/log/audiobook-pipeline/`)

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (e.g., `organize.py`, `error_recovery.py`)
- Test files: `test_<module>.py` (e.g., `test_config.py`, `test_manifest.py`)
- Bash scripts: `kebab-case.sh` (e.g., `audiobook-convert`, `cron-scanner.sh`)
- Bash stages: `NN-name.sh` (e.g., `01-validate.sh`, `07-organize.sh`)

**Directories:**
- Python packages: `snake_case` (e.g., `audiobook_pipeline`, `test_api`)
- Top-level: lowercase (e.g., `bin`, `lib`, `stages`, `tests`, `scripts`)

**Classes:**
- `PascalCase` (e.g., `PipelineRunner`, `PipelineConfig`, `Manifest`)

**Functions:**
- `snake_case` (e.g., `parse_path`, `build_plex_path`, `get_stage_runner`)
- Private helpers: `_prefixed` (e.g., `_looks_like_author`, `_clean_author_tag`)

**Constants:**
- `UPPER_SNAKE_CASE` (e.g., `STAGE_ORDER`, `AUDIO_EXTENSIONS`, `PRE_COMPLETED_STAGES`)
- Private constants: `_UPPER_SNAKE_CASE` (e.g., `_LABEL_SUFFIXES`, `_ROLE_WORDS`)

**Enums:**
- `PascalCase` class, `UPPER_SNAKE_CASE` values (e.g., `PipelineMode.CONVERT`, `Stage.ORGANIZE`)

## Where to Add New Code

**New Pipeline Stage:**
1. Create stage runner in `src/audiobook_pipeline/stages/<stage_name>.py`
2. Implement `run(source_path, book_hash, config, manifest, dry_run, verbose)` function
3. Register in `src/audiobook_pipeline/stages/__init__.py` `get_stage_runner()`
4. Add tests in `tests/test_stages/test_<stage_name>.py`
5. Stage is already defined in `models.py` `Stage` enum and `STAGE_ORDER`

**New External API Client:**
1. Create client module in `src/audiobook_pipeline/api/<service>.py`
2. Use `httpx` for HTTP calls (not `requests`)
3. Return structured dicts, handle errors with try/except returning empty results
4. Add tests in `tests/test_api/test_<service>.py`

**New File Operation:**
1. Add to existing `src/audiobook_pipeline/ops/organize.py` or create new `ops/<operation>.py`
2. Keep pure logic -- no manifest updates, no logging side effects
3. Add tests in `tests/test_ops/`

**New Configuration Option:**
1. Add field to `PipelineConfig` in `src/audiobook_pipeline/config.py`
2. pydantic-settings auto-reads from env vars (field name uppercased)
3. Document in `.env.example`

**New Utility/Helper:**
1. Add to the appropriate existing module (`sanitize.py`, `ffprobe.py`, etc.)
2. If truly new concern, create top-level module in `src/audiobook_pipeline/`
3. Prefix private helpers with `_`

**New CLI Option:**
1. Add `@click.option()` decorator in `src/audiobook_pipeline/cli.py`
2. Pass through to `PipelineRunner` or set env var for `PipelineConfig`
3. Add test in `tests/test_cli.py`

## Special Directories

**`lib/` and `stages/` (Legacy Bash):**
- Purpose: Original bash implementation of the full pipeline
- Generated: No
- Committed: Yes
- Note: Being replaced by Python package in `src/`. Both implementations share manifest format. Do not add new features here -- implement in Python.

**`.venv/` and `.venv-pkg/`:**
- Purpose: Python virtual environments
- Generated: Yes (via `uv venv`)
- Committed: No (gitignored)

**`.planning/`:**
- Purpose: GSD planning and codebase analysis documents
- Generated: By GSD commands
- Committed: Yes

**`.reports/`:**
- Purpose: Session reports and analysis outputs
- Generated: By session workflows
- Committed: Yes (selectively)

---

*Structure analysis: 2026-02-21*
