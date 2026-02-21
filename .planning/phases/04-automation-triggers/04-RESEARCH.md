# Phase 4: Automation & Triggers - Consolidated Research

**Researched:** 2026-02-20
**Domains:** Readarr hooks, cron scanning, concurrency control, disk space, structured logging, error recovery
**Confidence:** HIGH

## 1. Key Findings

1. **Readarr 30-second timeout requires fast-exit pattern**: Hook writes trigger file (<5s), exits immediately; separate processor handles pipeline asynchronously
2. **flock with `-n -E 0` enables clean second-instance exits**: Non-blocking lock attempt returns exit code 0 when lock held (not an error)
3. **mtime stability check prevents mid-download processing**: Cron scanner verifies newest file unchanged for 2+ minutes before triggering
4. **Existing structured logging already meets NFR-02**: `lib/core.sh` implements logfmt (key=value) with timestamp/level/stage/book_hash
5. **Retry tracking via manifest extension**: Add `retry_count`, `max_retries`, `last_error` fields to existing JSON schema
6. **Failure categorization via exit codes**: Exit 2-3 = permanent (corrupt input, config error), exit 1/4+ = transient (retry)
7. **3x disk space multiplier accounts for pipeline overhead**: Source files + intermediate temps + final M4B output
8. **Automation cycle provides natural retry loop**: Cron every 15 minutes, no need for in-process exponential backoff
9. **Per-book locking deferred until MAX_JOBS > 1**: Global singleton lock sufficient for Phase 4
10. **Trigger files carry metadata payloads**: JSON format reduces re-parsing, enables audit trail

## 2. Readarr Hook Integration

### Fast-Exit Hook Script Pattern
```bash
#!/usr/bin/env bash
set -euo pipefail

# Exit immediately on test events
[[ "${readarr_eventtype:-}" == "Test" ]] && exit 0

# Validate critical env var
if [[ -z "${readarr_addedbookpaths:-}" ]]; then
  echo "ERROR: readarr_addedbookpaths not set" >&2
  exit 1
fi

# Generate unique trigger file
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RANDOM_ID=$(head -c 8 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 6)
TRIGGER_FILE="/var/lib/audiobook-pipeline/queue/${TIMESTAMP}-${RANDOM_ID}.json"

# Write JSON payload
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

exit 0  # Fast exit target: <5 seconds
```

### Key Environment Variables (OnReleaseImport)
- `Readarr_EventType` = "Download"
- `Readarr_AddedBookPaths` - **Pipe-separated** file paths (critical for collections)
- `Readarr_Book_Title`, `Readarr_Author_Name` - Metadata
- `Readarr_BookFile_Edition_Asin` - ASIN identifier

**Important:** Variable names are case-sensitive; `readarr_addedbookpaths` (lowercase in practice) maps to source code variable `Readarr_AddedBookPaths`.

### Handling Pipe-Separated Paths
```bash
# Readarr uses | separator for multiple books
book_paths="${readarr_addedbookpaths}"
IFS='|' read -ra PATHS <<< "$book_paths"

for book_path in "${PATHS[@]}"; do
  echo "Processing: $book_path"
done
```

## 3. Cron Scanner

### Fallback Scanner with Stability Check
```bash
#!/usr/bin/env bash
set -euo pipefail

INCOMING_DIR="/mnt/media/AudioBooks/_incoming"
QUEUE_DIR="/var/lib/audiobook-pipeline/queue"
MANIFEST_DIR="/var/lib/audiobook-pipeline/manifests"
STABILITY_THRESHOLD=120  # 2 minutes in seconds

find "$INCOMING_DIR" -mindepth 1 -maxdepth 1 -type d | while read -r book_dir; do
  book_hash=$(echo -n "$book_dir" | sha256sum | cut -d' ' -f1 | head -c 12)

  # Skip if already processed
  [[ -f "$MANIFEST_DIR/${book_hash}.json" ]] && continue

  # Skip if trigger file already queued
  grep -q "\"book_paths\":.*${book_dir}" "$QUEUE_DIR"/*.json 2>/dev/null && continue

  # Stability check: newest file must be at least STABILITY_THRESHOLD old
  newest_file=$(find "$book_dir" -type f -printf '%T@ %p\n' | sort -rn | head -n1)
  [[ -z "$newest_file" ]] && continue

  newest_mtime=$(echo "$newest_file" | cut -d' ' -f1 | cut -d'.' -f1)
  current_time=$(date +%s)
  age=$((current_time - newest_mtime))

  if [[ $age -lt $STABILITY_THRESHOLD ]]; then
    continue  # Too new, skip
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
done
```

