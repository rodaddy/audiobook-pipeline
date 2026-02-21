---
phase: 02-metadata-enrichment
plan: 01
subsystem: metadata
tags: [asin, audnexus, readarr, bash]

requires:
  - phase: 01-core-conversion-pipeline
    provides: "lib/core.sh logging, lib/manifest.sh state management, bin/audiobook-convert orchestrator"
provides:
  - "ASIN discovery priority chain (manual file, folder regex, Readarr stub)"
  - "Audnexus API validation for discovered ASINs"
  - "Pipeline stage 05 (asin) wired into orchestrator"
  - "Manifest .metadata.asin and .metadata.asin_source fields"
affects: [02-02, 02-03, 04-01]

tech-stack:
  added: [audnexus-api]
  patterns: [priority-chain-discovery, graceful-degradation]

key-files:
  created:
    - lib/asin.sh
    - stages/05-asin.sh
  modified:
    - config.env.example
    - lib/manifest.sh
    - bin/audiobook-convert

key-decisions:
  - "discover_asin sets global ASIN_SOURCE variable rather than parsing composite output"
  - "Audnexus network failure returns exit 2 to distinguish from validation failure (exit 1)"
  - "Missing ASIN marks stage completed (not failed) for graceful degradation"
  - "Format-valid ASINs accepted with warning when Audnexus is unreachable on all attempts"

patterns-established:
  - "Priority chain pattern: try methods in order, validate each, fall through on failure"
  - "Graceful degradation: stage completes successfully even when discovery fails"

duration: 4min
completed: 2026-02-20
---

# Phase 2 Plan 1: ASIN Discovery Summary

**ASIN discovery library with 3-method priority chain, Audnexus API validation, and pipeline stage integration with graceful degradation**

## Performance

- **Duration:** 4 min
- **Started:** 2026-02-20
- **Completed:** 2026-02-20
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- ASIN discovery via priority chain: .asin file > folder name regex > Readarr API (stub)
- Audnexus API validation with network error fallback (accept format-valid ASINs when API unreachable)
- Pipeline stage 05 wired into orchestrator between convert and cleanup
- Manifest schema extended with asin stage and metadata fields

## Task Commits

Each task was committed atomically:

1. **Task 1: Create lib/asin.sh with discovery priority chain and Audnexus validation** - `83075fa` (feat)
2. **Task 2: Create stages/05-asin.sh and wire into pipeline orchestrator** - `e5c70c2` (feat)

## Files Created/Modified
- `lib/asin.sh` - 6 functions: check_manual_asin_file, extract_asin_from_folder, validate_asin_format, validate_asin_against_audnexus, query_readarr_for_asin, discover_asin
- `stages/05-asin.sh` - Stage 05 integration with manifest tracking and graceful degradation
- `config.env.example` - Readarr API config hooks (READARR_API_URL, READARR_API_KEY)
- `lib/manifest.sh` - Added asin stage to manifest schema and get_next_stage iteration
- `bin/audiobook-convert` - Added asin to STAGE_MAP, STAGE_ORDER, and library sourcing

## Decisions Made
- discover_asin sets a global `ASIN_SOURCE` variable instead of outputting composite "source:asin" format -- simpler for stage consumption
- Audnexus validation returns exit code 2 for network errors (distinct from exit 1 for validation failure) so discover_asin can track unreachability separately
- Missing ASIN sets stage to "completed" not "failed" -- downstream stages check .metadata.asin for null and skip enrichment
- When Audnexus is unreachable on all attempts, format-valid ASINs are accepted with a warning log

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- ASIN discovery stage is complete and wired into the pipeline
- Ready for 02-02 (Audnexus API integration) which will consume .metadata.asin from the manifest
- Readarr API stub is in place for Phase 4 implementation

---
*Phase: 02-metadata-enrichment*
*Completed: 2026-02-20*
