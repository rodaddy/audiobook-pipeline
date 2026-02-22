# External Integrations

**Analysis Date:** 2026-02-21

## APIs & External Services

**Audible Catalog API:**
- Purpose: Search audiobook metadata (ASIN, title, authors, series, position)
- Client: `httpx` direct HTTP calls (no SDK)
- Implementation: `src/audiobook_pipeline/api/audible.py`
- Endpoint: `https://api.audible.{region}/1.0/catalog/products`
- Auth: None required (public catalog API)
- Config: `AUDIBLE_REGION` env var (default: `com`). Supports: com, co.uk, com.au, ca, de, fr, co.jp, in, it, es
- Timeout: 30 seconds
- Returns: Up to 10 results with contributors, media, product_desc, product_attrs, series

**OpenAI-Compatible LLM API:**
- Purpose: AI-assisted metadata disambiguation and conflict resolution
- SDK/Client: `openai` Python SDK (`src/audiobook_pipeline/ai.py`)
- Auth: `PIPELINE_LLM_API_KEY` env var (falls back to `"not-needed"` for local proxies)
- Endpoint: `PIPELINE_LLM_BASE_URL` env var
- Model: `PIPELINE_LLM_MODEL` env var (default: `haiku`)
- Default target: LiteLLM proxy at `10.71.20.53:4000` routing to Claude Haiku
- Cache busting: UUID nonce prepended to prompts + `Cache-Control: no-cache` header to defeat LiteLLM Redis semantic cache
- Two AI functions:
  - `resolve()` - Full metadata resolution from conflicting sources (max 150 tokens, temp 0.1)
  - `disambiguate()` - Pick best Audible match from candidates (max 10 tokens, temp 0)
- Optional: Pipeline works without AI (graceful degradation to fuzzy scoring)

**Audnexus API (configured but not yet implemented in Python):**
- Purpose: Fallback metadata source
- Config: `AUDNEXUS_REGION`, `AUDNEXUS_CACHE_DIR`, `AUDNEXUS_CACHE_DAYS` env vars exist in config
- Status: Config fields defined in `src/audiobook_pipeline/config.py` but no client implementation found

## Data Storage

**Databases:**
- None. All state stored as JSON manifest files on local filesystem.

**Manifest Storage:**
- Type: JSON files, one per book
- Location: `MANIFEST_DIR` (default: `/var/lib/audiobook-pipeline/manifests`)
- Implementation: `src/audiobook_pipeline/manifest.py`
- Format: Compatible with original bash implementation (interchangeable)
- Write strategy: Atomic writes via `tempfile.mkstemp` + `os.replace`

**File Storage:**
- Local filesystem + NFS mount
- Input: `INCOMING_DIR` for automation scanner
- Working: `WORK_DIR` for intermediate files during processing
- Output: `NFS_OUTPUT_DIR` for Plex/Audiobookshelf library (structure: `Author/[Series/]Title/Book.m4b`)
- Archive: `ARCHIVE_DIR` with configurable retention (`ARCHIVE_RETENTION_DAYS`)

**Caching:**
- Audnexus responses cached locally (configured via `AUDNEXUS_CACHE_DIR` and `AUDNEXUS_CACHE_DAYS`, not yet implemented in Python)

## Authentication & Identity

**Auth Provider:**
- None. Pipeline is a local CLI tool with no user authentication.
- LLM API key is the only credential (`PIPELINE_LLM_API_KEY`)

## Monitoring & Observability

**Error Tracking:**
- Custom error hierarchy in `src/audiobook_pipeline/errors.py`
- Errors categorized as TRANSIENT (retriable) or PERMANENT (bad input)
- Retry tracking in manifest (`retry_count`, `max_retries`)

**Logs:**
- Loguru with structured format: `{time} | {level} | {stage} | {message}`
- Console: configurable level via `LOG_LEVEL`
- File: `LOG_DIR/pipeline.log`, DEBUG level, 10MB rotation, 30-day retention
- Stage-scoped logging via `logger.bind(stage="...")`

**Notifications (configured, not yet implemented):**
- `FAILURE_WEBHOOK_URL` - Slack/Discord webhook for failure alerts
- `FAILURE_EMAIL` - Email notifications (future)

## CI/CD & Deployment

**Hosting:**
- Self-hosted Linux server (paths suggest `/opt/audiobook-pipeline/` deployment)
- NFS mount to media server for library output

**CI Pipeline:**
- pre-commit hooks only (gen-readme, gen-requirements)
- No CI/CD service detected (no `.github/workflows/`, no `.gitlab-ci.yml`)

## Environment Configuration

**Required env vars:**
- None strictly required (all have defaults in `PipelineConfig`)

**Recommended for production:**
- `WORK_DIR` - Working directory for intermediate files
- `OUTPUT_DIR` - Final output directory
- `NFS_OUTPUT_DIR` - Plex library root (NFS mount)
- `PIPELINE_LLM_BASE_URL` - LLM endpoint (for AI-assisted metadata)
- `PIPELINE_LLM_API_KEY` - LLM auth key

**Secrets:**
- `.env` file (gitignored)
- Only secret: `PIPELINE_LLM_API_KEY`

## Webhooks & Callbacks

**Incoming:**
- Readarr webhook receiver (planned, referenced in `.env.example` comments)
- Not yet implemented in Python (automation package is stub)

**Outgoing:**
- `FAILURE_WEBHOOK_URL` - Slack/Discord failure notifications (configured, not yet implemented)

## External CLI Tool Dependencies

**FFmpeg/ffprobe:**
- Called via `subprocess.run` from `src/audiobook_pipeline/ffprobe.py`
- Used for: duration, bitrate, codec detection, channel count, sample rate, tag extraction, chapter counting, audio validation
- Must be on system PATH
- No version pinning -- uses standard ffprobe JSON output format

---

*Integration audit: 2026-02-21*