### Queue Processor (Separate Process)
```bash
#!/usr/bin/env bash
set -euo pipefail

QUEUE_DIR="/var/lib/audiobook-pipeline/queue"
PROCESSING_DIR="/var/lib/audiobook-pipeline/processing"
COMPLETED_DIR="/var/lib/audiobook-pipeline/completed"
FAILED_DIR="/var/lib/audiobook-pipeline/failed"
PIPELINE_BIN="/opt/audiobook-pipeline/bin/audiobook-convert"

for trigger_file in "$QUEUE_DIR"/*.json; do
  [[ ! -f "$trigger_file" ]] && continue

  # Atomic move to claim file (prevents concurrent processing)
  lock_file="$PROCESSING_DIR/$(basename "$trigger_file").lock"
  if ! mv "$trigger_file" "$lock_file" 2>/dev/null; then
    continue  # Another processor claimed it
  fi

  # Parse trigger file
  book_paths=$(jq -r '.book_paths' "$lock_file")
  event_type=$(jq -r '.event_type' "$lock_file")

  # Handle pipe-separated paths
  IFS='|' read -ra PATHS <<< "$book_paths"

  for book_path in "${PATHS[@]}"; do
    [[ ! -d "$book_path" ]] && continue

    if "$PIPELINE_BIN" "$book_path"; then
      mv "$lock_file" "$COMPLETED_DIR/$(basename "$lock_file" .lock)"
    else
      mv "$lock_file" "$FAILED_DIR/$(basename "$lock_file" .lock)"
    fi
  done
done
```

### Cron Configuration with Overlap Prevention
```bash
# In crontab:
# */15 * * * * flock -n /var/lock/audiobook-scanner.lock /opt/audiobook-pipeline/bin/cron-scanner.sh
# */5 * * * * flock -n /var/lock/audiobook-processor.lock /opt/audiobook-pipeline/bin/queue-processor.sh
```

## 4. Concurrency Control

### Global Singleton Lock (Phase 4)
```bash
#!/usr/bin/env bash
# bin/audiobook-convert

LOCK_DIR="/var/lib/audiobook-pipeline/locks"
LOCK_FILE="$LOCK_DIR/pipeline.lock"
mkdir -p "$LOCK_DIR"

# Acquire global lock (FD 200)
exec 200>"$LOCK_FILE"
if ! flock -n -E 0 200; then
  log_info "Another pipeline instance is running. Exiting cleanly."
  exit 0
fi

log_info "Global lock acquired. Starting pipeline."
# Lock automatically released when FD 200 closes (on script exit)
```

**Key details:**
- `-n` (non-blocking): Fail immediately if lock unavailable
- `-E 0`: Set exit code to 0 when lock acquisition fails (not an error)
- FD 200: High number to avoid conflicts with stdin/stdout/stderr
- Automatic cleanup: Kernel releases lock when FD closes (even on crash)

### Per-Book Locking (Future MAX_JOBS > 1)
```bash
# DEFERRED TO FUTURE -- not implemented in Phase 4
acquire_book_lock() {
  local book_hash="$1"
  local lock_file="$LOCK_DIR/books/$book_hash.lock"
  mkdir -p "$(dirname "$lock_file")"

  # Use FD 201 for per-book locks (global uses 200)
  exec 201>"$lock_file"
  if ! flock -n -E 0 201; then
    log_info "Book $book_hash is being processed by another job. Skipping."
    return 1
  fi

  return 0  # Lock acquired
}
```

