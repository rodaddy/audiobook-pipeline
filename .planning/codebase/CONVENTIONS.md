# Coding Conventions

**Analysis Date:** 2026-02-21

## Naming Patterns

**Files:**
- Use `snake_case.py` for all module files
- Package directories use `snake_case`
- Test files mirror source: `test_{module}.py` in `tests/`
- Test subdirectories mirror source packages: `tests/test_api/`, `tests/test_ops/`, `tests/test_stages/`

**Functions:**
- Use `snake_case` for all functions
- Private/internal functions prefixed with underscore: `_run_ffprobe()`, `_clean_author_tag()`, `_looks_like_author()`
- Public API functions are short verbs: `search()`, `resolve()`, `run()`
- Helper factory functions: `get_client()`, `get_duration()`, `get_tags()`

**Variables:**
- Use `snake_case` for all variables
- Constants use `UPPER_SNAKE_CASE`: `AUDIO_EXTENSIONS`, `STAGE_ORDER`, `PRE_COMPLETED_STAGES`
- Private module-level constants prefixed with underscore: `_ROLE_WORDS`, `_LABEL_SUFFIXES`, `_GENERIC_BASENAMES`
- Config env vars list: `_CONFIG_ENV_VARS` (underscore-prefixed)

**Types/Classes:**
- Use `PascalCase` for classes: `PipelineConfig`, `PipelineRunner`, `Manifest`
- Use `PascalCase` for enums: `PipelineMode`, `StageStatus`, `ErrorCategory`, `Stage`
- Enums inherit from `StrEnum` for string compatibility
- Exception classes end with `Error`: `PipelineError`, `StageError`, `ManifestError`, `LockError`

## Code Style

**Formatting:**
- No explicit formatter config (no `.prettierrc`, `ruff.toml`, or `.flake8`)
- Consistent 4-space indentation (Python standard)
- Trailing commas in multi-line function args and collections
- Line length appears ~88-100 chars (no enforced limit detected)

**Linting:**
- No linter config files present
- Pre-commit hooks exist but only for README generation and requirements sync (`.pre-commit-config.yaml`)

**Type Hints:**
- Use `from __future__ import annotations` when forward references needed (see `src/audiobook_pipeline/ai.py`)
- Use modern union syntax: `str | None`, `dict | None`, `Path | None`
- Use `-> None` for void returns
- Use `-> dict` or `-> list[dict]` for return types
- Pydantic fields use direct type annotations in `PipelineConfig`

## Import Organization

**Order:**
1. Standard library imports (`json`, `os`, `re`, `hashlib`, `subprocess`, `sys`, `shutil`)
2. Third-party imports (`click`, `httpx`, `loguru`, `pydantic_settings`, `rapidfuzz`, `openai`)
3. Relative package imports (`from .config import ...`, `from ..models import ...`)

**Style:**
- Prefer `from X import Y` over `import X` for specific items
- Group multiple imports from same module: `from .models import (Stage, StageStatus, ErrorCategory)`
- Use relative imports within the package: `from ..sanitize import sanitize_filename`
- Lazy imports for heavy dependencies: `from openai import OpenAI` inside function body (`src/audiobook_pipeline/ai.py:26`)

**Path Aliases:**
- None used. All imports are relative within the package.

## Error Handling

**Exception Hierarchy:**
- All pipeline exceptions inherit from `PipelineError` (`src/audiobook_pipeline/errors.py`)
- Domain-specific exceptions: `ConfigError`, `ManifestError`, `StageError`, `ExternalToolError`
- `StageError` carries structured data: `stage`, `exit_code`, `category` (transient/permanent)
- `ExternalToolError` carries: `tool`, `exit_code`, `stderr`
- `LockError` is standalone in `src/audiobook_pipeline/concurrency.py`

**Patterns:**
- External API calls wrapped in try/except returning empty list on failure (`src/audiobook_pipeline/api/audible.py:33`)
- AI calls wrapped in try/except returning None on failure (`src/audiobook_pipeline/ai.py:155`)
- Manifest operations raise `ManifestError` for missing data (`src/audiobook_pipeline/manifest.py:124`)
- Subprocess failures raise `ExternalToolError` with stderr capture (`src/audiobook_pipeline/runner.py:122-127`)
- Graceful degradation: AI/Audible failures fall through to next-best source, never crash the pipeline

