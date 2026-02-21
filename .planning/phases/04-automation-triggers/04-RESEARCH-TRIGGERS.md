# Phase 4: Automation & Triggers - Research (Trigger Mechanisms)

**Researched:** 2026-02-20
**Domain:** Readarr custom scripts, webhook triggers, queue-based processing, cron scanners
**Confidence:** MEDIUM-HIGH

## Summary

Readarr (and its Bookshelf fork) execute custom scripts on post-import events, passing book paths and metadata via environment variables. The critical challenge is that Readarr has a 30-second timeout for custom scripts, requiring a fast-exit pattern: the hook writes a trigger file to a queue directory and exits immediately (target: <5 seconds), then a separate processor picks up trigger files asynchronously.

For redundancy, a cron scanner (15-minute interval) detects books in `_incoming` that lack trigger files, using mtime stability checks (file unchanged for 2+ minutes) to avoid processing mid-download books. This catch-all handles manual imports and any hook failures.

**Primary recommendation:** Write trigger files with JSON payloads containing book path + metadata. Use a dedicated queue directory (`/var/lib/audiobook-pipeline/queue/`) with timestamp-based filenames. Separate the hook script (fast exit) from the processor (picks up queue files on its own schedule).

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Bash 4+ | 4.x-5.x | Hook script, cron scanner | Already on LXC 210, handles env vars natively |
| jq | 1.6+ | JSON trigger file parsing | De facto standard for JSON in bash scripts |
| stat | GNU coreutils | mtime checks for file stability | Universal, precise timestamps |
| find | GNU findutils | Cron scanner directory traversal | Standard file discovery tool |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| flock | util-linux | Lock files for concurrent processing | If multiple processors consume queue |
| systemd timer | systemd 240+ | Alternative to cron for scanning | Better logging, overlapping run prevention |
| inotifywait | inotify-tools | Real-time file monitoring | Optional: faster than cron polling |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Cron | systemd timer | Timer: better logging + dependency mgmt, but more complex setup |
| Trigger files | Direct processing in hook | Direct: simpler, but violates 30s timeout on long pipelines |
| JSON | Simple flag files | JSON: carries metadata, reduces need for re-parsing paths |

**Installation:**
```bash
# On LXC 210 (Ubuntu 24.04)
sudo apt-get install jq inotify-tools  # jq likely already installed
# flock, stat, find are in coreutils (pre-installed)
```

## Architecture Patterns

### Recommended Project Structure
```
/var/lib/audiobook-pipeline/
├── queue/                    # Trigger files from Readarr hook
│   ├── 20260220-153042-abc123.json
│   └── 20260220-154501-def456.json
├── processing/               # Locked files during pipeline execution
│   └── 20260220-153042-abc123.json.lock
├── completed/                # Archived trigger files (success)
└── failed/                   # Archived trigger files (errors)
```

### Pattern 1: Fast-Exit Hook Script
**What:** Readarr custom script that exits within 5 seconds, writes trigger file, no blocking I/O
**When to use:** All Readarr post-import events (OnReleaseImport)
**Example:**
```bash
#!/usr/bin/env bash
# Readarr post-import hook -- fast exit pattern
set -euo pipefail

# Exit immediately on test events
[[ "${readarr_eventtype:-}" == "Test" ]] && exit 0

# Validate critical env var
if [[ -z "${readarr_addedbookpaths:-}" ]]; then
  echo "ERROR: readarr_addedbookpaths not set" >&2
  exit 1
fi

# Generate unique trigger file name
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RANDOM_ID=$(head -c 8 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 6)
TRIGGER_FILE="/var/lib/audiobook-pipeline/queue/${TIMESTAMP}-${RANDOM_ID}.json"

# Write trigger file with JSON payload
cat > "$TRIGGER_FILE" <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "event_type": "${readarr_eventtype}",
  "book_paths": "${readarr_addedbookpaths}",
  "book_id": "${readarr_book_id:-}",
  "book_title": "${readarr_book_title:-}",
  "author_name": "${readarr_author_name:-}",
  "asin": "${readarr_bookfile_edition_asin:-}"
}
EOF

# Exit fast -- processor will pick this up later
exit 0
```
**Notes:**
- Pipe-separated paths in `readarr_addedbookpaths` handled by processor, not hook
- No filesystem operations besides single file write
- No external API calls or slow I/O

