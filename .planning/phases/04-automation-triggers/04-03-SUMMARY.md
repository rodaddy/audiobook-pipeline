---
phase: 04-automation-triggers
plan: 03
subsystem: error-recovery
tags: [retry, error-handling, logrotate, webhook, manifest]

# Dependency graph
requires:
  - phase: 04-01
    provides: Automation triggers (cron scanner, queue processor, Readarr hook)
  - phase: 04-02
    provides: Concurrency control (flock, disk space checks)
  - phase: 01-01
    provides: Core manifest schema and logging
provides:
  - Structured error recovery with retry tracking in manifests
  - Failure categorization (permanent vs transient) by exit code
  - Failed book isolation with ERROR.txt and manifest preservation
  - Optional webhook notifications for permanent failures
  - Log rotation configuration (daily, 14-day retention, gzip)
affects: [deployment, monitoring, operations]

# Tech tracking
tech-stack:
  added: [logrotate]
  patterns: [exit-code-categorization, retry-with-manifest-tracking, failed-book-isolation]

key-files:
  created:
    - lib/error-recovery.sh
    - logrotate.d/audiobook-pipeline
    - logrotate.d/README.md
  modified:
    - lib/manifest.sh
    - bin/audiobook-convert
    - config.env.example

key-decisions:
  - "Exit codes 2-3 = permanent failure (config errors, corrupt input), all others = transient"
  - "Retry count tracked in manifest, automation cycle provides natural retry without in-process delays"
  - "Failed books preserve ERROR.txt (human-readable) and pipeline-manifest.json (machine-readable)"
  - "Webhook failures are silent (|| true) -- never block pipeline operations"
  - "copytruncate for logrotate -- avoids pipeline restart requirement"

patterns-established:
  - "Exit code categorization: 2-3 permanent, 1/4+ transient"
  - "Error context preservation: ERROR.txt + manifest copy in failed/ directory"
  - "Non-blocking notifications: curl with 5s timeout and || true"

# Metrics
duration: 2min
completed: 2026-02-21
---

# Phase 4 Plan 3: Error Recovery Summary

**Structured error recovery with retry tracking, exit code categorization, failed book isolation, and log rotation**

## Performance

- **Duration:** 2 min
- **Started:** 2026-02-21T03:00:54Z
- **Completed:** 2026-02-21T03:02:46Z
- **Tasks:** 4
- **Files modified:** 6

## Accomplishments
- Extended manifest schema with retry_count, max_retries, and last_error fields for tracking failure state across automation cycles
- Created error recovery module with move_to_failed() (preserves ERROR.txt + manifest) and send_failure_notification() (optional webhook)
- Enhanced on_error trap to categorize failures by exit code and enforce retry limits before moving to failed/
- Added logrotate configuration for daily rotation with 14-day retention and gzip compression

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend manifest schema with retry tracking** - `23b94c2` (feat)
2. **Task 2: Create error recovery module** - `01d2595` (feat)
3. **Task 3: Enhance error trap with retry logic** - `e4e72be` (feat)
4. **Task 4: Add logrotate configuration** - `64e698a` (feat)

## Files Created/Modified
- `lib/manifest.sh` - Added retry_count, max_retries, last_error fields + manifest_increment_retry() + manifest_set_error()
- `lib/error-recovery.sh` - New module: move_to_failed() and send_failure_notification()
- `bin/audiobook-convert` - Enhanced on_error() with exit code categorization, retry limits, failure routing
- `config.env.example` - Added MAX_RETRIES, FAILURE_WEBHOOK_URL, FAILURE_EMAIL
- `logrotate.d/audiobook-pipeline` - Daily rotation, 14-day retention, gzip, copytruncate
- `logrotate.d/README.md` - Installation and testing instructions

## Decisions Made
- Exit codes 2-3 categorized as permanent (config errors, corrupt input) -- immediate move to failed/
- All other exit codes treated as transient -- retry up to MAX_RETRIES times
- Retry count increments before checking max_retries (first failure = retry_count 1)
- Webhook notifications are non-blocking with 5s timeout and || true
- copytruncate avoids needing pipeline restart for log rotation

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. Logrotate config must be deployed to `/etc/logrotate.d/` on the target system (see `logrotate.d/README.md`).

## Next Phase Readiness
- Phase 4 is now complete (all 3 plans executed)
- Full automation pipeline: Readarr hook + cron scanner + queue processor + concurrency control + error recovery
- Ready for deployment and production testing

## Self-Check: PASSED

All created files verified present. All 4 task commits verified in git log.

---
*Phase: 04-automation-triggers*
*Completed: 2026-02-21*
