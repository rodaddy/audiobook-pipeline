# Requirements

## Functional Requirements

### Conversion

#### FR-CONV-01: Single-File MP3 to M4B Conversion
- **Priority:** P0
- **Description:** Convert a single MP3 file into an M4B audiobook container with AAC-LC audio encoding.
- **Acceptance Criteria:**
  - [ ] Single MP3 input produces a valid M4B file playable in Plex/Prologue
  - [ ] Output uses AAC-LC codec (not HE-AAC) with `-movflags +faststart`
  - [ ] ffprobe validates the output container as M4B with correct duration

#### FR-CONV-02: Multi-File MP3 Merge
- **Priority:** P0
- **Description:** Concatenate multiple MP3 files (one per chapter) into a single M4B file, preserving chapter boundaries derived from individual file durations.
- **Acceptance Criteria:**
  - [ ] Multiple MP3 inputs merged into one M4B with no gaps or artifacts at boundaries
  - [ ] Chapter markers generated from individual MP3 durations (one chapter per source file)
  - [ ] Chapter names derived from source filenames (stripped of track numbers and extensions)
  - [ ] ffprobe `-show_chapters` confirms all chapters present in output

#### FR-CONV-03: Bitrate-Aware Encoding
- **Priority:** P0
- **Description:** Encode AAC at 64kbps mono for speech content. For sources at or below 64kbps, avoid lossy-to-lossy re-encoding where possible.
- **Acceptance Criteria:**
  - [ ] Default output is 64kbps mono AAC (matches Audible's encoding)
  - [ ] Source bitrate detected via ffprobe before encoding
  - [ ] Sources at or below 64kbps logged with a warning -- transcoded at source rate, not upscaled
  - [ ] Mono channel output (audiobooks are speech, stereo wastes space)

### Chapter Detection

#### FR-CHAP-01: File-Boundary Chapters
- **Priority:** P0
- **Description:** For multi-MP3 audiobooks, generate chapter markers from individual file boundaries (duration of each MP3 becomes a chapter).
- **Acceptance Criteria:**
  - [ ] Each source MP3 maps to exactly one chapter in the output M4B
  - [ ] Chapter timestamps are cumulative (chapter N starts at sum of durations 1..N-1)
  - [ ] Chapters written in a format tone CLI can import

#### FR-CHAP-02: Audnexus Chapter Import
- **Priority:** P1
- **Description:** When an ASIN is available, fetch chapter data from Audnexus API and use those timestamps instead of file boundaries. Audnexus provides millisecond-precision chapter data matching the Audible release.
- **Acceptance Criteria:**
  - [ ] Audnexus chapter data fetched via `api.audnex.us/chapters/{asin}`
  - [ ] Chapter timestamps from Audnexus applied to the M4B via tone CLI
  - [ ] Falls back to file-boundary chapters (FR-CHAP-01) if Audnexus has no chapter data
  - [ ] Audnexus chapters only used when total duration matches within a tolerance (e.g., 30 seconds)

#### FR-CHAP-03: Silence Detection Fallback
- **Priority:** P2
- **Description:** For single-file audiobooks without an ASIN, detect chapter boundaries via silence analysis as a last resort.
- **Acceptance Criteria:**
  - [ ] Silence detection runs only when no ASIN is available AND input is a single file
  - [ ] Uses ffmpeg silencedetect filter with configurable threshold and duration
  - [ ] Generated chapters have sequential names ("Chapter 1", "Chapter 2", etc.)
  - [ ] Can be disabled via config flag (some books have intentional long silences)

### Metadata Enrichment

#### FR-META-01: Audnexus Metadata Lookup
- **Priority:** P0
- **Description:** Fetch book metadata from the Audnexus API using the book's ASIN. Audnexus mirrors Audible's catalog and provides title, author, narrator, series, description, genres, and release date.
- **Acceptance Criteria:**
  - [ ] Book data fetched from `api.audnex.us/books/{asin}`
  - [ ] Extracted fields: title, author(s), narrator(s), series name, series position, year, description, genre(s)
  - [ ] API responses cached locally to avoid redundant calls
  - [ ] Graceful degradation when API is unavailable (skip tagging, don't fail pipeline)

#### FR-META-02: M4B Tagging via tone CLI
- **Priority:** P0
- **Description:** Write metadata fields and cover art into the M4B file using the tone CLI tool.
- **Acceptance Criteria:**
  - [ ] Title, author, narrator, series, series position, year, description, genre written to M4B tags
  - [ ] Cover art embedded in the M4B file
  - [ ] tone operates on local disk (not NFS mount) to avoid I/O issues
  - [ ] Tags verified post-write via `tone dump`

#### FR-META-03: Cover Art Download
- **Priority:** P1
- **Description:** Download high-resolution cover art from Audnexus/Audible and embed it in the M4B. Also save as companion file alongside the audiobook.
- **Acceptance Criteria:**
  - [ ] Cover image URL extracted from Audnexus response
  - [ ] Image downloaded and saved as `cover.jpg` alongside the M4B
  - [ ] Cover embedded in M4B via tone CLI
  - [ ] Fallback: if no cover available, pipeline continues without cover art

#### FR-META-04: Companion File Generation
- **Priority:** P1
- **Description:** Generate companion text files alongside the M4B for Plex metadata agents and human reference.
- **Acceptance Criteria:**
  - [ ] `desc.txt` created with book description
  - [ ] `reader.txt` created with narrator name(s)
  - [ ] Files placed in same directory as the M4B output

### ASIN Discovery

#### FR-ASIN-01: ASIN from Readarr/Bookshelf
- **Priority:** P1
- **Description:** Extract the Audible ASIN from Readarr/Bookshelf's API or database when processing a book that Readarr imported.
- **Acceptance Criteria:**
  - [ ] Query Readarr/Bookshelf API for the book record associated with the import event
  - [ ] Extract ASIN from the book's foreign ID or linked edition
  - [ ] Works with both Readarr webhook trigger and cron-based processing

#### FR-ASIN-02: ASIN from Folder/Filename
- **Priority:** P1
- **Description:** Parse ASIN from the audiobook's folder name or a naming convention (e.g., `{Title} [{ASIN}]` format used by some download sources).
- **Acceptance Criteria:**
  - [ ] Regex extracts ASIN pattern (B0xxxxxxxxx, 10 alphanumeric chars starting with B0) from folder name
  - [ ] Extracted ASIN validated against Audnexus API before use
  - [ ] Logged when ASIN found via folder name

#### FR-ASIN-03: Manual ASIN Override
- **Priority:** P0
- **Description:** Allow a `.asin` file placed alongside the audiobook to specify the ASIN manually. This is the reliable fallback for books that can't be auto-matched.
- **Acceptance Criteria:**
  - [ ] Pipeline checks for `.asin` file in the audiobook directory before any auto-detection
  - [ ] File contains a single ASIN string (trimmed of whitespace)
  - [ ] Manual ASIN takes highest priority over all other discovery methods

### Organization

#### FR-ORG-01: Plex Folder Structure
- **Priority:** P0
- **Description:** Organize output M4B files into the Plex-compatible directory structure: `Author/Series/## Title (Year)/Title.m4b`.
- **Acceptance Criteria:**
  - [ ] Books with series placed in `Author/Series Name/NN Title (Year)/Title.m4b` where NN is zero-padded series position
  - [ ] Books without series placed in `Author/Title (Year)/Title.m4b`
  - [ ] Author, title, and series names sanitized for filesystem safety (no `/`, `:`, etc.)
  - [ ] Final output written to `/mnt/media/AudioBooks/` NFS mount

#### FR-ORG-02: Filename Sanitization
- **Priority:** P0
- **Description:** Sanitize all metadata-derived names for filesystem use, removing or replacing characters that are invalid on ext4/NFS.
- **Acceptance Criteria:**
  - [ ] Characters `/ : * ? " < > |` replaced or removed
  - [ ] Leading/trailing whitespace and dots stripped
  - [ ] Double spaces collapsed to single
  - [ ] Maximum component length enforced (255 bytes)

### Triggers

#### FR-TRIG-01: Readarr Webhook Trigger
- **Priority:** P1
- **Description:** Accept Readarr/Bookshelf post-import custom script webhook. The hook script drops a trigger file and exits within Readarr's 30-second timeout.
- **Acceptance Criteria:**
  - [ ] Shell script compatible with Readarr custom script interface
  - [ ] Writes trigger file with book path and metadata to a queue directory
  - [ ] Exits within 5 seconds (well under Readarr's 30s timeout)
  - [ ] Pipeline picks up trigger file on next processing cycle

#### FR-TRIG-02: Cron Scan Trigger
- **Priority:** P1
- **Description:** Periodic cron job scans the staging directory (`_incoming`) for unprocessed audiobooks.
- **Acceptance Criteria:**
  - [ ] Cron runs every 15 minutes (configurable)
  - [ ] Detects new audiobook directories not yet in the processing manifest
  - [ ] Skips directories currently being written (file stability check via mtime)
  - [ ] Catch-all for books that arrive without a Readarr hook

#### FR-TRIG-03: Manual CLI Trigger
- **Priority:** P0
- **Description:** CLI interface for manually processing a specific audiobook directory.
- **Acceptance Criteria:**
  - [ ] `./pipeline.sh /path/to/audiobook/` processes a single book
  - [ ] Supports `--dry-run` flag to preview actions without modifying files
  - [ ] Supports `--force` flag to reprocess a previously completed book
  - [ ] Exit code 0 on success, non-zero on failure with meaningful error message

### Archive

#### FR-ARCH-01: Original File Archival
- **Priority:** P1
- **Description:** After successful conversion and organization, move original MP3 files to an archive directory rather than deleting them.
- **Acceptance Criteria:**
  - [ ] Originals moved to configurable archive path (default: `_incoming/_archive/`)
  - [ ] Archive preserves original directory structure
  - [ ] Move only happens after output M4B is verified (ffprobe validates, file size > 0)
  - [ ] Archive can be periodically cleaned by user (not pipeline's job)

### Concurrency

#### FR-CONC-01: flock-Based Concurrency Control
- **Priority:** P1
- **Description:** Use flock to ensure only one pipeline instance runs at a time, preventing race conditions between cron and webhook triggers.
- **Acceptance Criteria:**
  - [ ] Pipeline acquires exclusive flock on a lock file before processing
  - [ ] Second instance exits cleanly with a log message (not an error)
  - [ ] Lock released on pipeline exit (including abnormal termination via trap)
  - [ ] Optional per-book locking for future parallel processing (MAX_JOBS=2)

## Non-Functional Requirements

### NFR-01: Idempotent Processing
- **Priority:** P0
- **Description:** Processing a book that has already been successfully converted must be a no-op (skip with log), unless `--force` is used.
- **Acceptance Criteria:**
  - [ ] Per-book manifest tracks processing state across stages
  - [ ] Re-running pipeline on a completed book logs "already processed" and skips
  - [ ] `--force` flag overrides idempotency check

### NFR-02: Structured Logging
- **Priority:** P1
- **Description:** All pipeline operations produce structured key=value log output for debugging and audit.
- **Acceptance Criteria:**
  - [ ] Every log line includes timestamp, stage, book identifier, and message
  - [ ] Processing decisions logged (why a bitrate was chosen, which ASIN source was used)
  - [ ] Errors include enough context to reproduce (input path, ffmpeg exit code, API response)

### NFR-03: Error Recovery
- **Priority:** P1
- **Description:** Failed books are retried up to 3 times, then moved to a `failed/` directory with error context preserved.
- **Acceptance Criteria:**
  - [ ] Retry count tracked in per-book manifest
  - [ ] After 3 failures, book directory moved to `failed/` with last error logged
  - [ ] Failed books don't block processing of other books in the queue

### NFR-04: Disk Space Pre-flight
- **Priority:** P1
- **Description:** Check available disk space before starting conversion. Large audiobooks (40+ hours) need 3x input size in temp space.
- **Acceptance Criteria:**
  - [ ] Available space on work directory checked before each book
  - [ ] Required space estimated as 3x input directory size
  - [ ] Processing skipped with clear error if insufficient space

### NFR-05: File Permissions
- **Priority:** P0
- **Description:** All file operations run as readarr user (UID 2018, GID 2000 media group). Output files must be readable by Plex.
- **Acceptance Criteria:**
  - [ ] Pipeline runs as readarr user, not root
  - [ ] Output files owned by readarr:media with 644/755 permissions
  - [ ] NFS operations respect root squash (no root-owned files on NFS)

### NFR-06: Local Work Directory
- **Priority:** P0
- **Description:** All intermediate processing happens on local block storage, not NFS. Only final output is written to NFS.
- **Acceptance Criteria:**
  - [ ] Work directory defaults to `/var/lib/audiobook-pipeline/work/`
  - [ ] No ffmpeg or tone operations target NFS paths directly
  - [ ] Final M4B copied to NFS via `install` command with correct ownership

## Constraints

- **Runtime:** LXC 210 (media-automation), Ubuntu 24.04, 8GB RAM, no GPU
- **Stack:** Bash scripts + ffmpeg + tone CLI. No heavy frameworks.
- **File permissions:** readarr user (UID 2018, GID 2000 media group)
- **NFS:** Media files on TrueNAS NFS mount at /mnt/media/. Root squash active.
- **Shebang:** `#!/usr/bin/env bash` (LAW 7)
- **Folder structure:** Must match `Author/Series/## Title (Year)/` exactly. Plex + Prologue depend on this.
- **No Docker:** Direct install on LXC 210
- **Concurrency:** MAX_JOBS=2 on 8GB RAM to prevent OOM
- **Encoding:** AAC-LC only (not HE-AAC), 64kbps mono default, `-movflags +faststart` mandatory

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FR-CONV-01 | Phase 1 | Pending |
| FR-CONV-02 | Phase 1 | Pending |
| FR-CONV-03 | Phase 1 | Pending |
| FR-CHAP-01 | Phase 1 | Pending |
| FR-TRIG-03 | Phase 1 | Pending |
| NFR-01 | Phase 1 | Pending |
| NFR-05 | Phase 1 | Pending |
| NFR-06 | Phase 1 | Pending |
| FR-META-01 | Phase 2 | Pending |
| FR-META-02 | Phase 2 | Pending |
| FR-META-03 | Phase 2 | Pending |
| FR-META-04 | Phase 2 | Pending |
| FR-ASIN-01 | Phase 2 | Pending |
| FR-ASIN-02 | Phase 2 | Pending |
| FR-ASIN-03 | Phase 2 | Pending |
| FR-CHAP-02 | Phase 2 | Pending |
| FR-ORG-01 | Phase 3 | Pending |
| FR-ORG-02 | Phase 3 | Pending |
| FR-ARCH-01 | Phase 3 | Pending |
| FR-TRIG-01 | Phase 4 | Pending |
| FR-TRIG-02 | Phase 4 | Pending |
| FR-CONC-01 | Phase 4 | Pending |
| NFR-02 | Phase 4 | Pending |
| NFR-03 | Phase 4 | Pending |
| NFR-04 | Phase 4 | Pending |
| FR-CHAP-03 | Deferred (v2) | -- |

---
*Last updated: 2026-02-20*