**Rationale for deferral:** Global lock prevents concurrent instances. Per-book locks only needed when parallel processing (MAX_JOBS > 1) is implemented.

## 5. Disk Space Pre-flight

### Check Available Space (3x Source Size)
```bash
# Check if sufficient disk space is available
# Args: SOURCE_DIR WORK_DIR
check_disk_space() {
  local source_dir="$1"
  local work_dir="$2"

  # Calculate source directory size in KB
  local source_size_kb
  source_size_kb=$(du -sk "$source_dir" | awk '{print $1}')

  # Estimate required space: 3x source size
  local required_kb=$((source_size_kb * 3))

  # Get available space on work directory's filesystem in KB
  local available_kb
  available_kb=$(df -k "$work_dir" | awk 'NR==2 {print $4}')

  log_info "Disk space check: source=${source_size_kb}KB required=${required_kb}KB available=${available_kb}KB"

  if (( available_kb < required_kb )); then
    log_error "Insufficient disk space: need ${required_kb}KB, have ${available_kb}KB"
    return 1
  fi

  return 0
}
```

**Why 3x multiplier:**
- 1x = original source files (remain until archive stage)
- 1x = concatenated temp file or merged audio stream
- 1x = final M4B with metadata/chapters
- Safety margin for multiple concurrent jobs (future MAX_JOBS > 1)

**Integration point:** Call from validate stage before processing starts.

## 6. Structured Logging

### Current Implementation (Already Meets NFR-02)
`lib/core.sh` (lines 27-57) implements logfmt:

```bash
# Output format:
# timestamp=2026-02-20T10:15:30Z level=INFO stage=validate book_hash=abc123 message="Starting validation"
```

**What's working:**
- Structured key=value format (logfmt)
- Timestamp in RFC-3339 format (ISO 8601 with Z suffix)
- Log level filtering (DEBUG/INFO/WARN/ERROR)
- Stage tracking via `$STAGE` global variable
- Book identifier via `$BOOK_HASH` global variable
- Dual output: stderr (terminal visibility) and file append

**No changes needed** -- current implementation already meets requirements.

### Log Rotation Configuration
`/etc/logrotate.d/audiobook-pipeline`:
```
/var/log/audiobook-pipeline/*.log {
    daily
    rotate 14
    copytruncate
    compress
    delaycompress
    notifempty
    missingok
    dateext
    dateformat -%Y%m%d
    su readarr media
}
```

**Why copytruncate:** Pipeline appends to log file continuously, no signal handling to reopen. copytruncate allows rotation without restarting pipeline (brief log loss window acceptable).

## 7. Error Recovery & Retry Logic

### Extended Manifest Schema
```json
{
  "book_hash": "abc123",
  "status": "pending",
  "retry_count": 0,
  "max_retries": 3,
  "last_error": {
    "stage": "convert",
    "timestamp": "2026-02-20T10:15:30Z",
    "exit_code": 1,
    "message": "ffmpeg failed: corrupt input file",
    "category": "permanent"
  },
  "stages": {
    "validate": { "status": "pending" },
    "concat": { "status": "pending" },
    "convert": { "status": "pending" },
    "asin": { "status": "pending" },
    "metadata": { "status": "pending" },
    "organize": { "status": "pending" },
    "archive": { "status": "pending" },
    "cleanup": { "status": "pending" }
  },
  "metadata": {}
}
```

### Failure Categorization (Exit Codes)