### Pattern 2: Cron Scanner with mtime Stability Check
**What:** Periodic scanner that detects books without trigger files, ensures file stability before processing
**When to use:** Every 15 minutes as a catch-all for manual imports or hook failures
**Example:**
```bash
#!/usr/bin/env bash
# Cron scanner for _incoming directory
set -euo pipefail

INCOMING_DIR="/mnt/media/AudioBooks/_incoming"
QUEUE_DIR="/var/lib/audiobook-pipeline/queue"
MANIFEST_DIR="/var/lib/audiobook-pipeline/manifests"
STABILITY_THRESHOLD=120  # seconds (2 minutes)

# Find book directories in _incoming
find "$INCOMING_DIR" -mindepth 1 -maxdepth 1 -type d | while read -r book_dir; do
  book_hash=$(echo -n "$book_dir" | sha256sum | cut -d' ' -f1 | head -c 12)

  # Skip if already processed (manifest exists)
  [[ -f "$MANIFEST_DIR/${book_hash}.json" ]] && continue

  # Skip if trigger file already queued
  grep -q "\"book_paths\":.*${book_dir}" "$QUEUE_DIR"/*.json 2>/dev/null && continue

  # File stability check: ensure newest file is at least STABILITY_THRESHOLD old
  newest_file=$(find "$book_dir" -type f -printf '%T@ %p\n' | sort -rn | head -n1)
  [[ -z "$newest_file" ]] && continue

  newest_mtime=$(echo "$newest_file" | cut -d' ' -f1 | cut -d'.' -f1)
  current_time=$(date +%s)
  age=$((current_time - newest_mtime))

  if [[ $age -lt $STABILITY_THRESHOLD ]]; then
    echo "Skipping $book_dir -- newest file is only ${age}s old (threshold: ${STABILITY_THRESHOLD}s)"
    continue
  fi

  # Stable -- create trigger file
  timestamp=$(date +%Y%m%d-%H%M%S)
  trigger_file="$QUEUE_DIR/${timestamp}-${book_hash}.json"

  cat > "$trigger_file" <<EOF
{
  "timestamp": "$(date -Iseconds)",
  "event_type": "CronScan",
  "book_paths": "${book_dir}",
  "source": "cron-scanner"
}
EOF

  echo "Created trigger file: $trigger_file"
done
```

### Pattern 3: Queue Processor (Separate from Hook)
**What:** Independent process that consumes trigger files and invokes pipeline
**When to use:** Run via systemd timer (every 5 minutes) or triggered by inotifywait
**Example:**
```bash
#!/usr/bin/env bash
# Queue processor -- processes trigger files
set -euo pipefail

QUEUE_DIR="/var/lib/audiobook-pipeline/queue"
PROCESSING_DIR="/var/lib/audiobook-pipeline/processing"
COMPLETED_DIR="/var/lib/audiobook-pipeline/completed"
FAILED_DIR="/var/lib/audiobook-pipeline/failed"
PIPELINE_BIN="/opt/audiobook-pipeline/bin/audiobook-convert"

for trigger_file in "$QUEUE_DIR"/*.json; do
  [[ ! -f "$trigger_file" ]] && continue  # No files in queue

  # Atomic move to processing (prevents concurrent processing)
  lock_file="$PROCESSING_DIR/$(basename "$trigger_file").lock"
  if ! mv "$trigger_file" "$lock_file" 2>/dev/null; then
    continue  # Another processor grabbed it
  fi

  # Parse trigger file
  book_paths=$(jq -r '.book_paths' "$lock_file")
  event_type=$(jq -r '.event_type' "$lock_file")

  # Handle pipe-separated paths (Readarr collections)
  IFS='|' read -ra PATHS <<< "$book_paths"

  for book_path in "${PATHS[@]}"; do
    [[ ! -d "$book_path" ]] && continue

    echo "Processing: $book_path (event: $event_type)"

    # Invoke pipeline
    if "$PIPELINE_BIN" "$book_path"; then
      mv "$lock_file" "$COMPLETED_DIR/$(basename "$lock_file" .lock)"
      echo "Success: $book_path"
    else
      mv "$lock_file" "$FAILED_DIR/$(basename "$lock_file" .lock)"
      echo "Failed: $book_path" >&2
    fi
  done
done
```

