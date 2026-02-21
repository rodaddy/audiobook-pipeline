# Roadmap

## Milestone: v1.0 -- Automated Audiobook Pipeline

### Overview

Convert downloaded MP3 audiobooks into chaptered, tagged M4B files organized for Plex/Prologue -- fully automated. Phase 1 gets manual conversion working end-to-end. Phase 2 adds rich Audible metadata (the original pain point). Phase 3 handles Plex folder placement and archival. Phase 4 makes it hands-off with Readarr hooks and cron triggers. Each phase is independently useful -- you can stop after any phase and have a working tool.

### Phases

- [x] **Phase 1: Core Conversion Pipeline** - MP3 to chaptered M4B via CLI (2026-02-20)
- [x] **Phase 2: Metadata Enrichment** - Audnexus tagging with cover art and chapters (2026-02-20)
- [ ] **Phase 3: Folder Organization & Output** - Plex folder structure and archival
- [ ] **Phase 4: Automation & Triggers** - Readarr hooks, cron, concurrency, error recovery

## Phase Details

### Phase 1: Core Conversion Pipeline
**Goal:** Convert multi-MP3 audiobooks to single M4B files with chapters from file boundaries, runnable via manual CLI
**Depends on:** Nothing (first phase)
**Requirements:** FR-CONV-01, FR-CONV-02, FR-CONV-03, FR-CHAP-01, FR-TRIG-03, NFR-01, NFR-05, NFR-06
**Success Criteria** (what must be TRUE):
  1. Given a folder of sorted MP3 files, `./pipeline.sh /path/to/book/` produces a single M4B with one chapter per source MP3
  2. Single-MP3 input also produces a valid M4B (no chapters needed)
  3. Output is 64kbps mono AAC-LC with `-movflags +faststart`, verified by ffprobe
  4. Re-running the same book skips processing with "already processed" log message (idempotent)
  5. All file operations run as readarr user on local work directory, not NFS
**Plans:** 3 plans

Plans:
- [x] 01-01: Project scaffolding, config, shared libraries (logging, manifest, ffprobe helpers)
- [x] 01-02: MP3 merge, chapter generation, AAC encoding, M4B muxing
- [x] 01-03: CLI interface with --dry-run, --force, idempotency via manifest

### Phase 2: Metadata Enrichment
**Goal:** Tag M4B files with rich metadata from Audnexus API (title, author, narrator, series, cover art, chapters)
**Depends on:** Phase 1
**Requirements:** FR-META-01, FR-META-02, FR-META-03, FR-META-04, FR-ASIN-01, FR-ASIN-02, FR-ASIN-03, FR-CHAP-02
**Success Criteria** (what must be TRUE):
  1. Given a `.asin` file alongside the audiobook, pipeline fetches metadata from Audnexus and writes title/author/narrator/series/year/description/genre tags to the M4B
  2. Cover art downloaded and embedded in M4B, plus saved as `cover.jpg` alongside the output
  3. Companion files (`desc.txt`, `reader.txt`) generated next to the M4B
  4. When Audnexus has chapter data matching the book's duration, those chapters replace file-boundary chapters
  5. Pipeline continues without metadata when ASIN is unavailable or API is down (graceful degradation)
**Plans:** 3 plans

Plans:
- [x] 02-01: ASIN discovery (manual .asin file, folder name regex, Readarr API lookup)
- [x] 02-02: Audnexus API integration (book metadata, chapter data, cover art download, caching)
- [x] 02-03: tone CLI tagging (metadata write, chapter import, cover embed, companion files)

### Phase 3: Folder Organization & Output
**Goal:** Organize tagged M4B files into Plex-compatible folder structure and archive originals
**Depends on:** Phase 2
**Requirements:** FR-ORG-01, FR-ORG-02, FR-ARCH-01
**Success Criteria** (what must be TRUE):
  1. Books with series metadata land in `/mnt/media/AudioBooks/Author/Series Name/NN Title (Year)/Title.m4b`
  2. Books without series land in `/mnt/media/AudioBooks/Author/Title (Year)/Title.m4b`
  3. Filenames and directories are sanitized (no invalid characters, truncated to 255 bytes)
  4. Original MP3 files moved to archive directory after output M4B is verified via ffprobe