| Exit Code | Category | Meaning | Action |
|-----------|----------|---------|--------|
| 0 | Success | Normal completion | Mark stage completed |
| 1 | Transient | Network timeout, API unavailable, temp resource exhaustion | Retry up to max_retries |
| 2 | Permanent (config) | Invalid configuration, missing required files | Move to failed/ immediately |
| 3 | Permanent (input) | Corrupt audio file, invalid format | Move to failed/ immediately |
| 4+ | Transient | General errors (assume transient unless proven otherwise) | Retry up to max_retries |

### Error Trap with Retry Logic
```bash
# Extended on_error trap in bin/audiobook-convert
on_error() {
  local lineno="$1"
  local exit_code=$?

  log_error "Pipeline failed at line $lineno (exit=$exit_code)"

  if [[ -z "$CURRENT_STAGE" || -z "${BOOK_HASH:-}" ]]; then
    log_error "No stage or book_hash set, cannot update manifest"
    return 0
  fi

  # Categorize failure
  local category="transient"
  case $exit_code in
    2|3)
      category="permanent"
      log_error "Permanent failure detected (exit=$exit_code)"
      ;;
    *)
      category="transient"
      log_warn "Transient failure detected (exit=$exit_code)"
      ;;
  esac

  # Update manifest with error details
  local timestamp
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  manifest_set_stage "$BOOK_HASH" "$CURRENT_STAGE" "failed" || true
  manifest_update "$BOOK_HASH" \
    ".status = \"failed\"
     | .retry_count += 1
     | .last_error.timestamp = \"$timestamp\"
     | .last_error.exit_code = $exit_code
     | .last_error.stage = \"$CURRENT_STAGE\"
     | .last_error.message = \"Pipeline failed at line $lineno\"
     | .last_error.category = \"$category\"" || true

  # Handle permanent failures immediately
  if [[ "$category" == "permanent" ]]; then
    log_error "Permanent failure -- moving to failed/ directory"
    send_failure_notification "$BOOK_HASH" "$CURRENT_STAGE" "Permanent failure at line $lineno"
    move_to_failed "$BOOK_HASH" "$SOURCE_PATH"
    exit $exit_code
  fi

  # Check retry limit for transient failures
  local retry_count
  retry_count=$(manifest_read "$BOOK_HASH" "retry_count" || echo "0")
  local max_retries
  max_retries=$(manifest_read "$BOOK_HASH" "max_retries" || echo "3")

  if [[ "$retry_count" -ge "$max_retries" ]]; then
    log_error "Max retries ($max_retries) exceeded -- moving to failed/"
    send_failure_notification "$BOOK_HASH" "$CURRENT_STAGE" "Max retries exceeded after $retry_count attempts"
    move_to_failed "$BOOK_HASH" "$SOURCE_PATH"
    exit 1
  fi

  log_info "Work directory preserved for debugging: ${WORK_DIR:-unknown}"
  log_info "Will retry on next automation cycle (attempt $retry_count/$max_retries)"
}
trap 'on_error $LINENO' ERR
```

### Move to Failed Directory
```bash
# Move failed book to failed/ directory with error context
move_to_failed() {
  local book_hash="$1"
  local source_path="$2"
  local failed_dir="${FAILED_DIR:-/var/lib/audiobook-pipeline/failed}"

  mkdir -p "$failed_dir"

  local book_name
  book_name=$(basename "$source_path")
  local failed_path="$failed_dir/$book_name"

  # Avoid clobbering if name collision
  local counter=1
  while [[ -e "$failed_path" ]]; do
    failed_path="$failed_dir/${book_name}.${counter}"
    counter=$((counter + 1))
  done

  log_error "Moving failed book to: $failed_path"

  if [[ "${DRY_RUN:-false}" != "true" ]]; then
    mv "$source_path" "$failed_path"

    # Copy manifest for debugging context
    local manifest
    manifest=$(manifest_path "$book_hash")
    if [[ -f "$manifest" ]]; then
      cp "$manifest" "$failed_path/pipeline-manifest.json"
    fi

    # Write human-readable error summary
    cat > "$failed_path/ERROR.txt" <<EOF
Pipeline failed after $(manifest_read "$book_hash" "retry_count") attempts.

Last error:
  Stage: $(manifest_read "$book_hash" "last_error.stage")
  Time: $(manifest_read "$book_hash" "last_error.timestamp")
  Exit code: $(manifest_read "$book_hash" "last_error.exit_code")
  Category: $(manifest_read "$book_hash" "last_error.category")
  Message: $(manifest_read "$book_hash" "last_error.message")

Work directory: ${WORK_DIR:-unknown}
Manifest: pipeline-manifest.json
EOF
  fi

  log_info "Failed book moved to $failed_path"
}
```

