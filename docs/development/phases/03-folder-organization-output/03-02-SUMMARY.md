---
phase: 03-folder-organization-output
plan: 02
subsystem: pipeline
tags: [bash, m4b, ffprobe, archive, validation]

# Dependency graph
requires:
  - phase: 03-folder-organization-output/01
    provides: "Organize stage (07) with output_path in manifest"
  - phase: 02-metadata-enrichment
    provides: "lib/ffmpeg.sh get_duration/get_codec for validation"
provides:
  - "6-point M4B integrity validation library (lib/archive.sh)"
  - "Archive stage (08) as safety gate before deleting originals"
  - "Renumbered cleanup stage (09) with stripped output logic"
  - "Full 8-stage pipeline: validate concat convert asin metadata organize archive cleanup"
affects: [04-automation-triggers]

# Tech tracking
tech-stack:
  added: [bc]
  patterns: ["validation gate before destructive operations", "cross-filesystem cp+verify+rm"]

key-files:
  created:
    - "lib/archive.sh"
    - "stages/08-archive.sh"
  modified:
    - "stages/09-cleanup.sh (renamed from 04-cleanup.sh)"
    - "bin/audiobook-convert"
    - "lib/manifest.sh"
    - "config.env.example"

key-decisions:
  - "Stat detection uses stat --version (not uname) per project convention from 02-02"
  - "Cleanup stage stripped to work-dir-only cleanup -- organize handles output"

patterns-established:
  - "Validation gate: validate output before destructive archival of inputs"
  - "Stage renumbering: stages numbered by execution order, not creation order"

# Metrics
duration: 2min
completed: 2026-02-21
---

# Phase 3 Plan 2: Archive Stage with Verification Gate Summary

**6-point M4B validation gate with original MP3 archival and pipeline renumbered to 8 stages**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-21T02:12:36Z
- **Completed:** 2026-02-21T02:14:55Z
- **Tasks:** 3
- **Files modified:** 6

## Accomplishments

- M4B integrity validation with 6 checks: exists, ffprobe parseable, duration > 0, AAC codec, mov/mp4 container, file size within 10%
- Archive stage as safety gate -- originals only moved after validation passes
- Pipeline wired with full stage order: validate -> concat -> convert -> asin -> metadata -> organize -> archive -> cleanup
- Cleanup stage renumbered to 09, stripped of M4B output logic (organize handles that)

## Task Commits

Each task was committed atomically:

1. **Task 1: Create lib/archive.sh with validation library** - `dcf2948` (feat)
2. **Task 2: Create stages/08-archive.sh** - `172dafb` (feat)
3. **Task 3: Wire archive stage into pipeline** - `36e14e0` (feat)

## Files Created/Modified

- `lib/archive.sh` - M4B validation (validate_m4b_integrity) and archive operations (archive_originals)
- `stages/08-archive.sh` - Archive stage orchestration with validation gate and idempotency
- `stages/09-cleanup.sh` - Renumbered cleanup, work-dir-only (was 04-cleanup.sh with M4B output)
- `bin/audiobook-convert` - Sources lib/archive.sh, ARCHIVE_DIR config, updated STAGE_MAP/STAGE_ORDER
- `lib/manifest.sh` - Archive stage in manifest_create and get_next_stage
- `config.env.example` - ARCHIVE_DIR and ARCHIVE_RETENTION_DAYS documentation

## Decisions Made

- Used `stat --version` for filesystem detection instead of `uname` (project convention from 02-02 -- GNU coreutils on macOS breaks uname detection)
- Stripped all M4B output logic from cleanup stage since organize stage (07) now handles output placement

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed stat detection method in lib/archive.sh**
- **Found during:** Task 1 (lib/archive.sh verification)
- **Issue:** Previous executor used `uname` for macOS/Linux stat detection, but project convention (from 02-02) uses `stat --version` because GNU coreutils in PATH on macOS breaks uname-based detection
- **Fix:** Changed to `stat --version` success/failure test, matching lib/audnexus.sh pattern
- **Files modified:** lib/archive.sh
- **Committed in:** dcf2948 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Consistency fix with existing codebase convention. No scope creep.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Phase 3 complete: organize (07) + archive (08) + cleanup (09) provide full output pipeline
- Ready for Phase 4: automation triggers (Readarr hook, manual CLI already exists)
- ARCHIVE_DIR defaults to /var/lib/audiobook-pipeline/archive (local disk, configurable)

## Self-Check: PASSED

All 6 files verified present. All 3 task commits verified (dcf2948, 172dafb, 36e14e0).

---
*Phase: 03-folder-organization-output*
*Completed: 2026-02-21*
