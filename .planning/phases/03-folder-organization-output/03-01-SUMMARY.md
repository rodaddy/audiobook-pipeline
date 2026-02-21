---
phase: 03-folder-organization-output
plan: 01
subsystem: organize
tags: [plex, nfs, folder-structure, metadata-driven]
dependency_graph:
  requires: [metadata-stage, audnexus-cache]
  provides: [organize-stage, plex-paths, companion-deployment]
  affects: [pipeline-orchestration, manifest-schema]
tech_stack:
  added: [lib/organize.sh, stages/07-organize.sh]
  patterns: [nfs-safe-copy, utf8-byte-truncation, metadata-fallbacks]
key_files:
  created:
    - lib/organize.sh
    - stages/07-organize.sh
  modified:
    - bin/audiobook-convert
    - lib/manifest.sh
    - config.env.example
decisions:
  - "Use cp+chmod instead of install for NFS compatibility (root squash)"
  - "UTF-8 byte truncation via wc -c (not character counting) for filesystem limits"
  - "Space-based sanitization for folder names (not underscore like filenames)"
  - "Primary metadata source: Audnexus JSON cache, fallback to source folder names"
  - "Series position zero-padded to 2 digits with decimal support (01.5)"
  - "NFS availability checked with 5s timeout to detect stale mounts"
metrics:
  duration_seconds: 174
  duration_minutes: 2
  completed_at: "2026-02-21T02:06:07Z"
  tasks_completed: 3
  files_created: 2
  files_modified: 3
  commits: 3
---

# Phase 03 Plan 01: Organize Stage Implementation Summary

**One-liner:** Plex-compatible folder organization with NFS-safe deployment, metadata-driven paths (Author/Series/Title), and companion file support.

## What Was Built

Implemented the organize stage (07-organize.sh) that transforms tagged M4B files into Plex-compatible folder structures on NFS mounts. The stage reads metadata from cached Audnexus JSON, constructs hierarchical paths based on author/series/title, and deploys M4B files with companion files (cover.jpg, desc.txt, reader.txt).

### Core Components

**lib/organize.sh** -- Plex path construction and NFS operations library
- `sanitize_folder_component()`: Space-based invalid char replacement, UTF-8 byte-aware truncation to 255 bytes using `wc -c`
- `build_plex_path()`: Constructs paths from Audnexus JSON cache (primary) with fallbacks to source folder names
  - With series: `/Author/Series Name/NN - Title (Year)/`
  - Without series: `/Author/Title (Year)/`
  - Zero-pads series position to 2 digits, handles decimals (01.5)
- `copy_to_nfs_safe()`: Uses `cp + chmod` wrapper (NOT `install`) to avoid NFS root squash chown failures
- `check_nfs_available()`: 5-second timeout-based stale mount detection
- `deploy_companion_files()`: Deploys cover.jpg, desc.txt, reader.txt alongside M4B

**stages/07-organize.sh** -- Organize stage implementation
- Idempotency: checks manifest status, skips if already completed
- Reads M4B location from `stages.convert.output_file` (same path metadata stage uses)
- Validates NFS mount availability before operations
- Creates directory structure and deploys M4B + companions
- Updates manifest with final output path

**Pipeline Integration**
- bin/audiobook-convert: Added organize to STAGE_MAP (`[organize]="07"`), STAGE_ORDER (after metadata, before cleanup), sourced lib/organize.sh
- lib/manifest.sh: Added organize stage to schema and get_next_stage loop
- config.env.example: Added NFS_OUTPUT_DIR and CREATE_COMPANION_FILES variables

### Metadata Flow

```
Audnexus JSON Cache (WORK_DIR/audnexus_book_*.json)
  ↓
build_plex_path() extracts:
  - .authors[0].name → Author
  - .title → Title
  - .seriesPrimary.name → Series (optional)
  - .seriesPrimary.position → Series position (optional)
  - .releaseDate → Year (optional)
  ↓
Fallback if no JSON:
  - Author: parent directory name → "Unknown Author"
  - Title: source directory basename + hash
  - Series: omitted entirely
  - Year: omitted entirely
  ↓
Sanitize each component (space-based, 255-byte truncation)
  ↓
Construct path:
  /Author/Series/NN - Title (Year)/Title.m4b
```