### Optional Webhook Notification
```bash
send_failure_notification() {
  local book_hash="$1"
  local stage="$2"
  local message="$3"

  local webhook_url="${FAILURE_WEBHOOK_URL:-}"
  [[ -z "$webhook_url" ]] && return 0  # Skip if not configured

  local book_name
  book_name=$(manifest_read "$book_hash" "source_path" | xargs basename)

  local payload
  payload=$(jq -n \
    --arg text "Audiobook pipeline failure: $book_name" \
    --arg stage "$stage" \
    --arg msg "$message" \
    --arg hash "$book_hash" \
    '{
      text: $text,
      fields: [
        { title: "Book", value: $hash, short: true },
        { title: "Stage", value: $stage, short: true },
        { title: "Error", value: $msg }
      ]
    }')

  curl -s -m 5 -X POST -H 'Content-Type: application/json' \
    --data "$payload" "$webhook_url" >/dev/null 2>&1 || true

  log_debug "Failure notification sent to webhook"
}
```

**Configuration:**
```bash
# Optional in config.env
FAILURE_WEBHOOK_URL=""  # Slack/Discord webhook URL
FAILURE_EMAIL=""        # Email address for notifications
```

## 8. Anti-Patterns

### Things to Avoid

1. **Long-running hook scripts**: Readarr 30s timeout will kill the process. Hook must exit in <5s.
2. **No stability checks in cron scanner**: Processing books mid-download causes corrupt M4B files.
3. **Skipping Test events**: Readarr marks connection as failed if hook doesn't exit 0 on Test events.
4. **Retry loops within single pipeline run**: Let automation cycle (cron every 15 min) provide retry. No `sleep` in error handling.
5. **Moving books while processing**: Clean up work directory BEFORE moving to failed/.
6. **Treating all failures as retryable**: Categorize exit codes -- corrupt input (exit 3) won't fix itself.
7. **Using `df -h` for comparisons**: Human-readable sizes ("10G", "500M") don't sort correctly. Use `df -k` for numeric KB values.
8. **PID files for locking**: Use flock instead. PID files leave stale locks on crash.
9. **Webhook failures blocking pipeline**: Always use `|| true` and short timeout (`-m 5`) on curl.
10. **Assuming single book path**: `readarr_addedbookpaths` can contain pipe-separated paths for collections.

## 9. Open Questions

1. **Bookshelf fork compatibility**: Does Bookshelf fix the `readarr_addedbookpaths` manual import bug? **Action:** Test both automatic and manual imports.
2. **Readarr script timeout exact value**: Commonly cited as 30s but not in official docs. **Action:** Assume 30s, design for <5s exit.
3. **inotifywait vs cron for queue processing**: inotify more responsive, cron simpler. **Action:** Start with cron, add inotifywait later if needed.
4. **Trigger file retention policy**: Keep completed/failed files for 7 days? **Action:** Implement cleanup in cron (weekly).
5. **Per-book lock cleanup strategy**: Leave 0-byte lock files or delete after completion? **Action:** Leave files (0 bytes), periodic cleanup via cron.
6. **Retry delay for API failures**: Should Audnexus failures have delay vs immediate retry? **Action:** Start immediate, add 5s delay if rate limiting observed.
7. **3x disk space multiplier sufficient?**: Does ffmpeg/tone create additional temp files? **Action:** Monitor actual usage, adjust if needed.
8. **Failed book auto-cleanup**: Prune failed/ directory after 90 days? **Action:** Document as manual cleanup, consider optional cron job.
9. **Notification log excerpts**: Include last N log lines in webhook? **Action:** Keep notifications simple, point to log file location.
10. **Explicit stage exit codes**: Add exit 3 for known permanent failures? **Action:** Add to stages with known failure modes (convert, validate).

