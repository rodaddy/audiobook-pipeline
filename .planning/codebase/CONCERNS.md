# Codebase Concerns

**Analysis Date:** 2026-02-21

## Tech Debt

**Duplicate AUDIO_EXTENSIONS constant:**
- Issue: `AUDIO_EXTENSIONS` is defined in both `src/audiobook_pipeline/models.py` (line 64) and `src/audiobook_pipeline/sanitize.py` (line 8), with slightly different values -- `models.py` omits `.m4b` while `sanitize.py` also omits `.m4b`. Meanwhile `runner.py` (line 41) and `stages/organize.py` (line 187) use inline sets `{".m4b", ".mp3", ".m4a", ".flac"}` that include `.m4b` but omit `.ogg` and `.wma`.
- Files: `src/audiobook_pipeline/models.py`, `src/audiobook_pipeline/sanitize.py`, `src/audiobook_pipeline/runner.py`, `src/audiobook_pipeline/stages/organize.py`
- Impact: Inconsistent file discovery -- a `.m4b` file is found by the runner's batch glob but would be missed by `generate_book_hash()` in `sanitize.py` when computing directory hashes. An `.ogg` file would be hashed but never discovered by the runner.
- Fix approach: Define a single canonical `AUDIO_EXTENSIONS` in `models.py` that includes `.m4b`. Import it everywhere. Remove the inline sets in `runner.py` and `stages/organize.py`.

**Only organize stage implemented:**
- Issue: The stage registry in `src/audiobook_pipeline/stages/__init__.py` returns `None` for all stages except `Stage.ORGANIZE`. The `convert`, `enrich`, `metadata`, `asin`, `validate`, `concat`, `archive`, and `cleanup` stages are all stubs.
- Files: `src/audiobook_pipeline/stages/__init__.py`
- Impact: Only `--mode organize` is functional. The `convert`, `enrich`, and `metadata` modes silently skip every stage except organize (and cleanup, also unimplemented). The runner logs "not yet implemented, skipping" at debug level -- easy to miss.
- Fix approach: Either implement the missing stages or raise a clear error when an unimplemented mode is selected, rather than silently skipping.

**Automation module is empty:**
- Issue: `src/audiobook_pipeline/automation/__init__.py` is a placeholder with planned submodules (scanner, queue) that don't exist.
- Files: `src/audiobook_pipeline/automation/__init__.py`
- Impact: No impact on current functionality, but config has `incoming_dir`, `queue_dir`, `processing_dir`, `completed_dir`, `failed_dir` settings that are unused dead config.
- Fix approach: Implement the automation submodules or remove the placeholder and unused config fields until needed.

**CLI sets env vars as side-effect for config:**
- Issue: `src/audiobook_pipeline/cli.py` (lines 100-108) mutates `os.environ` to pass CLI flags (`DRY_RUN`, `FORCE`, `VERBOSE`, `AI_ALL`) to `PipelineConfig`, rather than passing them as constructor kwargs.
- Files: `src/audiobook_pipeline/cli.py`
- Impact: Environment pollution -- these env vars persist for the process lifetime. Makes testing harder and creates coupling between CLI and config layers. Also means running multiple pipeline instances in the same process (e.g., tests) could leak state.
- Fix approach: Pass CLI flags directly as kwargs to `PipelineConfig()` constructor. Pydantic-settings supports this natively via `PipelineConfig(dry_run=True, force=True, ...)`.

**Custom .env loader duplicates pydantic-settings:**
- Issue: `_load_env_file()` in `src/audiobook_pipeline/cli.py` (lines 25-48) is a hand-rolled .env parser that runs before `PipelineConfig()`. But `PipelineConfig` already uses `pydantic-settings` with `env_file=".env"`.
- Files: `src/audiobook_pipeline/cli.py`
- Impact: Two different .env parsers with subtly different behavior. The hand-rolled one skips `${VAR:-default}` expansions; pydantic-settings has its own parsing rules. This creates confusion about which parser "wins" and makes it hard to reason about config precedence.
- Fix approach: Remove the custom loader. Pass the discovered env file path to `PipelineConfig(_env_file=env_file)` and let pydantic-settings handle it.

## Known Bugs

**Manifest read-modify-write race condition:**
- Symptoms: Under concurrent access, manifest updates can be lost. Each mutation method (`update`, `set_stage`, `set_error`, `increment_retry`) reads the full JSON, modifies it, then writes atomically -- but there's no locking between the read and write.
- Files: `src/audiobook_pipeline/manifest.py` (lines 121-200)
- Trigger: Two processes or threads processing the same book_hash simultaneously, or rapid sequential calls (e.g., `set_stage` followed immediately by `update`).
- Workaround: The global file lock in `concurrency.py` prevents concurrent pipeline instances, but within a single run, rapid sequential manifest writes could theoretically interleave if stage runners become async.