**Exit Code Categorization:**
- Codes 2, 3 = permanent (bad input) -- do not retry
- All other non-zero = transient -- retry up to `max_retries`
- Defined in `src/audiobook_pipeline/errors.py:44-52`

## Logging

**Framework:** Loguru (`from loguru import logger`)

**Patterns:**
- Bind a stage context per module: `log = logger.bind(stage="organize")` at module level
- Use bound logger throughout module: `log.debug(...)`, `log.info(...)`, `log.warning(...)`
- User-facing output uses `click.echo()`, not logger (see `src/audiobook_pipeline/stages/organize.py`)
- Log format includes stage field: `{time} | {level} | {stage} | {message}` (`src/audiobook_pipeline/config.py:103-106`)
- Default stage filter ensures missing `stage` extra doesn't crash: `record["extra"].setdefault("stage", "")`
- File sink: DEBUG level, 10MB rotation, 30-day retention
- Stderr sink: configurable level (default INFO)

**When to use which:**
- `click.echo()` -- user-visible progress/status output
- `log.debug()` -- internal decision tracing (metadata resolution, stage skipping)
- `log.info()` -- significant state changes (AI resolved metadata)
- `log.warning()` -- recoverable failures (API errors, AI failures)

## Comments

**When to Comment:**
- Module-level docstrings on every file (required -- drives README generation)
- Function docstrings on all public functions
- Inline comments for non-obvious logic (regex patterns, heuristics)
- Section headers using `# ---------------------------------------------------------------------------` dividers in longer files

**Docstring Style:**
- Triple-quoted, imperative mood: `"""Parse a source path into structured metadata."""`
- Multi-line docstrings have blank line after summary, then details
- Parameter docs are informal (no `:param:` or `Args:` sections)
- Return value documented in prose when non-obvious

**`__init__.py` Docstrings (CRITICAL):**
- Every package `__init__.py` has a detailed docstring listing submodules
- These docstrings drive the pre-commit `gen-readme.py` hook
- Update `__init__.py` docstrings whenever module functionality changes

## Function Design

**Size:** Functions stay focused. Longest file is `src/audiobook_pipeline/ops/organize.py` at ~430 lines but individual functions are well-scoped.

**Parameters:**
- Use keyword arguments with defaults for optional behavior: `dry_run: bool = False`, `skip: bool = False`
- Use `Path` objects for filesystem args, not strings
- Stage runner functions follow a standard signature: `run(source_path, book_hash, config, manifest, dry_run, verbose)`
- Config passed as `PipelineConfig` object, not individual settings

**Return Values:**
- Return `None` for "not found" / "unavailable" (e.g., `get_client()`, `resolve()`, `disambiguate()`)
- Return empty string `""` for "no value determined" in metadata fields
- Return empty list `[]` for "no results" from searches
- Return dict with consistent keys for metadata: `{"author": "", "title": "", "series": "", "position": ""}`

## Module Design

**Exports:**
- No `__all__` declarations used
- Public API is implicit: non-underscore functions
- Internal helpers prefixed with underscore

**Barrel Files:**
- `__init__.py` files contain docstrings only (no re-exports) except `src/audiobook_pipeline/stages/__init__.py` which has `get_stage_runner()`
- Imports go directly to the module: `from audiobook_pipeline.config import PipelineConfig`

## Configuration Pattern

**Pydantic Settings:**
- Single `PipelineConfig` class using `pydantic-settings` (`src/audiobook_pipeline/config.py`)
- Reads `.env` file and environment variables with layered priority: `.env < env vars < constructor kwargs`
- Test isolation: pass `_env_file=None` to constructor to prevent `.env` loading
- AI config uses `PIPELINE_LLM_*` prefix to avoid collisions with global `OPENAI_*` vars

## Data Types

**Enums:**
- All enums use `StrEnum` for JSON/string compatibility (`src/audiobook_pipeline/models.py`)
- Constants derived from enums: `STAGE_ORDER`, `PRE_COMPLETED_STAGES`

**Collections:**
- Use `frozenset` for immutable constant sets: `AUDIO_EXTENSIONS`, `_ROLE_WORDS`, `_LABEL_SUFFIXES`
- Use `dict` for metadata passing between functions (not dataclasses/TypedDicts)

**Paths:**
- Always use `pathlib.Path`, never raw strings for filesystem operations
- String conversion only at boundaries: `str(file)` for subprocess args, `str(source_path)` for manifest storage

---

*Convention analysis: 2026-02-21*