**Plans:** 2 plans

Plans:
- [ ] 03-01-PLAN.md -- Plex folder structure generation, filename sanitization, NFS output (lib/organize.sh, stages/07-organize.sh)
- [ ] 03-02: Archive stage with verification gate

### Phase 4: Automation & Triggers
**Goal:** Fully automated pipeline triggered by Readarr post-import hooks and cron scans
**Depends on:** Phase 3
**Requirements:** FR-TRIG-01, FR-TRIG-02, FR-CONC-01, NFR-02, NFR-03, NFR-04
**Success Criteria** (what must be TRUE):
  1. Readarr post-import hook drops a trigger file and exits within 5 seconds
  2. Cron job every 15 minutes picks up new audiobooks in `_incoming` that lack trigger files
  3. Only one pipeline instance runs at a time (flock-based), second instance exits cleanly
  4. Failed books retry up to 3 times, then move to `failed/` directory with error context
  5. Structured key=value logging with timestamp, stage, and book identifier on every line
**Plans:** TBD

Plans:
- [ ] 04-01: Readarr hook shim and cron scanner
- [ ] 04-02: flock concurrency, retry logic, disk space pre-flight
- [ ] 04-03: Structured logging, error recovery, failed book handling

### Deferred to v2

| Requirement | Reason |
|-------------|--------|
| FR-CHAP-03 (Silence Detection) | P2 priority. Research recommends deferring -- complex, unreliable for unknown books. Manual `.asin` file is the reliable fallback. |

## Coverage

| Requirement | Phase | Priority |
|-------------|-------|----------|
| FR-CONV-01 | Phase 1 | P0 |
| FR-CONV-02 | Phase 1 | P0 |
| FR-CONV-03 | Phase 1 | P0 |
| FR-CHAP-01 | Phase 1 | P0 |
| FR-TRIG-03 | Phase 1 | P0 |
| NFR-01 | Phase 1 | P0 |
| NFR-05 | Phase 1 | P0 |
| NFR-06 | Phase 1 | P0 |
| FR-META-01 | Phase 2 | P0 |
| FR-META-02 | Phase 2 | P0 |
| FR-META-03 | Phase 2 | P1 |
| FR-META-04 | Phase 2 | P1 |
| FR-ASIN-01 | Phase 2 | P1 |
| FR-ASIN-02 | Phase 2 | P1 |
| FR-ASIN-03 | Phase 2 | P0 |
| FR-CHAP-02 | Phase 2 | P1 |
| FR-ORG-01 | Phase 3 | P0 |
| FR-ORG-02 | Phase 3 | P0 |
| FR-ARCH-01 | Phase 3 | P1 |
| FR-TRIG-01 | Phase 4 | P1 |
| FR-TRIG-02 | Phase 4 | P1 |
| FR-CONC-01 | Phase 4 | P1 |
| NFR-02 | Phase 4 | P1 |
| NFR-03 | Phase 4 | P1 |
| NFR-04 | Phase 4 | P1 |
| FR-CHAP-03 | Deferred | P2 |

**Mapped:** 25/25 v1 requirements (P0+P1)
**Deferred:** 1 requirement (P2)
**Orphaned:** 0

## Progress

**Execution Order:** Phase 1 -> Phase 2 -> Phase 3 -> Phase 4

| Phase | Plans Complete | Status | Completed |
|-------|---------------|--------|-----------|
| 1. Core Conversion Pipeline | 3/3 | Complete | 2026-02-20 |
| 2. Metadata Enrichment | 3/3 | Complete | 2026-02-20 |
| 3. Folder Organization & Output | 0/2 | Planned | - |
| 4. Automation & Triggers | 0/3 | Not started | - |

---
*Created: 2026-02-20*