**ffprobe functions crash on empty output:**
- Symptoms: `ValueError` or `IndexError` when ffprobe returns empty stdout (e.g., corrupt file, missing ffprobe binary).
- Files: `src/audiobook_pipeline/ffprobe.py` (lines 17-67)
- Trigger: Call `get_duration()`, `get_bitrate()`, `get_channels()`, or `get_sample_rate()` on a corrupt or unsupported file. The `float()`, `int()` conversions will raise on empty strings.
- Workaround: `validate_audio_file()` can be called first, but nothing enforces this order. The organize stage calls `get_tags()` which does handle errors, but the raw numeric functions don't.

**`_normalize_for_compare` strips trailing 's' too aggressively:**
- Symptoms: Book titles or author names ending in 's' (e.g., "James", "Mass") get incorrectly normalized, causing false-positive folder dedup matches.
- Files: `src/audiobook_pipeline/ops/organize.py` (line 323)
- Trigger: Two different books where one title ends in 's' and the other doesn't -- e.g., "The Expanse" vs "The Expanse" (no issue), but "Mars" could match "Mar" in edge cases.
- Workaround: None. The normalization is simple `.rstrip("s")` which strips ALL trailing s characters.

## Security Considerations

**Audible API called without authentication:**
- Risk: The Audible catalog API at `api.audible.{region}` is called without any API key or auth token. This relies on the API being publicly accessible, which Amazon could restrict at any time.
- Files: `src/audiobook_pipeline/api/audible.py`
- Current mitigation: None. The pipeline degrades gracefully (returns empty results) but metadata resolution quality drops significantly.
- Recommendations: Add rate limiting. Consider caching Audible results to reduce API calls. Add a fallback metadata source (e.g., Google Books, OpenLibrary).

**AI API key passed as "not-needed" when empty:**
- Risk: `src/audiobook_pipeline/ai.py` (line 33) sets `api_key="not-needed"` when no key is configured. This is fine for local LiteLLM but could cause confusing auth failures if pointed at a real OpenAI endpoint.
- Files: `src/audiobook_pipeline/ai.py`
- Current mitigation: The `base_url` check gates AI usage -- if `PIPELINE_LLM_BASE_URL` is empty, AI is disabled entirely.
- Recommendations: Log a warning when `base_url` is set but `api_key` is empty.

**No input validation on source_path:**
- Risk: Path traversal via crafted filenames. The `sanitize_filename()` function strips slashes from output filenames, but source paths are used directly in `shutil.copy2()` and `subprocess.run()` calls.
- Files: `src/audiobook_pipeline/sanitize.py`, `src/audiobook_pipeline/ops/organize.py`
- Current mitigation: Click's `exists=True` validation ensures the source path exists, but doesn't prevent symlink-following or path traversal in intermediate operations.
- Recommendations: Resolve and validate that source paths are within expected directories before processing.

## Performance Bottlenecks

**Sequential Audible API calls in organize stage:**
- Problem: `_search_audible()` in `src/audiobook_pipeline/stages/organize.py` (lines 195-230) issues up to 5 sequential HTTP requests to the Audible API (one per query variant), each with a 30-second timeout.
- Files: `src/audiobook_pipeline/stages/organize.py`, `src/audiobook_pipeline/api/audible.py`
- Cause: Each query variant (`title`, `series`, `series + title`, `author + title`, `author + series`) is a separate blocking HTTP call.
- Improvement path: Use `httpx.AsyncClient` or batch queries with `asyncio.gather()`. Could also cache results by query to avoid repeated searches across batch runs.

**Manifest read-write amplification:**
- Problem: The organize stage reads the manifest 3-4 times and writes it 3-4 times for a single book (set_stage RUNNING, optional read in update, set_stage COMPLETED, plus the main update call).
- Files: `src/audiobook_pipeline/manifest.py`, `src/audiobook_pipeline/stages/organize.py`
- Cause: Each stage status change and metadata update triggers a full JSON read + write cycle.
- Improvement path: Buffer manifest changes in memory and flush once at stage completion. Or pass the manifest data dict through the stage and write once.

**`_reuse_existing()` scans entire directory on every call:**
- Problem: For each book being organized, `build_plex_path()` calls `_reuse_existing()` up to 3 times (author, series, title), each scanning the parent directory with `iterdir()`.
- Files: `src/audiobook_pipeline/ops/organize.py` (lines 327-344)
- Cause: No caching of directory listings. For a library with thousands of author folders, this is O(n) per book.
- Improvement path: Cache directory listings at the batch level. Build a normalized-name lookup dict once at batch start.

## Fragile Areas

**Path parser (`ops/organize.py` parse_path):**
- Files: `src/audiobook_pipeline/ops/organize.py` (lines 29-225)
- Why fragile: 431-line file with 7+ regex-based pattern branches (A through G) that cascade with implicit priority. Adding a new pattern or modifying an existing one can break other patterns. The fall-through logic between patterns C, D, and E is particularly interleaved.
- Safe modification: Always add new patterns BEFORE the fallback (Pattern D). Write test cases for the new pattern AND regression tests for existing patterns. Run the full organize test suite.
- Test coverage: No test file exists for `ops/organize.py` -- `tests/test_ops/` contains only `__init__.py`. Path parsing is the most complex logic in the codebase and has zero direct unit tests.

