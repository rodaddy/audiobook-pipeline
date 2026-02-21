---
phase: 02-metadata-enrichment
plan: 02
subsystem: api
tags: [audnexus, curl, jq, caching, metadata, cover-art, bash]

# Dependency graph
requires:
  - phase: 01-core-conversion
    provides: lib/core.sh (logging), lib/ffmpeg.sh (get_duration)
provides:
  - "lib/audnexus.sh -- 6-function API client for Audnexus metadata, chapters, cover art"
  - "File-based JSON caching with configurable TTL"
  - "Shell-evaluable metadata extraction (eval-safe output)"
  - "Chapter timestamp conversion (HH:MM:SS.mmm format)"
affects: [02-metadata-enrichment, 03-plex-organization]

# Tech tracking
tech-stack:
  added: [audnexus-api]
  patterns: [file-cache-with-ttl, stat-flavor-detection, graceful-degradation]

key-files:
  created: [lib/audnexus.sh]
  modified: [config.env.example]

key-decisions:
  - "Detect stat flavor via stat --version (not uname) -- GNU coreutils in PATH on macOS breaks uname detection"
  - "All API failures use log_warn (not log_error) for graceful degradation"
  - "extract_metadata_fields outputs single-quoted shell assignments with proper escaping for eval safety"
  - "Genre field uses asin fallback since Audnexus genres are numeric IDs, not human-readable names"

patterns-established:
  - "Cache pattern: check file exists + age via stat, serve from cache or fetch+validate+write"
  - "Stat detection: test stat --version at source time, store in module-level variable"
  - "API degradation: log_warn + return 1, never crash the pipeline on API failure"

# Metrics
duration: 4min
completed: 2026-02-20
---

# Plan 02-02: Audnexus API Client Summary

**lib/audnexus.sh with 6 functions -- book metadata, chapters, cover art, caching, timestamp conversion, eval-safe field extraction**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-20T20:14:00Z
- **Completed:** 2026-02-20T20:18:00Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Complete Audnexus API client library with file-based caching (30-day TTL)
- Cover art download with JPEG magic byte validation and resolution upgrade
- Chapter timestamp conversion producing HH:MM:SS.mmm format
- Eval-safe metadata extraction with proper shell quoting
- All failure paths degrade gracefully (return 1, never crash)
- Verified against live API with ASIN B002V5D1CG

## Task Commits

Each task was committed atomically:

1. **Task 1: Create lib/audnexus.sh with API client, caching, and metadata extraction** - `c9c8267` (feat)
2. **Task 2: Integration smoke test + stat detection fix** - `5a975bc` (fix)

## Files Created/Modified
- `lib/audnexus.sh` - Audnexus API client: fetch_audnexus_book, fetch_audnexus_chapters, download_cover_art, validate_chapter_duration, convert_chapters_to_timestamps, extract_metadata_fields
- `config.env.example` - Added AUDNEXUS_REGION, AUDNEXUS_CACHE_DIR, AUDNEXUS_CACHE_DAYS, CHAPTER_DURATION_TOLERANCE vars

## Decisions Made
- Used `stat --version` detection instead of `uname` -- discovered during smoke testing that GNU coreutils stat is in PATH on macOS via Homebrew, making uname-based detection unreliable
- Genre extraction uses `.genres[0]?.asin` as primary since Audnexus genres are numeric category IDs (e.g., `18574597011`), not human-readable names
- Series position extraction uses regex capture (`^[0-9]+(\\.[0-9]+)?`) to handle positions like "14" or "1.5"

## Deviations from Plan

### Auto-fixed Issues

**1. Stat detection method changed from uname to stat --version**
- **Found during:** Task 2 (smoke test)
- **Issue:** `uname` returns `Darwin` but `stat` is GNU coreutils (via Homebrew), causing `stat -f %m` to fail with "cannot read file system information for '%m'"
- **Fix:** Detect stat flavor at source time via `stat --version` success/failure instead of uname
- **Files modified:** lib/audnexus.sh
- **Verification:** Cache hit test passes on both first and subsequent calls
- **Committed in:** `5a975bc`

---

**Total deviations:** 1 auto-fixed (portability)
**Impact on plan:** Essential fix for macOS environments with Homebrew coreutils. No scope creep.

## Issues Encountered
- Audnexus config vars were already added to config.env.example by plan 02-01, so no additional config changes needed

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- lib/audnexus.sh is ready for plan 02-03 to consume via `source lib/audnexus.sh`
- All 6 functions verified working against live API
- Cache layer reduces API calls on repeated runs

---
*Phase: 02-metadata-enrichment*
*Completed: 2026-02-20*
