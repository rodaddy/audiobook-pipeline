# Architecture Patterns

**Domain:** Audiobook processing pipeline (MP3 to M4B conversion, metadata enrichment, Plex organization)
**Researched:** 2026-02-20
**Overall confidence:** HIGH

## Recommended Architecture

### Design: Stage-Based Sequential Pipeline with Job Queue

The pipeline processes one book at a time through sequential stages, with a job queue managing concurrency across multiple books. Each stage is idempotent and resumable -- if a stage fails, the book stays in its current stage directory and can be retried without data loss.

This is the right pattern because:
- Audio conversion is CPU-bound and I/O-heavy -- parallelizing stages within a single book adds complexity with no benefit
- Multiple books CAN process in parallel (controlled by `MAX_JOBS`)
- Sequential stages with explicit directory transitions make failure recovery trivial
- Existing scripts already follow this pattern loosely -- formalize it, don't reinvent it

### Pipeline Flow Diagram

```
                                TRIGGER LAYER
                    +-------------------------------+
                    |  Readarr Hook  |  Cron Scan   |  Manual CLI  |
                    +-------+-------+------+--------+------+-------+
                            |              |               |
                            v              v               v
                    +----------------------------------------------+
                    |              QUEUE MANAGER                    |
                    |  flock-based single instance                  |
                    |  Scans staging/ for unprocessed books         |
                    |  Creates job manifest per book                |
                    +----------------------+-----------------------+
                                           |
                              +------------+------------+
                              |   PER-BOOK PIPELINE     |
                              |   (up to MAX_JOBS=2)    |
                              +------------+------------+
                                           |
                    +----------------------v-----------------------+
                    |                                               |
          +---------v---------+                                    |
          |  STAGE 1: INGEST  |  Validate input, detect format,   |
          |                   |  create job manifest, copy to      |
          |                   |  work dir                          |
          +---------+---------+                                    |
                    |                                              |
          +---------v---------+                                    |
          |  STAGE 2: MERGE   |  Multi-file: concat MP3s          |
          |  (conditional)    |  Single-file: skip/passthrough     |
          +---------+---------+                                    |
                    |                                              |
          +---------v---------+                                    |
          | STAGE 3: CHAPTER  |  Multi-MP3: use filenames          |
          |                   |  Single-MP3: silence detection     |
          +---------+---------+                                    |
                    |                                              |
          +---------v---------+                                    |
          | STAGE 4: CONVERT  |  MP3 -> AAC in M4B container       |
          |                   |  Match source bitrate (128k floor) |
          |                   |  Embed chapter metadata            |
          +---------+---------+                                    |
                    |                                              |
          +---------v---------+                                    |
          | STAGE 5: TAG      |  Audible metadata lookup           |
          |                   |  Cover art, series, narrator       |
          |                   |  tone CLI writes to M4B            |
          +---------+---------+                                    |
                    |                                              |
          +---------v---------+                                    |
          | STAGE 6: ORGANIZE |  Move to Plex structure            |
          |                   |  Author/Series/## Title (Year)/    |
          +---------+---------+                                    |
                    |                                              |
          +---------v---------+                                    |
          | STAGE 7: ARCHIVE  |  Move originals to archive/        |
          |                   |  Clean up work dir                 |
          |                   |  Log completion                    |
          +---------+---------+                                    |
                    |                                              |
                    +----------------------------------------------+
```

### Why Sequential Per-Book, Parallel Across Books

Parallel stages within one book (e.g., converting while tagging another chapter) adds massive complexity for minimal gain. Audio encoding is the bottleneck -- ffmpeg already uses multiple threads internally. The right parallelism is at the book level: process 2 books simultaneously on 8GB RAM (each ffmpeg instance uses ~1-2GB peak for long audiobooks).

**MAX_JOBS=2** is the recommendation for LXC 210 with 8GB RAM. The existing script used MAX_JOBS=4, which would OOM on large audiobooks.

## Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `pipeline.sh` | Main orchestrator. Queue management, job dispatch, flock | All stages, logging |
| `stage-ingest.sh` | Validate input, create manifest, copy to work dir | pipeline.sh |
| `stage-merge.sh` | Concatenate multi-file MP3s into single stream | pipeline.sh, ffmpeg |
| `stage-chapter.sh` | Generate chapter metadata (silence detection or filename-based) | pipeline.sh, ffprobe, chapterize_ab.py |
| `stage-convert.sh` | Transcode MP3 to AAC/M4B with chapters | pipeline.sh, ffmpeg |
| `stage-tag.sh` | Audible metadata lookup + write tags | pipeline.sh, tone CLI |
| `stage-organize.sh` | Move M4B to Plex folder structure | pipeline.sh |
| `stage-archive.sh` | Archive originals, cleanup work dir | pipeline.sh |
| `lib/logging.sh` | Structured logging functions | All components |
| `lib/manifest.sh` | Job manifest read/write (stage tracking) | All stages |
| `config.env` | All configurable paths, limits, defaults | All components |

### Data Flow

```
Input:                   Work Directory:                     Output:
staging/                 work/<job-id>/                      library/
  Author/                  manifest.json                      Author/
    BookTitle/               stage: "convert"                   Series Name/
      chapter01.mp3          source_type: "multi-mp3"            01 Book Title (2020)/
      chapter02.mp3          bitrate: 128000                       Book Title.m4b
      ...                    author: "Author Name"
                             title: "Book Title"
                           input/
                             chapter01.mp3          archive/
                             chapter02.mp3            Author/
                           merged.mp3                   BookTitle/
                           chapters.txt                   chapter01.mp3
                           output.m4b                     chapter02.mp3
                           cover.jpg
```

## Trigger Mechanisms

### Recommendation: Cron + Readarr Hook (Dual Trigger)

**Do NOT use inotifywait.** The media files are on NFS (`/mnt/media/`), and inotify does not work on NFS filesystems. This is a hard kernel limitation, not a configuration issue.

| Trigger | Use Case | Implementation |
|---------|----------|----------------|
| **Readarr post-import hook** | Primary -- immediate processing when Readarr imports a book | Custom script in Readarr Settings > Connect. Receives `readarr_author_name`, `readarr_book_title`, `readarr_addedbookpaths` via env vars. Drops a trigger file into staging/ and exits fast (Readarr has a 30s script timeout). |
| **Cron scan** (every 15 min) | Catch-all -- picks up books that arrived without a hook (manual drops, hook failures) | `*/15 * * * * /opt/audiobook-pipeline/pipeline.sh --scan` |
| **Manual CLI** | Ad-hoc processing, retries, testing | `pipeline.sh /path/to/book` or `pipeline.sh --retry <job-id>` |

### Readarr Hook Architecture

The Readarr hook must be a thin shim, not the full pipeline. Readarr kills scripts that run longer than ~30 seconds.

```bash
#!/usr/bin/env bash
# readarr-hook.sh -- thin shim, drops trigger file and exits
# Readarr calls this with env vars set

STAGING="/mnt/media/AudioBooks/_incoming"
TRIGGER_DIR="/opt/audiobook-pipeline/triggers"

mkdir -p "$TRIGGER_DIR"

# Write trigger file with metadata from Readarr env vars
cat > "$TRIGGER_DIR/$(date +%s%N).trigger" <<EOF
author=${readarr_author_name:-unknown}
title=${readarr_book_title:-unknown}
path=${readarr_addedbookpaths:-}
event=${readarr_eventtype:-unknown}
EOF

# Optionally kick off pipeline (non-blocking)
nohup /opt/audiobook-pipeline/pipeline.sh --scan >/dev/null 2>&1 &
```

