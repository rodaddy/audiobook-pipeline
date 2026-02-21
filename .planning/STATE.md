# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-20)

**Core value:** Downloaded audiobooks are automatically converted, tagged, and organized into the correct Plex library structure without manual intervention.
**Current focus:** Phase 4 -- Automation & Triggers (in progress)

## Current Position

Phase: 4 of 4 (Automation & Triggers)
Plan: 3 of 3 executed
Status: Phase 4 Complete -- All phases complete
Last activity: 2026-02-21 -- Completed 04-03-PLAN.md

Progress: [██████████] 100%

## Performance Metrics

**Velocity:**
- Total plans completed: 11
- Average duration: 2.8min
- Total execution time: 0.52 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01 | 3/3 | 11min | 3.7min |
| 02 | 3/3 | 10min | 3.3min |
| 03 | 2/2 | 4min | 2.0min |
| 04 | 3/3 | 6min | 2.0min |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- Roadmap: 64kbps mono AAC-LC (not 128k floor -- research corrected this)
- Roadmap: FR-CHAP-03 (silence detection) deferred to v2 -- unreliable, manual .asin fallback is sufficient
- Roadmap: Phase 1 includes FR-TRIG-03 (manual CLI) so conversion is usable immediately
- 01-01: run() uses "$@" instead of eval to avoid quoting bugs
- 01-01: Manifest writes use temp file + mv for atomicity
- 01-01: Log output to both stderr and file for terminal visibility
- 01-02: Single-file books skip chapter generation entirely
- 01-02: Chapter count mismatch is warning, not fatal (allows graceful degradation)
- 01-03: Force mode deletes manifest entirely (simpler than field-by-field reset)
- 01-03: ERR trap preserves work directory for debugging on failure
- 01-03: check_book_status exit 1 captured with || true to avoid set -e conflict
- 02-01: discover_asin sets global ASIN_SOURCE variable (simpler than composite output parsing)
- 02-01: Audnexus network error returns exit 2 (distinct from validation failure exit 1)
- 02-01: Missing ASIN marks stage completed, not failed (graceful degradation)
- 02-01: Format-valid ASINs accepted with warning when Audnexus unreachable on all attempts
- 02-02: Stat detection uses stat --version (not uname) -- GNU coreutils in PATH on macOS breaks uname detection
- 02-02: All API failures use log_warn for graceful degradation, never crash the pipeline
- 02-02: extract_metadata_fields outputs single-quoted eval-safe shell assignments
- 02-02: Genre field uses numeric Audnexus category ID (not human-readable)
- 02-03: lib/metadata.sh extracts fields directly via jq (not eval) -- safer than audnexus.sh pattern
- 02-03: download_cover_art reused from lib/audnexus.sh, not duplicated
- 02-03: Only tone tag failure is fatal; all other metadata failures degrade gracefully
- [Phase 03]: Use cp+chmod instead of install for NFS compatibility (root squash)
- [Phase 03]: UTF-8 byte truncation via wc -c (not character counting) for filesystem limits
- [Phase 03]: Space-based sanitization for folder names (not underscore like filenames)
- [Phase 03]: Primary metadata source: Audnexus JSON cache, fallback to source folder names
- 04-01: Cron scanner replicates generate_book_hash() from lib/sanitize.sh for accurate manifest dedup
- 04-01: Cross-platform mtime via stat -f%m (macOS) / stat -c%Y (Linux) instead of GNU-only find -printf
- 04-01: Queue processor tracks all_succeeded per trigger file, partial failures move to failed/
- 04-02: FD 200 for flock -- avoids collision with standard FDs and pipeline redirections
- 04-02: Exit 0 on lock contention -- cron/hook callers treat as success, not failure
- 04-02: 3x source size multiplier for disk space check (concat + convert + headroom)
- 04-02: Disk check in validate stage (not main) -- fail fast before processing
- 04-03: Exit codes 2-3 = permanent failure (config/corrupt), all others = transient with retry
- 04-03: Retry count in manifest, automation cycle provides natural retry without in-process delays
- 04-03: Failed books preserve ERROR.txt + pipeline-manifest.json for debugging
- 04-03: Webhook failures silent (|| true) -- never block pipeline
- 04-03: copytruncate for logrotate -- avoids pipeline restart

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 4: Bookshelf hook env vars (`readarr_addedbookpaths`) reportedly unreliable on manual imports -- needs testing on actual install
- Shadow reviewer (Phase 1): Case-sensitivity mismatch in MP3 matching (-name vs -iname) -- should be fixed before production use
- Shadow reviewer (Phase 1): Missing dependency checks for ffmpeg, ffprobe, jq, tone at pipeline start

## Session Continuity

Last session: 2026-02-21
Stopped at: Completed 04-03-PLAN.md -- All phases complete
Resume file: None
Next: All 11 plans across 4 phases executed. Project ready for deployment.
