---
phase: 04-automation-triggers
plan: 02
subsystem: concurrency
tags: [flock, disk-space, singleton, bash]

requires:
  - phase: 01-core-conversion
    provides: lib/core.sh logging functions (log_info, log_error, die)
  - phase: 04-automation-triggers plan 01
    provides: automation hooks and queue processor that need singleton behavior
provides:
  - flock-based global singleton lock (acquire_global_lock)
  - disk space pre-flight check (check_disk_space, 3x source size)
  - LOCK_DIR configuration variable
affects: [04-automation-triggers plan 03, deployment, operations]

tech-stack:
  added: [flock]
  patterns: [FD-based advisory locking, pre-flight validation]

key-files:
  created: [lib/concurrency.sh]
  modified: [bin/audiobook-convert, stages/01-validate.sh, config.env.example]

key-decisions:
  - "FD 200 for flock -- avoids collision with standard FDs and pipeline redirections"
  - "Exit 0 on lock contention -- cron/hook callers treat this as success, not failure"
  - "3x source size multiplier -- covers concat + convert + headroom"
  - "Disk check in validate stage, not main -- fail fast before any processing"

patterns-established:
  - "Singleton via flock: acquire_global_lock() at top of main(), auto-release on exit"
  - "Pre-flight checks in validate stage before expensive operations"

duration: 1min
completed: 2026-02-21
---

# Phase 4 Plan 2: Concurrency Control Summary

**flock-based singleton lock on FD 200 and disk space pre-flight check (3x source size) preventing pipeline collisions and out-of-space failures**

## Performance

- **Duration:** 1 min
- **Started:** 2026-02-21T02:55:56Z
- **Completed:** 2026-02-21T02:57:12Z
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments
- Global singleton lock via flock ensures only one pipeline instance runs at a time
- Second instance exits cleanly (exit 0) -- not an error, safe for cron/hook callers
- Disk space check requires 3x source size before processing begins
- Structured logging shows exact KB values (source/required/available) for debugging

## Task Commits

Each task was committed atomically:

1. **Task 1: Create lib/concurrency.sh** - `79b099c` (feat)
2. **Task 2: Integrate global lock into bin/audiobook-convert** - `e75bf96` (feat)
3. **Task 3: Add disk space check to validate stage** - `3f9f806` (feat)

## Files Created/Modified
- `lib/concurrency.sh` - acquire_global_lock() and check_disk_space() functions
- `bin/audiobook-convert` - Sources concurrency.sh, acquires lock before pipeline starts, exports LOCK_DIR
- `stages/01-validate.sh` - Sources concurrency.sh, calls check_disk_space after directory validation
- `config.env.example` - Added LOCK_DIR configuration variable

## Implementation Details

**Lock mechanism:**
- File: `$LOCK_DIR/pipeline.lock` (default: `/var/lib/audiobook-pipeline/locks/pipeline.lock`)
- FD 200, non-blocking (`flock -n 200`)
- Auto-released on script exit, ERR trap, or signal termination
- Contention results in exit 0 with informational log message

**Disk space check:**
- Source size via `du -sk`, available via `df -k`
- Required = 3x source (concat intermediate + convert output + headroom)
- Returns 1 on insufficient space, die() in validate stage
- Log line includes exact KB values for all three measurements

**Exit codes:**
- Lock contention: exit 0 (clean, not error)
- Insufficient disk: exit 1 (permanent failure, no retry)

## Decisions Made
- FD 200 for flock -- avoids collision with standard FDs and pipeline redirections
- Exit 0 on lock contention -- cron/hook callers treat this as success, not failure
- 3x source size multiplier -- covers concat + convert + headroom
- Disk check in validate stage (not main) -- fail fast before any processing

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required. LOCK_DIR auto-creates on first run.

## Next Phase Readiness
- Concurrency control ready for cron scanner and Readarr hook triggers
- Plan 04-03 (systemd integration) can reference LOCK_DIR for service configuration

## Self-Check: PASSED

- [x] lib/concurrency.sh exists
- [x] 04-02-SUMMARY.md exists
- [x] Commit 79b099c found (task 1)
- [x] Commit e75bf96 found (task 2)
- [x] Commit 3f9f806 found (task 3)

---
*Phase: 04-automation-triggers*
*Completed: 2026-02-21*