**Important:** Readarr is officially retired. The replacement is [Bookshelf](https://github.com/community-scripts/ProxmoxVE/discussions/8159). The hook interface is the same (env vars via custom scripts), so this architecture works for both. Plan for eventual Readarr -> Bookshelf migration.

## File Locking and Concurrency

### Use `flock` for All Concurrency Control

`flock` is the correct tool -- it's kernel-level, race-condition-free, and auto-releases on process exit (even crashes). Do NOT use PID files or touch-based lock files -- they have race conditions between check-and-create.

```bash
#!/usr/bin/env bash
# pipeline.sh entry point

LOCK_FILE="/var/run/audiobook-pipeline.lock"

# Acquire exclusive lock -- queue if another instance is running
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "Another pipeline instance is running. Exiting."
    exit 0
fi

# Lock acquired -- this instance owns the queue
# ... scan for jobs, dispatch workers ...
```

### Concurrency Model

```
flock (pipeline.sh)          -- Only one queue manager runs at a time
  |
  +-- worker 1 (book A)     -- Each worker gets its own work/<job-id>/ directory
  +-- worker 2 (book B)     -- No shared state between workers
  |
  +-- (waits for slot)       -- MAX_JOBS=2 controlled via xargs -P or wait
```

Per-book locking is NOT needed because each book gets its own work directory. No two workers ever touch the same files.

## Error Recovery

### Stage-Based Idempotency via Manifest

Each book's processing state is tracked in a manifest file (`work/<job-id>/manifest.json`). When a stage completes, the manifest is updated. On failure, the book stays at its current stage.

```bash
# manifest.json (plain text key=value for bash simplicity -- not actual JSON despite the name)
job_id=1708456832123456789
source_path=/mnt/media/AudioBooks/_incoming/Author/BookTitle
source_type=multi-mp3
stage=convert
stage_started=2026-02-20T14:30:00
bitrate=128000
author=Author Name
title=Book Title
series=Series Name
series_position=3
year=2020
asin=B08EXAMPLE
error=
```

### Recovery Patterns

| Failure Point | What Happens | Recovery |
|---------------|-------------|----------|
| Ingest fails | No work dir created | Book stays in staging/, next scan retries |
| Merge fails | `work/<id>/` exists, stage=merge | `pipeline.sh --retry <id>` re-runs from merge |
| Convert fails (mid-encode) | Partial M4B in work dir | Stage is idempotent -- re-run overwrites partial output |
| Tag fails (Audible unreachable) | M4B exists but untagged | Retry tag stage only. M4B is valid without tags. |
| Organize fails (disk full) | Tagged M4B in work dir | Fix disk, retry. M4B is safe in work dir. |
| Archive fails | M4B in library, originals still in staging | Idempotent -- re-run just moves originals |
| Pipeline crash mid-book | flock auto-releases, manifest shows last stage | Next cron run detects in-progress jobs, resumes |

### Critical Rule: Never Delete Source Until Archive Stage

Source files in staging/ are NEVER deleted or moved until the final archive stage confirms the M4B is valid and in the correct library location. This prevents data loss on any failure.

### Failed Books Directory

Books that fail 3+ times get moved to `failed/` with their manifest and error logs. These require manual investigation. Don't retry forever -- that's a recipe for log spam and wasted CPU.

```
failed/
  2026-02-20_Author_BookTitle/
    manifest.json          # Shows which stage failed
    error.log              # stderr from the failed stage
    input/                 # Original files preserved
```

## Directory Layout

### Recommended Structure on LXC 210

```
/opt/audiobook-pipeline/           # Application code
  pipeline.sh                      # Main entry point
  readarr-hook.sh                  # Readarr custom script shim
  stages/
    01-ingest.sh
    02-merge.sh
    03-chapter.sh
    04-convert.sh
    05-tag.sh
    06-organize.sh
    07-archive.sh
  lib/
    logging.sh                     # Structured logging functions
    manifest.sh                    # Manifest read/write helpers
    audible.sh                     # Audible metadata lookup
    ffmpeg-helpers.sh              # Bitrate detection, encoding helpers
  config.env                       # All configurable paths/settings
  triggers/                        # Trigger files from Readarr hooks

/var/lib/audiobook-pipeline/       # Runtime data (persistent across restarts)
  work/                            # In-progress jobs
    <job-id>/                      # Per-book working directory
      manifest.json
      input/
      output.m4b
  failed/                          # Books that failed 3+ times
  logs/                            # Log files
    pipeline.log                   # Main structured log
    pipeline.log.1                 # Rotated logs
  state/                           # Persistent state
    processed.log                  # History of all processed books (append-only)

/mnt/media/AudioBooks/
  _incoming/                       # Staging -- Readarr drops books here
    Author/
      BookTitle/
        chapter01.mp3
  _archive/                        # Processed originals (keep for safety)
    Author/
      BookTitle/
        chapter01.mp3
  Author Name/                     # Plex library (output)
    Series Name/
      01 Book Title (2020)/
        Book Title.m4b
```

### Why This Layout

- **`/opt/`** for application code -- standard Linux convention, survives package updates
- **`/var/lib/`** for runtime state -- standard for service data, proper permissions
- **`_incoming/` and `_archive/`** prefixed with underscore -- sorts to top of directory listing, visually distinct from library content, Plex ignores directories with no media
- **`work/` on local disk** (not NFS) -- faster I/O for transcoding, avoids NFS lock issues. The final `mv` to NFS is atomic at the directory level.

### Temp File Strategy

All intermediate files live in `work/<job-id>/`. Never use `/tmp` -- it's typically tmpfs (RAM-backed) and a 500MB audiobook will eat RAM. The work directory is on local disk (`/var/lib/`) which has actual storage.

The `trap` pattern in the existing `convert_to_m4b.sh` is good but should NOT auto-delete on failure -- that destroys recovery state. Only clean up work dirs on successful completion.

```bash
# BAD -- destroys evidence on failure
trap 'rm -rf "$WORK_DIR"' EXIT

# GOOD -- only clean up on success
cleanup_on_success() {
    rm -rf "$WORK_DIR"
}
# Called explicitly at end of successful pipeline, not in trap
```

## Logging and Monitoring

### Structured Logging with Minimal Dependencies

Use a simple logging library that outputs structured key=value lines (not JSON -- parsing JSON in bash requires jq, and structured k=v is native to tools like `grep`, `awk`, and syslog).

```bash
# lib/logging.sh

LOG_FILE="/var/lib/audiobook-pipeline/logs/pipeline.log"

log() {
    local level="$1"
    shift
    local msg="$1"
    shift
    # Remaining args are key=value pairs
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    printf '%s level=%s msg="%s"' "$ts" "$level" "$msg"
    for kv in "$@"; do
        printf ' %s' "$kv"
    done
    printf '\n'
} | tee -a "$LOG_FILE" >&2

# Usage:
# log INFO "Starting conversion" job_id="$JOB_ID" stage="convert" author="$AUTHOR"
# log ERROR "ffmpeg failed" job_id="$JOB_ID" exit_code="$?" stage="convert"
```

Output format:
```
2026-02-20T14:30:00Z level=INFO msg="Starting conversion" job_id=1708456832 stage=convert author="Brandon Sanderson"
2026-02-20T14:35:22Z level=ERROR msg="ffmpeg failed" job_id=1708456832 exit_code=1 stage=convert
```

### Log Rotation

Use `logrotate` -- it's already on Ubuntu 24.04:

```
# /etc/logrotate.d/audiobook-pipeline
/var/lib/audiobook-pipeline/logs/pipeline.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

### Success/Failure Tracking

Maintain an append-only `processed.log` for history:

```
# processed.log
2026-02-20T14:35:22Z SUCCESS author="Brandon Sanderson" title="Mistborn" series="Mistborn" series_pos=1 duration_sec=342 source_files=24 output="/mnt/media/AudioBooks/Brandon Sanderson/Mistborn/01 Mistborn (2006)/Mistborn.m4b"
2026-02-20T15:10:45Z FAILURE author="Joe Abercrombie" title="The Blade Itself" stage=convert error="ffmpeg exit code 137 (OOM)"
```

This gives you a greppable audit trail: `grep FAILURE processed.log | wc -l` for failure rate, `grep SUCCESS processed.log | tail -20` for recent completions.

## Analysis of Similar Projects' Architecture

### m4b-tool (sandreas) -- [GitHub](https://github.com/sandreas/m4b-tool)

**Architecture:** PHP CLI wrapping ffmpeg and mp4v2. Plugin-based command structure (merge, split, chapters as separate commands). Monolithic per-operation -- no pipeline concept.

**What to steal:** Chapter detection via silence analysis, metadata format for FFMETADATA1 chapter files.

**What to avoid:** Monolithic design. m4b-tool tries to do everything in one invocation -- merge+chapter+tag. When it fails, you restart everything. The staged approach is better.

### m4b-merge (djdembeck) -- [GitHub](https://github.com/djdembeck/m4b-merge)

**Architecture:** Python CLI. Linear pipeline: input validation -> Audible ASIN lookup (interactive prompt) -> merge/convert via ffmpeg -> tag via mutagen -> organize to output dir. Docker-first deployment.

**What to steal:** Audible metadata lookup by ASIN. The output path pattern `Author/Book/Book.m4b`. Bitrate matching logic.

**What to avoid:** Interactive ASIN prompt -- kills automation. The pipeline should auto-search Audible by author+title, with ASIN as an optional override in the manifest.

### audtag (jeffjose) -- [GitHub](https://github.com/jeffjose/audtag)

**Architecture:** Python CLI with task system. Parallel file processing. YAML-based post-tagging task configuration. Searches Audible by author+title automatically.

**What to steal:** The task system pattern (YAML config for post-processing actions). Auto-search Audible by title+author without requiring ASIN. Parallel processing model.

**What to avoid:** Python dependency for what should be a simple tag operation. tone CLI can do this natively.

### tone (sandreas) -- [GitHub](https://github.com/sandreas/tone)

**Architecture:** C# single binary. Subcommands: dump (read tags), tag (write tags). Supports JavaScript-based custom taggers for metadata lookup. No Audible scraping built-in, but scriptable.

**What to steal:** Use tone as the tagging engine -- it's a single binary with no dependencies, writes directly to M4B, handles chapters and cover art. The custom JS tagger feature could be used for Audible lookup.

**Key integration point:** `tone tag --meta-title "Book" --meta-artist "Author" --meta-cover-file cover.jpg --meta-chapters-file chapters.txt input.m4b`

### Existing Scripts (this project)

**Architecture:** Two-script pipeline: `process_audiobooks.sh` (orchestrator) -> `convert_to_m4b.sh` (converter). Uses `xargs -P` for parallelism. Copies files to temp dir before processing. No metadata tagging (the gap that killed the workflow).

**What to keep:**
- `xargs -P $MAX_JOBS` for parallel book processing -- simple and effective
- Temp directory per conversion run
- Detection of single vs multi-file audiobooks

**What to fix:**
- Hardcoded 64k bitrate in convert_to_m4b.sh (should match source with 128k floor)
- No metadata tagging at all
- No error recovery -- `set -euo pipefail` + `trap rm` means failures destroy work
- `#!/bin/bash` shebang (must be `#!/usr/bin/env bash`)
- No logging -- just echo to stdout
- No concurrency protection -- two cron runs could process the same book

## Patterns to Follow

### Pattern 1: Stage Script Contract

Every stage script follows the same interface:

```bash
#!/usr/bin/env bash
# stages/04-convert.sh
# Contract: receives JOB_DIR as $1, returns 0 on success, non-zero on failure
# Idempotent: safe to re-run (overwrites partial output)

set -euo pipefail
source "$(dirname "$0")/../lib/logging.sh"
source "$(dirname "$0")/../lib/manifest.sh"

JOB_DIR="$1"
manifest_load "$JOB_DIR/manifest.json"

# Stage-specific work
log INFO "Converting to M4B" job_id="$JOB_ID" bitrate="$BITRATE"

ffmpeg -y -f concat -safe 0 \
    -i "$JOB_DIR/files.txt" \
    -i "$JOB_DIR/chapters.txt" \
    -map_metadata 1 -map 0:a \
    -c:a aac -b:a "${BITRATE}k" \
    "$JOB_DIR/output.m4b"

manifest_set stage "tag"  # Advance to next stage
log INFO "Conversion complete" job_id="$JOB_ID"
```

### Pattern 2: Bitrate Detection with Floor

```bash
detect_bitrate() {
    local file="$1"
    local raw_bitrate
    raw_bitrate=$(ffprobe -v error -select_streams a:0 \
        -show_entries stream=bit_rate \
        -of default=noprint_wrappers=1:nokey=1 "$file")

    # Convert to kbps
    local kbps=$((raw_bitrate / 1000))

    # Apply floor
    if [ "$kbps" -lt 128 ]; then
        kbps=128
    fi

    # Cap at 256k for AAC (higher is wasteful for spoken word)
    if [ "$kbps" -gt 256 ]; then
        kbps=256
    fi

    echo "$kbps"
}
```

### Pattern 3: Plex Path Construction

```bash
build_plex_path() {
    local author="$1"
    local title="$2"
    local year="$3"
    local series="${4:-}"
    local series_pos="${5:-}"

    local base="/mnt/media/AudioBooks"

    if [ -n "$series" ] && [ -n "$series_pos" ]; then
        # Author/Series Name/01 Book Title (2020)/Book Title.m4b
        local padded_pos
        padded_pos=$(printf "%02d" "$series_pos")
        echo "$base/$author/$series/$padded_pos $title ($year)/$title.m4b"
    else
        # Author/Book Title (2020)/Book Title.m4b
        echo "$base/$author/$title ($year)/$title.m4b"
    fi
}
```

## Anti-Patterns to Avoid

### Anti-Pattern 1: Processing on NFS

**What:** Running ffmpeg encoding directly on NFS-mounted files.
**Why bad:** NFS adds latency to every I/O operation. ffmpeg does thousands of small reads/writes during encoding. A 10-hour audiobook conversion takes 2-3x longer on NFS vs local disk.
**Instead:** Copy input to local work dir (`/var/lib/audiobook-pipeline/work/`), process locally, then move final M4B to NFS.

### Anti-Pattern 2: Trap-Based Cleanup on Failure

**What:** `trap 'rm -rf "$WORK_DIR"' EXIT`
**Why bad:** Destroys evidence needed for debugging and recovery. Can't resume from where it failed.
**Instead:** Only clean work dirs on successful completion. Failed dirs stay until manually investigated or retry limit hit.

### Anti-Pattern 3: Interactive Prompts in Automated Pipeline

**What:** Requiring ASIN input or confirmation for Audible metadata.
**Why bad:** Kills automation. The whole point is unattended processing.
**Instead:** Auto-search Audible by author+title. If no match or ambiguous, log a warning and continue without Audible metadata. The M4B is still valid -- metadata is an enhancement, not a requirement. Provide a CLI for manual ASIN override on retry.

### Anti-Pattern 4: Monolithic Single Script

**What:** One giant script that does ingest+merge+chapter+convert+tag+organize.
**Why bad:** Can't resume from mid-point. Can't test stages independently. Can't reuse stages. Hard to debug.
**Instead:** Separate stage scripts with a uniform interface, dispatched by the orchestrator.

## Scalability Considerations

| Concern | Current (LXC 210, 8GB) | If Library Grows 10x | If Processing Demands Increase |
|---------|------------------------|---------------------|-------------------------------|
| Concurrency | MAX_JOBS=2 | Same -- CPU-bound, not I/O-bound | Migrate to dedicated LXC with more RAM |
| Storage | work/ on local disk | Add disk or use /tmp cautiously | work/ cleanup more aggressive |
| Queue depth | In-memory (scan staging/) | Same -- hundreds of books is fine | Add persistent queue file if needed |
| Monitoring | grep processed.log | Same | Add Telegram notification on failure |
| Audible rate limits | Not a concern at 1-5 books/week | May need delays between lookups | Cache Audible results in manifest |

## Sources

- [m4b-tool (GitHub)](https://github.com/sandreas/m4b-tool) -- PRIMARY: architecture reference, chapter detection patterns
- [tone CLI (GitHub)](https://github.com/sandreas/tone) -- PRIMARY: metadata tagging engine
- [m4b-merge (GitHub)](https://github.com/djdembeck/m4b-merge) -- Audible metadata pipeline reference
- [audtag (GitHub)](https://github.com/jeffjose/audtag) -- Parallel processing and auto-search patterns
- [Readarr Custom Scripts (Servarr Wiki)](https://wiki.servarr.com/readarr/custom-scripts) -- Hook env vars and interface
- [flock(2) Linux Manual](https://man7.org/linux/man-pages/man2/flock.2.html) -- File locking semantics
- [inotify(7) Linux Manual](https://www.man7.org/linux/man-pages/man7/inotify.7.html) -- NFS limitation documented
- [BashFAQ/045](https://mywiki.wooledge.org/BashFAQ/045) -- Lock file best practices
- [Structured logging in shell scripts](https://stegard.net/2021/07/how-to-make-a-shell-script-log-json-messages/) -- Logging patterns
- Existing project scripts at `/Volumes/ThunderBolt/AudioBookStuff/AudioBooks_to_fix/` -- Prior art analysis