### Anti-Patterns to Avoid
- **Long-running hook scripts:** Readarr has 30s timeout. Scripts that call the pipeline directly will block and timeout.
- **No stability checks in cron scanner:** Processing books mid-download causes corrupt M4B files.
- **Skipping Test events:** Hook scripts must exit 0 on `readarr_eventtype=Test` or Readarr marks the connection as failed.
- **Assuming single book path:** `readarr_addedbookpaths` can contain multiple paths separated by `|` for collections.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| File locking | Custom PID files | `flock` (util-linux) | Handles stale locks, race conditions, signals |
| JSON parsing | awk/sed extraction | `jq` | Handles escaping, nested structures, edge cases |
| File monitoring | Polling loops | `inotifywait` (inotify-tools) | Kernel-level events, no CPU waste |
| Timestamp math | Date string parsing | `date +%s` and `stat -c %Y` | POSIX epoch arithmetic, no TZ bugs |

**Key insight:** File-based coordination (trigger files, lock files, queue directories) is simpler and more debuggable than database queues or message brokers for this use case. Bash + coreutils handles it natively.

## Common Pitfalls

### Pitfall 1: Unreliable `readarr_addedbookpaths` on Manual Imports
**What goes wrong:** User reports indicate `readarr_addedbookpaths` is sometimes empty on manual imports vs automatic downloads.
**Why it happens:** Readarr bug or edge case in manual import flow -- variable not set consistently.
**How to avoid:** Cron scanner acts as fallback. Hook script logs when variable is empty but doesn't fail the entire pipeline.
**Warning signs:** Trigger files created with empty `book_paths` field, cron scanner catches these books later.

### Pitfall 2: Race Condition in Queue Processing
**What goes wrong:** Two processors pick up the same trigger file, process the same book twice.
**Why it happens:** Checking file existence then opening it is non-atomic.
**How to avoid:** Use `mv` (atomic rename) to claim the file. If `mv` fails, another processor already has it.
**Warning signs:** Duplicate manifests, duplicate output files, concurrent ffmpeg processes on same source.

