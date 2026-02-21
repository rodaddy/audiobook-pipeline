---
phase: 01-core-conversion-pipeline
plan: 03
subsystem: cli
tags: [bash, cli, orchestration, idempotency, cleanup]

requires: [01-01, 01-02]
provides:
  - "bin/audiobook-convert: Full CLI orchestrator with arg parsing, idempotency, stage sequencing"
  - "stages/04-cleanup.sh: Output validation, file placement, work dir cleanup"
affects: [02-PLAN]

tech-stack:
  added: []
  patterns: [stage-orchestration, resume-support, error-trapping, idempotent-pipeline]

key-files:
  modified:
    - bin/audiobook-convert
  created:
    - stages/04-cleanup.sh

key-decisions:
  - "Force mode deletes manifest entirely rather than resetting fields -- simpler and guarantees clean state"
  - "ERR trap marks both stage and overall status as failed, preserves work dir for debugging"
  - "Stage source files are loaded inside run_stage() so STAGE var is set before sourcing"
  - "Cleanup uses install(1) for atomic copy+permission when FILE_OWNER is set, falls back to cp+chmod"
  - "check_book_status returns exit 1 for completed -- captured with || true to avoid set -e trap"

patterns-established:
  - "Stage loop: STAGE_ORDER array + STAGE_MAP associative array for name-to-number mapping"
  - "Resume: get_next_stage() determines where to pick up, stages before that are skipped"
  - "Error handling: CURRENT_STAGE tracked globally, ERR trap uses it to mark correct stage as failed"

duration: 4min
completed: 2026-02-20
---

# Phase 1 Plan 03: CLI Interface and Cleanup Stage

**Full CLI orchestrator replacing the stub main(), plus the final cleanup stage completing the 4-stage pipeline**

## What Was Built

| File | Purpose |
|------|---------|
| `bin/audiobook-convert` | CLI entry point -- arg parsing, book hash, idempotency check, stage loop, error trap |
| `stages/04-cleanup.sh` | Output M4B validation via ffprobe, move to OUTPUT_DIR with permissions, work dir cleanup |

## CLI Features

- `--dry-run` -- previews all actions without writing files (propagated via `run()`)
- `--force` -- reprocesses a completed book by deleting its manifest
- `-v / --verbose` -- sets LOG_LEVEL=DEBUG for detailed output
- `-c / --config FILE` -- override config.env path
- `-h / --help` -- usage synopsis with examples
- Unknown flags produce a clear error with `--help` hint
- Missing SOURCE_PATH dies with usage guidance

## Pipeline Flow

```
SOURCE_PATH -> generate_book_hash -> check_book_status
  |-> "completed" + no --force -> exit 0 (skip)
  |-> "completed" + --force -> delete manifest, start fresh
  |-> "new" -> manifest_create, run all stages
  |-> "failed" -> resume from get_next_stage
```

Stages execute in order: validate -> concat -> convert -> cleanup
Each stage is skipped if `get_next_stage()` reports it's already done.

## Requirements Covered

- FR-TRIG-03: Manual CLI trigger (`./bin/audiobook-convert /path/to/book/`)
- FR-CONV-04: Idempotent re-runs (check_book_status + manifest tracking)
- FR-OUT-01: Output to configurable OUTPUT_DIR with permissions
- NFR-01: Non-zero exit + stderr message on failure

## Commits

- `ec9037a`: feat(01-03): CLI entry point with stage orchestration and idempotency
- `ddc1ec9`: feat(01-03): cleanup stage with output validation and permissions

## Deviations

None. Both tasks matched the plan specification.
