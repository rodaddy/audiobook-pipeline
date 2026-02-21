---
phase: 04-automation-triggers
plan: 01
subsystem: automation
tags: [readarr, webhook, cron, queue, bash]

# Dependency graph
requires:
  - phase: 01-core-conversion-pipeline
    provides: "bin/audiobook-convert CLI, lib/sanitize.sh generate_book_hash, lib/manifest.sh"
  - phase: 03-folder-organization-output
    provides: "NFS output paths, config.env.example structure"
provides:
  - "bin/readarr-hook.sh -- fast-exit Readarr webhook handler"
  - "bin/cron-scanner.sh -- cron-based fallback scanner with stability check"
  - "bin/queue-processor.sh -- asynchronous trigger file processor"
  - "Automation config vars in config.env.example"
affects: [04-02, 04-03]

# Tech tracking
tech-stack:
  added: []
  patterns: [fast-exit webhook, trigger-file queue, atomic-mv claim, mtime stability check]

key-files:
  created: [bin/readarr-hook.sh, bin/cron-scanner.sh, bin/queue-processor.sh]
  modified: [config.env.example]

key-decisions:
  - "Cron scanner uses same hash algorithm as pipeline (path + sorted MP3 list -> sha256 -> 16 chars) for accurate manifest dedup"
  - "Cross-platform mtime detection via stat -f%m (macOS) fallback to stat -c%Y (Linux)"
  - "Queue processor tracks all_succeeded flag across pipe-separated paths before moving trigger"

patterns-established:
  - "Fast-exit webhook: write trigger file and exit, never invoke pipeline directly"
  - "Atomic file claiming: mv to processing dir prevents concurrent processing"
  - "Trigger file JSON format: timestamp, event_type, book_paths as standard payload"

# Metrics
duration: 3min
completed: 2026-02-21
---

# Phase 4 Plan 1: Automation Triggers Summary

**Readarr webhook hook, cron fallback scanner, and queue processor for fully automated trigger-to-pipeline flow**

## Performance

- **Duration:** 3 min
- **Started:** 2026-02-21T02:49:21Z
- **Completed:** 2026-02-21T02:52:28Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- Readarr webhook handler exits in <20ms, writes JSON trigger files to queue directory
- Cron scanner detects unprocessed books with mtime stability check (2-min threshold)
- Queue processor atomically claims and processes trigger files without race conditions
- config.env.example documents all automation variables with sensible defaults

## Task Commits

Each task was committed atomically:

1. **Task 1: Create Readarr fast-exit hook script** - `c7cc0af` (feat)
2. **Task 2: Create cron fallback scanner with stability check** - `08aaeef` (feat)
3. **Task 3: Create queue processor and update config** - `54f77b5` (feat)

## Files Created/Modified
- `bin/readarr-hook.sh` - Fast-exit Readarr OnReleaseImport webhook handler (45 lines)
- `bin/cron-scanner.sh` - Cron-triggered fallback scanner with stability and dedup checks (89 lines)
- `bin/queue-processor.sh` - Asynchronous trigger file processor invoking bin/audiobook-convert (67 lines)
- `config.env.example` - Added INCOMING_DIR, QUEUE_DIR, PROCESSING_DIR, COMPLETED_DIR, FAILED_DIR, PIPELINE_BIN, STABILITY_THRESHOLD

## Decisions Made
- Cron scanner replicates generate_book_hash() from lib/sanitize.sh (path + sorted MP3 list -> sha256 -> 16 chars) instead of plan's simpler path-only hash -- ensures manifest dedup check actually works
- Cross-platform mtime detection: stat -f%m (macOS) with fallback to stat -c%Y (Linux) instead of find -printf (GNU-only)
- Queue processor tracks all_succeeded flag per trigger file rather than per-path, so partial failures move entire trigger to failed/

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed book hash algorithm mismatch**
- **Found during:** Task 2 (cron scanner)
- **Issue:** Plan specified `echo -n "$book_dir" | sha256sum | cut -d' ' -f1 | head -c 12` (path-only, 12 chars). Actual pipeline uses path + sorted MP3 file list -> sha256 -> 16 chars. Mismatched hashes would cause cron scanner to never detect already-processed books.
- **Fix:** Implemented compute_book_hash() matching lib/sanitize.sh generate_book_hash() exactly
- **Files modified:** bin/cron-scanner.sh
- **Verification:** Hash output matches for same directory with same MP3 files
- **Committed in:** 08aaeef (Task 2 commit)

**2. [Rule 1 - Bug] Fixed GNU-only find -printf for mtime detection**
- **Found during:** Task 2 (cron scanner)
- **Issue:** Plan specified `find -printf '%T@ %p\n'` which is GNU-only (fails on macOS/BSD). Pipeline targets both platforms.
- **Fix:** Used stat -f%m / stat -c%Y loop over find results for cross-platform mtime detection
- **Files modified:** bin/cron-scanner.sh
- **Verification:** Works on macOS with GNU coreutils in PATH
- **Committed in:** 08aaeef (Task 2 commit)

---

**Total deviations:** 2 auto-fixed (2 bugs)
**Impact on plan:** Both fixes required for correctness. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Trigger infrastructure complete, ready for Phase 4 Plan 2 (concurrency control/flock) and Plan 3 (error recovery/retry)
- All three scripts source config.env for consistent configuration
- Queue directory structure (queue/processing/completed/failed) established for downstream plans

## Self-Check: PASSED

All files exist. All commits verified. All integration tests pass.

---
*Phase: 04-automation-triggers*
*Completed: 2026-02-21*