### Pitfall 3: mtime Stability False Positives
**What goes wrong:** File appears stable (mtime hasn't changed for 2 minutes) but is actually paused mid-download.
**Why it happens:** Network congestion, download client pause, BitTorrent seeding delay.
**How to avoid:** Increase stability threshold (5 minutes instead of 2), or check Readarr API for active downloads before processing.
**Warning signs:** Books in `_incoming` that never get processed, cron scanner repeatedly skips them.

### Pitfall 4: Readarr Hook Script Timeout
**What goes wrong:** Hook script tries to do too much, Readarr kills it at 30 seconds, marks connection as failed.
**Why it happens:** Script calls pipeline directly, does metadata lookups, or writes large log files.
**How to avoid:** Hook script does ONE thing: write trigger file. Exit in <5 seconds. All heavy lifting happens in separate processor.
**Warning signs:** Readarr connection shows "failed" status, logs show timeout errors.

### Pitfall 5: Cron Job Overlap
**What goes wrong:** Cron job starts every 15 minutes, but previous run is still processing, leading to concurrent execution.
**Why it happens:** Cron doesn't check if previous invocation is running.
**How to avoid:** Use `flock` at start of cron script, or switch to systemd timer with `Persistent=false` (won't queue missed runs).
**Warning signs:** Multiple scanner processes in `ps aux`, lock file conflicts, CPU spike every 15 minutes.

## Code Examples

Verified patterns from research:

### Check File Age (mtime-based)
```bash
# Source: https://www.baeldung.com/linux/file-age-and-modification-time
# Calculate file age in seconds
filepath="/path/to/file"
file_mtime=$(date -r "$filepath" +%s)  # or: stat -c %Y "$filepath"
current_time=$(date +%s)
age_seconds=$((current_time - file_mtime))

if [[ $age_seconds -ge 120 ]]; then
  echo "File is stable (${age_seconds}s old)"
else
  echo "File is too new (${age_seconds}s old), skipping"
fi
```

### Wait for File to Stop Changing
```bash
# Source: https://www.commandlinefu.com/commands/view/14234
# Wait until file hasn't changed for 10 seconds
filename="/path/to/file"
while [ $(( $(date +%s) - $(stat -c %Y "$filename") )) -lt 10 ]; do
  sleep 1
done
echo "File is stable"
```

### Handle Pipe-Separated Lists from Readarr
```bash
# Source: Readarr CustomScript.cs (GitHub)
# readarr_addedbookpaths uses | separator for multiple books
book_paths="${readarr_addedbookpaths}"
IFS='|' read -ra PATHS <<< "$book_paths"

for book_path in "${PATHS[@]}"; do
  echo "Processing: $book_path"
done
```

### Atomic File Claim with flock
```bash
# Source: https://hackaday.com/2020/08/18/linux-fu-one-at-a-time-please-critical-sections-in-bash-scripts/
# Prevent concurrent processing of same trigger file
trigger_file="/var/lib/audiobook-pipeline/queue/123.json"
lock_file="/var/lib/audiobook-pipeline/processing/123.json.lock"

# Atomic move to claim file
if mv "$trigger_file" "$lock_file" 2>/dev/null; then
  echo "Claimed file, processing..."
  # ... do work ...
  mv "$lock_file" "/var/lib/audiobook-pipeline/completed/$(basename "$lock_file" .lock)"
else
  echo "Another processor already claimed this file"
fi
```

### Cron with flock to Prevent Overlap
```bash
# Source: https://www.kiloroot.com/bash-two-methods-for-job-control-simple-lock-files-and-flock/
# In crontab:
# */15 * * * * flock -n /var/lock/audiobook-scanner.lock /opt/audiobook-pipeline/bin/cron-scanner.sh

# Script exits immediately if lock is held (previous run still active)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Direct pipeline invocation in hook | Trigger file + async processor | ~2020s (cloud patterns) | Decouples hook from pipeline, prevents timeouts |
| Cron polling only | Cron + inotifywait hybrid | 2015+ (inotify maturity) | Faster response, lower CPU for polling |
| Simple lock files (touch/rm) | flock or atomic mv | 2010+ (concurrency awareness) | Handles stale locks, race conditions |
| Cron for periodic tasks | systemd timers | 2015+ (systemd adoption) | Better logging, dependency mgmt, no overlap |

**Deprecated/outdated:**
- **lockfile(1):** Replaced by `flock` -- lockfile doesn't handle crashes gracefully, creates stale lock files
- **Polling without stability checks:** Just checking file existence isn't enough, must verify mtime stability
- **Database queues for local file processing:** Overkill for single-host pipelines, filesystem-based queues are simpler

## Readarr Custom Script Environment Variables

Based on Readarr source code (`CustomScript.cs`), the following environment variables are available:

### On Release Import (OnReleaseImport)
Event type: `Readarr_EventType` = "Download"

**Book metadata:**
- `Readarr_Book_Id` - Book ID
- `Readarr_Book_Title` - Book title
- `Readarr_Book_GRId` - Monitored edition's Goodreads ID
- `Readarr_Book_ReleaseDate` - Book release date

**Author metadata:**
- `Readarr_Author_Id` - Author ID
- `Readarr_Author_Name` - Author name
- `Readarr_Author_Path` - Author folder path
- `Readarr_Author_GRId` - Author's Goodreads ID

**File paths (CRITICAL):**
- `Readarr_AddedBookPaths` - **Pipe-separated** file paths (if files present)
- `Readarr_DeletedPaths` - Pipe-separated deleted file paths (if old files present)
- `Readarr_DeletedDateAdded` - Pipe-separated date-added values for deleted files

**Download metadata:**
- `Readarr_Download_Client` - Download client name
- `Readarr_Download_Client_Type` - Download client type
- `Readarr_Download_Id` - Download ID

### Other Event Types
See source code for: Grab, Rename, AuthorAdded, AuthorDelete, BookDelete, BookFileDelete, BookRetag, HealthIssue, ApplicationUpdate, Test

**Note:** Variable names are case-sensitive. `readarr_addedbookpaths` (lowercase in practice) maps to `Readarr_AddedBookPaths` in source code.

## Open Questions

1. **Bookshelf fork compatibility**
   - What we know: Bookshelf is a revival of retired Readarr, maintains API compatibility
   - What's unclear: Does Bookshelf fix the `readarr_addedbookpaths` manual import bug?
   - Recommendation: Test both automatic and manual imports, log all env vars to confirm behavior

2. **Readarr script timeout exact value**
   - What we know: Commonly cited as 30 seconds, but not in official docs
   - What's unclear: Is it configurable? Does it vary by Readarr version?
   - Recommendation: Assume 30s, design for <5s exit to be safe

3. **inotifywait vs cron for queue processing**
   - What we know: inotifywait is more responsive, cron is simpler
   - What's unclear: Does inotify reliably trigger on NFS mounts? (queue dir is local, but worth verifying)
   - Recommendation: Start with cron (15min), add inotifywait optimization later if needed

4. **Trigger file retention policy**
   - What we know: Completed/failed trigger files accumulate over time
   - What's unclear: How long to keep them? Archive to logs? Rotate daily/weekly?
   - Recommendation: Keep for 7 days, then delete (or compress to tar.gz if debugging needed)

## Sources

### Primary (HIGH confidence)
- [Readarr CustomScript.cs source code](https://github.com/Readarr/Readarr/blob/develop/src/NzbDrone.Core/Notifications/CustomScript/CustomScript.cs) - Complete environment variable list
- [Calibre-Web-Automated Readarr integration discussion](https://github.com/crocodilestick/Calibre-Web-Automated/discussions/248) - Practical hook script example
- [Hackaday: Critical Sections in Bash Scripts](https://hackaday.com/2020/08/18/linux-fu-one-at-a-time-please-critical-sections-in-bash-scripts/) - flock patterns
- [Baeldung: File Age and Modification Time](https://www.baeldung.com/linux/file-age-and-modification-time) - mtime checks

### Secondary (MEDIUM confidence)
- [Readarr Custom Scripts Wiki (Retired)](https://wiki.servarr.com/readarr/custom-scripts) - General overview (limited content due to retirement)
- [Radarr Custom Scripts Wiki](https://wiki.servarr.com/radarr/custom-scripts) - Similar *arr app patterns
- [Servarr Wiki: Readarr Tips and Tricks](https://wikiold.servarr.com/Readarr_Tips_and_Tricks) - Old wiki content
- [systemd timer vs cron comparison (multiple sources)](https://coady.tech/systemd-timer-vs-cron/) - Periodic task scheduling
- [Trigger File Based Workflows (Coviant Software)](https://www.coviantsoftware.com/tech-tips/trigger-file-based-workflows-that-was-easy/) - Pattern overview

### Tertiary (LOW confidence)
- Community forum posts about `readarr_addedbookpaths` reliability on manual imports (needs testing to verify)
- Generic bash job queue and semaphore tutorials (patterns apply, but not Readarr-specific)

## Metadata

**Confidence breakdown:**
- Readarr environment variables: HIGH - extracted from source code
- Hook script timeout (<30s): MEDIUM - widely cited but not in official docs, needs testing
- mtime stability pattern: HIGH - standard Unix pattern, well-documented
- Cron vs systemd timer tradeoffs: HIGH - comprehensive comparisons from multiple sources
- Trigger file pattern: MEDIUM - common in enterprise workflows, adapted to this use case

**Research date:** 2026-02-20
**Valid until:** 2026-03-20 (30 days - stable domain, Readarr is retired/frozen)