**AI response parsing:**
- Files: `src/audiobook_pipeline/ai.py` (lines 213-256)
- Why fragile: `_parse_resolve_response()` relies on the AI returning a specific line-by-line format (`AUTHOR: <name>`, `TITLE: <title>`, etc.). Different models or temperature settings could return slightly different formats (extra whitespace, markdown formatting, additional commentary).
- Safe modification: Add more robust parsing -- regex extraction instead of line-by-line splitting. Add test cases with various AI response formats.
- Test coverage: No test file exists for `ai.py` -- `tests/test_api/` is empty.

**Organize stage orchestration:**
- Files: `src/audiobook_pipeline/stages/organize.py` (lines 20-174)
- Why fragile: 230-line function with multiple conditional branches for AI resolution, Audible search, tag extraction, and fallback chains. The metadata priority logic (AI > Audible > tags > path) is spread across multiple if/elif blocks making it hard to trace which source "won".
- Safe modification: Extract the metadata resolution priority chain into a separate function. Add integration tests with mocked external calls.
- Test coverage: No test file exists in `tests/test_stages/`.

## Scaling Limits

**Batch processing is single-threaded:**
- Current capacity: Processes one audiobook at a time in `runner.py` batch mode.
- Limit: For large libraries (1000+ books), organize mode takes hours due to sequential Audible API calls and file copies.
- Scaling path: Add `--parallel N` flag. The global lock in `concurrency.py` would need per-book locking instead. File copies to NFS are I/O-bound and would benefit from parallelism.

**Manifest storage is one-file-per-book:**
- Current capacity: Works fine for hundreds of books.
- Limit: At 10k+ books, the manifest directory becomes slow to list/scan. No index or query capability.
- Scaling path: Migrate to SQLite for manifest storage. Keep JSON export for backward compatibility.

## Dependencies at Risk

**Audible catalog API (undocumented):**
- Risk: The Audible API at `api.audible.{region}/1.0/catalog/products` is not an official public API. Amazon could change, rate-limit, or remove it without notice.
- Impact: Metadata resolution quality degrades to path-parsing + tags only. Books with poor filenames end up in `_unsorted/`.
- Migration plan: Add OpenLibrary or Google Books as fallback. Cache successful Audible lookups to reduce API dependency.

## Missing Critical Features

**No cleanup stage:**
- Problem: Stage.CLEANUP is defined but not implemented. Work directories, temp files, and intermediate artifacts are never cleaned up.
- Blocks: Long-running pipeline instances accumulate disk usage in `work_dir`.

**No error recovery / retry:**
- Problem: The manifest tracks `retry_count` and `max_retries` but nothing reads these values. Failed books are logged to stderr and forgotten.
- Blocks: Transient failures (network timeout on Audible, NFS mount hiccup) require manual re-runs.

**No progress reporting:**
- Problem: Batch mode uses `click.echo()` for per-book output but no progress bar, ETA, or summary statistics beyond final ok/error counts.
- Blocks: Users can't estimate completion time for large batch runs.

## Test Coverage Gaps

**ops/organize.py -- zero tests:**
- What's not tested: Path parsing (patterns A-G), Plex path building, near-duplicate detection, file copying, all helper functions.
- Files: `src/audiobook_pipeline/ops/organize.py` (431 lines, 0 tests)
- Risk: The most complex module in the codebase has no regression tests. Any change to path parsing patterns risks silent breakage.
- Priority: **High** -- this is the core business logic.

**stages/organize.py -- zero tests:**
- What's not tested: The full organize orchestration flow, AI resolution integration, Audible search integration, metadata priority chain.
- Files: `src/audiobook_pipeline/stages/organize.py` (230 lines, 0 tests)
- Risk: Integration behavior between path parsing, tag extraction, Audible search, and AI resolution is untested.
- Priority: **High** -- this is the primary user-facing pipeline stage.

**ai.py -- zero tests:**
- What's not tested: AI client creation, needs_resolution logic, resolve prompt construction, response parsing, disambiguate flow.
- Files: `src/audiobook_pipeline/ai.py` (256 lines, 0 tests)
- Risk: AI response format changes or edge cases (empty response, malformed output) could silently produce bad metadata.
- Priority: **Medium** -- `_parse_resolve_response()` and `needs_resolution()` are pure functions that are easy to test.

**api/search.py and api/audible.py -- zero tests:**
- What's not tested: Fuzzy scoring logic, Audible API response parsing, error handling.
- Files: `src/audiobook_pipeline/api/search.py` (93 lines), `src/audiobook_pipeline/api/audible.py` (53 lines)
- Risk: Scoring weights or Audible response format changes could break metadata matching.
- Priority: **Medium** -- `score_results()` is a pure function, easy to test with mock data.

**runner.py -- zero tests:**
- What's not tested: Batch directory walking, stage execution loop, manifest creation/skip logic, dry-run mode.
- Files: `src/audiobook_pipeline/runner.py` (128 lines, 0 tests)
- Risk: Changes to stage execution order or batch processing logic could break without detection.
- Priority: **Medium**

---

*Concerns audit: 2026-02-21*