## 10. Sources

### Primary (HIGH confidence)
- [flock man page (man7.org)](https://man7.org/linux/man-pages/man1/flock.1.html)
- [BashFAQ/045 (Greg's Wiki)](https://mywiki.wooledge.org/BashFAQ/045)
- [Readarr CustomScript.cs source code](https://github.com/Readarr/Readarr/blob/develop/src/NzbDrone.Core/Notifications/CustomScript/CustomScript.cs)
- [Logfmt specification](https://brandur.org/logfmt)
- [Better Stack: Logrotate Guide](https://betterstack.com/community/guides/logging/how-to-manage-log-files-with-logrotate-on-ubuntu-20-04/)
- [Datadog: Log File Control with Logrotate](https://www.datadoghq.com/blog/log-file-control-with-logrotate/)
- [logrotate man page](https://man7.org/linux/man-pages/man8/logrotate.8.html)
- [Bash df Command (W3Schools)](https://www.w3schools.com/bash/bash_df.php)
- [Bash du Command (W3Schools)](https://www.w3schools.com/bash/bash_du.php)
- [Baeldung Linux: File Age and Modification Time](https://www.baeldung.com/linux/file-age-and-modification-time)

### Secondary (MEDIUM confidence)
- [Calibre-Web-Automated Readarr integration discussion](https://github.com/crocodilestick/Calibre-Web-Automated/discussions/248)
- [Hackaday: Critical Sections in Bash Scripts](https://hackaday.com/2020/08/18/linux-fu-one-at-a-time-please-critical-sections-in-bash-scripts/)
- [Medium: 12 Bash Scripts for Retry/Backoff](https://medium.com/@obaff/12-bash-scripts-to-implement-intelligent-retry-backoff-error-recovery-a02ab682baae)
- [BashScript.net: Using flock in Bash Scripts](https://bashscript.net/using-flock-in-bash-scripts-manage-file-locks-and-prevent-task-overlaps/)
- [Putorius: Lock Files for Job Control](https://www.putorius.net/lock-files-bash-scripts.html)
- [Baeldung Linux: df Filter by Filesystem Usage](https://www.baeldung.com/linux/df-filter-by-filesystem-usage)
- [Baeldung Linux: Background Jobs in Loop](https://www.baeldung.com/linux/bash-background-jobs-loop)
- [systemd timer vs cron comparison](https://coady.tech/systemd-timer-vs-cron/)
- [Trigger File Based Workflows (Coviant Software)](https://www.coviantsoftware.com/tech-tips/trigger-file-based-workflows-that-was-easy/)
- [Better Stack: Logfmt Guide](https://betterstack.com/community/guides/logging/logfmt/)
- [OneUptime: Shell Scripting Best Practices](https://oneuptime.com/blog/post/2026-02-13-shell-scripting-best-practices/view)

### Tertiary (LOW confidence)
- [Radarr Custom Scripts Wiki](https://wiki.servarr.com/radarr/custom-scripts) - Similar *arr patterns
- [How to Geek: Bash Error Handling Patterns](https://www.howtogeek.com/bash-error-handling-patterns-i-use-in-every-script/)
- [Red Hat: Bash error handling](https://www.redhat.com/en/blog/bash-error-handling)
- Community forum posts about `readarr_addedbookpaths` reliability (needs testing)

---

**Research date:** 2026-02-20
**Valid until:** 2026-03-22 (30 days - stable domain, bash/coreutils patterns change slowly)