## Deviations from Plan

None -- plan executed exactly as written.

All tasks completed as specified:
1. lib/organize.sh created with all 5 required functions
2. stages/07-organize.sh created and integrated into pipeline orchestration
3. config.env.example updated with NFS_OUTPUT_DIR and CREATE_COMPANION_FILES variables

No architectural changes, bugs, or blocking issues encountered during execution.

## Technical Implementation Details

### NFS Compatibility

The organize stage uses `cp + chmod` instead of `install` for file deployment. The `install` command attempts to set ownership via `chown`, which fails on NFS mounts with root squash enabled (common security configuration). Using `cp` followed by `chmod` avoids ownership changes while maintaining correct file permissions.

### UTF-8 Byte Truncation

Filesystem path components are limited to 255 bytes (not characters). The sanitize_folder_component() function uses `wc -c` to count bytes (not `${#var}` which counts characters). For multi-byte UTF-8 characters, this ensures truncation doesn't exceed filesystem limits. The function uses `iconv -c` to safely truncate without splitting mid-character.

### Metadata Fallback Strategy

Primary metadata source: Audnexus JSON cache written by stage 05-asin.sh and used by stage 06-metadata.sh. This ensures consistency across stages.

Fallback triggers:
- METADATA_SKIP=true (user disabled metadata enrichment)
- No ASIN found in source folder
- Audnexus API unreachable

Fallback behavior:
- Author: parent directory name, then "Unknown Author"
- Title: source directory basename with hash suffix to avoid collisions
- Series folder: omitted entirely (no guessing)
- Year: omitted from folder name

This prevents the organize stage from failing when metadata enrichment is skipped while still creating valid Plex paths.

### Series Position Formatting

Series positions are zero-padded to 2 digits for proper lexicographic sorting in Plex:
- `1` → `01`
- `9` → `09`
- `10` → `10`
- `1.5` → `01.5` (decimal preserved)

This ensures books appear in correct order when sorted alphabetically by folder name.

## Verification Results

All verification checks passed:

1. **Library functions loadable:** All 5 functions (sanitize_folder_component, build_plex_path, copy_to_nfs_safe, check_nfs_available, deploy_companion_files) sourced and declared successfully
2. **Stage integration:** organize stage present in STAGE_MAP, STAGE_ORDER, manifest schema, and get_next_stage loop
3. **NFS safety:** No `install -o` usage found in organize library or stage
4. **UTF-8 byte counting:** Uses `wc -c` for byte-accurate truncation
5. **Fallback logic:** "Unknown Author" fallback implemented for missing metadata
6. **Syntax validation:** All bash files pass `bash -n` syntax checks

## Next Steps

Plan 03-02 will:
1. Add archive stage (08-archive.sh) for ZIP creation with folder structure preservation
2. Rename cleanup from 04 to 09 (after organize and archive)
3. Modify cleanup to skip M4B output (organize handles it), only clean work directory if CLEANUP_WORK_DIR=true

After Phase 3 completes, the pipeline will have:
- Plex-compatible folder organization (this plan)
- ZIP archives with preserved folder structure (plan 03-02)
- Work directory cleanup at the end of the pipeline (plan 03-02)

## Self-Check: PASSED

Verified all created files exist:
- lib/organize.sh: EXISTS
- stages/07-organize.sh: EXISTS

Verified all commits exist:
- 2044467 (Task 1: lib/organize.sh): EXISTS
- 7f9cfd2 (Task 2: organize stage integration): EXISTS
- 6c00fe1 (Task 3: config variables): EXISTS

Verified modified files contain expected changes:
- bin/audiobook-convert: sources lib/organize.sh, has organize in STAGE_MAP and STAGE_ORDER
- lib/manifest.sh: has organize in stages schema and get_next_stage loop
- config.env.example: has NFS_OUTPUT_DIR and CREATE_COMPANION_FILES variables

All deliverables verified present and correct.
