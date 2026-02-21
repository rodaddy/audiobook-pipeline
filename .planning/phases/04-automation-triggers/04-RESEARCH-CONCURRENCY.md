# Phase 4: Automation & Triggers - Concurrency & Disk Space Research

**Researched:** 2026-02-20
**Domain:** Bash concurrency control (flock), disk space pre-flight checks, retry logic, error recovery
**Confidence:** HIGH

## Summary

This research covers the concurrency control and disk space management aspects of Phase 4 (Automation & Triggers). The primary requirement is to ensure only one pipeline instance runs at a time using flock-based mutual exclusion, with support for future per-book locking to enable MAX_JOBS=2 parallel processing. Additional requirements include disk space pre-flight checks (3x input size estimation), retry logic for transient failures, and organized failure handling.

**Key findings:**
- flock provides kernel-level file locking with automatic cleanup on process exit
- The -n (non-blocking) and -E (exit code) flags enable clean second-instance exits without error status
- Per-book locking can be implemented by hashing the source path to generate unique lock file names
- df + du provide reliable disk space checking with parseable byte-level output
- Bash retry patterns with counters are simple and robust for transient failures
- Exponential backoff is overkill for local file operations; immediate retry with max attempts is sufficient

**Primary recommendation:** Use flock with -n -E 0 for clean second-instance exits. Implement global singleton lock first (Phase 4), defer per-book locking to when MAX_JOBS > 1 support is actually needed.

## Standard Stack

### Core Tools
| Tool | Version | Purpose | Why Standard |
|------|---------|---------|--------------|
| flock | util-linux 2.39.3 (Ubuntu 24.04) | Exclusive file locking | Kernel-level, automatic cleanup, POSIX-compliant |
| df | coreutils 9.x | Disk space availability | Standard Unix tool, universal availability |
| du | coreutils 9.x | Directory size calculation | Standard Unix tool, block-accurate sizing |
| jq | 1.6+ | Manifest retry count tracking | Already used in lib/manifest.sh |

### Supporting
| Tool | Version | Purpose | When to Use |
|------|---------|---------|-------------|
| wait -n | bash 4.3+ | Background job completion | Future MAX_JOBS=2 implementation |
| stat | coreutils 9.x | Filesystem metadata | Alternative to df for specific mount checking |

### Already in Codebase
- lib/manifest.sh: JSON state tracking, already supports adding retry_count field
- lib/core.sh: Structured logging (log_info, log_error, die)
- set -euo pipefail: Strict error handling baseline

## Architecture Patterns

### Recommended Lock File Structure
```
/var/lib/audiobook-pipeline/locks/
├── pipeline.lock           # Global singleton lock (Phase 4)
└── books/                  # Per-book locks (future MAX_JOBS=2)
    ├── abc123.lock         # Per-book hash lock
    └── def456.lock
```

**Rationale:** Global lock prevents concurrent instances. Per-book locks (future) allow parallel processing of different books while preventing duplicate work on same book.

### Pattern 1: Singleton Global Lock (Clean Second Instance Exit)

**What:** Ensure only one pipeline instance runs globally, second instance exits cleanly (not as error).

**When to use:** Phase 4 automation (cron + webhook triggers).

**Example:**
```bash
#!/usr/bin/env bash
# bin/audiobook-convert

LOCK_DIR="/var/lib/audiobook-pipeline/locks"
LOCK_FILE="$LOCK_DIR/pipeline.lock"
mkdir -p "$LOCK_DIR"

# Open FD 200 on lock file, attempt exclusive lock
exec 200>"$LOCK_FILE"
if ! flock -n -E 0 200; then
  # Exit code 0 (not an error) if lock already held
  log_info "Another pipeline instance is running. Exiting cleanly."
  exit 0
fi

# Lock acquired, continue with processing
# Lock automatically released when script exits (FD 200 closes)
```

**Source:** [BashFAQ/045](https://mywiki.wooledge.org/BashFAQ/045), [flock man page](https://man7.org/linux/man-pages/man1/flock.1.html)

**Key details:**
- `-n` (non-blocking): Fail immediately if lock cannot be acquired instead of waiting
- `-E 0`: Set exit code to 0 when lock acquisition fails (default is 1)
- File descriptor 200: High number to avoid conflicts with stdin/stdout/stderr (0,1,2)
- No explicit unlock needed: Lock released automatically when FD closes (on script exit, including crashes)

### Pattern 2: Per-Book Locking (Future MAX_JOBS=2)

**What:** Allow parallel processing of different books while preventing duplicate work on the same book.

**When to use:** When MAX_JOBS > 1 support is implemented (post-Phase 4).

**Example:**
```bash
# Per-book lock based on book hash
acquire_book_lock() {
  local book_hash="$1"
  local lock_file="$LOCK_DIR/books/$book_hash.lock"
  mkdir -p "$(dirname "$lock_file")"

  # Use FD 201 for per-book locks (global uses 200)
  exec 201>"$lock_file"
  if ! flock -n -E 0 201; then
    log_info "Book $book_hash is being processed by another job. Skipping."
    return 1  # Signal to skip this book
  fi

  return 0  # Lock acquired, proceed with book
}

# In main loop:
for book_dir in /mnt/media/_incoming/*; do
  book_hash=$(hash_book_path "$book_dir")

  # Try to acquire per-book lock
  if acquire_book_lock "$book_hash"; then
    # Process book in background
    process_book "$book_dir" &

    # Limit concurrent jobs
    while (( $(jobs -r | wc -l) >= MAX_JOBS )); do
      wait -n  # Wait for next job to complete
    done
  fi
done

wait  # Wait for all remaining jobs
```

**Source:** [Parallel Processing in Bash](https://jwkenney.github.io/parellel-processing-in-bash/), [Baeldung Linux - Background Jobs Loop](https://www.baeldung.com/linux/bash-background-jobs-loop)

**Key details:**
- Each book gets its own lock file based on hash (unique identifier)
- `wait -n` waits for *next* background job to complete (bash 4.3+)
- `jobs -r` counts running background jobs
- Per-book lock FD (201) is separate from global lock FD (200)

### Pattern 3: Disk Space Pre-flight Check

**What:** Estimate required disk space (3x input size) and verify available space before processing.

**When to use:** Before starting any book conversion (in validate stage).

**Example:**
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

**Source:** [Bash df Command](https://www.w3schools.com/bash/bash_df.php), [Bash du Command](https://www.w3schools.com/bash/bash_du.php), [Baeldung Linux - df filter by usage](https://www.baeldung.com/linux/df-filter-by-filesystem-usage)

**Key details:**
- `du -sk` reports size in kilobytes (k-suffix), single-column output for directory total
- `df -k` reports in 1024-byte blocks, field 4 is available space
- `awk 'NR==2 {print $4}'` parses second line (skips header), extracts available space
- Numeric comparison: `(( available_kb < required_kb ))`
- 3x multiplier accounts for: (1) source files, (2) intermediate temp files, (3) final M4B output

**Why 3x:**
- 1x = original source files (remain until archive stage)
- 1x = concatenated temp file or merged audio stream
- 1x = final M4B with metadata/chapters
- Safety margin for multiple concurrent jobs (future MAX_JOBS=2)

### Pattern 4: Retry Logic with Counter

**What:** Track retry attempts in manifest, retry up to 3 times, then move to failed/ directory.

**When to use:** Transient failures (network timeouts for Audnexus API, temporary I/O errors).

**Example:**
```bash
# Retry a stage up to MAX_RETRIES times
# Args: BOOK_HASH STAGE COMMAND...
retry_stage() {
  local book_hash="$1"
  local stage="$2"
  shift 2
  local command=("$@")

  local max_retries=3
  local retry_count
  retry_count=$(manifest_read "$book_hash" "stages.${stage}.retry_count" || echo "0")

  log_info "Attempting stage $stage (retry $retry_count/$max_retries)"

  if "${command[@]}"; then
    # Success: reset retry count, mark completed
    manifest_update "$book_hash" ".stages.${stage}.retry_count = 0"
    manifest_set_stage "$book_hash" "$stage" "completed"
    return 0
  else
    # Failure: increment retry count
    retry_count=$((retry_count + 1))
    manifest_update "$book_hash" ".stages.${stage}.retry_count = $retry_count"

    if (( retry_count >= max_retries )); then
      # Max retries exceeded: move to failed/
      log_error "Stage $stage failed after $max_retries attempts. Moving to failed/."
      move_to_failed "$book_hash" "$stage" "Max retries exceeded"
      return 1
    else
      # Retry available: mark for retry
      manifest_set_stage "$book_hash" "$stage" "retry_pending"
      log_warn "Stage $stage failed (attempt $retry_count/$max_retries). Will retry."
      return 1
    fi
  fi
}
```

**Source:** [Medium - 12 Bash Scripts for Retry/Backoff](https://medium.com/@obaff/12-bash-scripts-to-implement-intelligent-retry-backoff-error-recovery-a02ab682baae), [TutorialKart - Bash Retry Logic](https://www.tutorialkart.com/bash-shell-scripting/bash-script-retry-logic-for-failed-commands/)

**Key details:**
- Retry count stored in manifest: `.stages.${stage}.retry_count`
- Immediate retry (no delay) - appropriate for local file operations
- After 3 failures, move to failed/ directory (prevents infinite retry loop)
- Reset retry count to 0 on success (so subsequent stages start fresh)

**Exponential backoff:** NOT recommended for this pipeline. Exponential backoff is for network/API rate limiting. Our failures are local (disk space, file I/O, missing dependencies) where immediate retry or skip is more appropriate.

### Pattern 5: Move Failed Books to failed/ Directory

**What:** Move books that exceed max retries to a failed/ directory with error context.

**When to use:** After retry_count >= 3 on any stage.

**Example:**
```bash
# Move failed book to failed/ directory with error context
# Args: BOOK_HASH STAGE ERROR_MESSAGE
move_to_failed() {
  local book_hash="$1"
  local stage="$2"
  local error_msg="$3"

  local source_path
  source_path=$(manifest_read "$book_hash" "source_path")

  local failed_dir="/mnt/media/_incoming/failed"
  mkdir -p "$failed_dir"

  local book_name
  book_name=$(basename "$source_path")
  local failed_path="$failed_dir/$book_name"

  # Write error context file
  local error_file="$failed_path/.error"
  {
    echo "book_hash=$book_hash"
    echo "failed_stage=$stage"
    echo "error_message=$error_msg"
    echo "failed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$error_file"

  # Move source directory to failed/
  if [[ -d "$source_path" ]]; then
    mv "$source_path" "$failed_path"
    log_error "Moved $book_name to failed/ after $stage failure: $error_msg"
  fi

  # Update manifest status
  manifest_update "$book_hash" \
    ".status = \"failed\" | .failed_stage = \"$stage\" | .failed_at = \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
}
```

**Source:** [How To Geek - Bash Error Handling](https://www.howtogeek.com/bash-error-handling-patterns-i-use-in-every-script/), [Graham Watts - Logging in Bash](https://grahamwatts.co.uk/bash-logging/)

**Key details:**
- `.error` file stores context: book_hash, failed_stage, error_message, timestamp
- Source directory moved atomically (within same filesystem)
- Manifest updated with failed status for audit trail
- Human-readable error file enables manual investigation/retry

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| File locking | Custom PID files, mkdir-based locks | flock | Kernel-level, automatic cleanup, race-free |
| Background job limiting | Manual job counting with ps/pgrep | bash wait -n + jobs -r | Built-in, reliable, no polling needed |
| Disk space parsing | Regex parsing of df -h output | df -k + awk for numeric fields | Human-readable formats (10G, 500M) don't sort/compare correctly |
| Exponential backoff | Complex sleep $(( 2**attempt )) loops | Simple retry counter for local ops | Local failures are immediate (no network latency), backoff adds no value |

**Key insight:** Bash built-ins (flock, wait -n, test -f) are more reliable than custom implementations. The kernel handles race conditions better than userspace scripts.

## Common Pitfalls

### Pitfall 1: Lock File Cleanup on Abnormal Exit

**What goes wrong:** If using PID files or mkdir-based locks, crashes leave stale lock files. Second instance sees stale lock, exits forever (manual cleanup required).

**Why it happens:** Custom lock mechanisms require explicit cleanup via trap handlers. If script is killed via SIGKILL (-9), traps don't run.

**How to avoid:** Use flock. Kernel automatically releases lock when file descriptor closes (on *any* exit, including crashes).

**Warning signs:** Lock files accumulate in lock directory, pipeline refuses to run after crashes.

**Confidence:** HIGH - [flock man page](https://man7.org/linux/man-pages/man1/flock.1.html) explicitly documents automatic cleanup behavior.

### Pitfall 2: df -h Output Doesn't Sort/Compare Correctly

**What goes wrong:** Using `df -h` (human-readable) produces output like "10G", "500M". String comparison treats "500M" > "10G" (alphabetic sort). Arithmetic comparison fails entirely.

**Why it happens:** Human-readable sizes are strings, not numbers. Bash arithmetic requires numeric input.

**How to avoid:** Use `df -k` for numeric KB values. Parse with awk to extract field 4 (available space). Perform arithmetic comparison: `(( available < required ))`.

**Warning signs:** Disk space checks pass when they should fail, or vice versa.

**Confidence:** HIGH - [Baeldung Linux - df filter by usage](https://www.baeldung.com/linux/df-filter-by-filesystem-usage), [Bash df Command](https://www.w3schools.com/bash/bash_df.php)

### Pitfall 3: flock Exit Code 1 Triggers Error on Clean Skip

**What goes wrong:** Using `flock -n 200` without `-E 0` returns exit code 1 when lock cannot be acquired. Under `set -e`, this causes immediate script termination. Second instance treats "another instance running" as an error.

**Why it happens:** Default flock exit code for lock failure is 1. `set -e` treats any non-zero exit as a fatal error.

**How to avoid:** Use `flock -n -E 0 200` to set exit code to 0 on lock failure. Check return value explicitly: `if ! flock -n -E 0 200; then exit 0; fi`.

**Warning signs:** Cron emails report "error" when second instance runs, even though this is expected behavior.

**Confidence:** HIGH - [flock man page](https://man7.org/linux/man-pages/man1/flock.1.html) documents -E option explicitly.

### Pitfall 4: du Counts Filesystem Blocks, Not Actual Data

**What goes wrong:** `du` reports disk space used, which depends on filesystem block size (typically 4KB). A 100-byte file consumes 4KB on disk. Small files cause du to overestimate significantly.

**Why it happens:** Filesystems allocate space in blocks. du reports blocks allocated, not file content size.

**How to avoid:** For audiobooks (large MP3/M4B files), this is negligible. If concerned, use `du --apparent-size` for file content size. For disk space checking, block-based du is actually *correct* (that's the space we need).

**Warning signs:** du reports 10MB for a directory with 1MB of text files.

**Confidence:** HIGH - [du man page](https://linux.die.net/man/1/du), [How to Get Size of File/Directory](https://linuxize.com/post/how-get-size-of-file-directory-linux/)

### Pitfall 5: Per-Book Lock FD Conflicts with Global Lock

**What goes wrong:** Using the same file descriptor (e.g., FD 200) for both global lock and per-book lock causes second lock to override the first.

**Why it happens:** File descriptors are per-process. Assigning to FD 200 twice closes the first file and opens the second.

**How to avoid:** Use separate FDs: 200 for global lock, 201 for per-book locks. Document FD allocation in comments.

**Warning signs:** Global lock released unexpectedly, concurrent instances run.

**Confidence:** MEDIUM - General file descriptor behavior, not flock-specific.

## Code Examples

Verified patterns from official sources:

### Global Singleton Lock (Production-Ready)

```bash
#!/usr/bin/env bash
# bin/audiobook-convert

set -euo pipefail

# Source libraries
source "$(dirname "$0")/../lib/core.sh"
source "$(dirname "$0")/../lib/manifest.sh"

# Global lock setup
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

# Main processing loop
# Lock automatically released when script exits
```

**Source:** [BashFAQ/045](https://mywiki.wooledge.org/BashFAQ/045)

### Disk Space Pre-flight (Integrated with Validation)

```bash
# In bin/audiobook-convert, validate stage
validate_book() {
  local book_dir="$1"
  local book_hash="$2"

  STAGE="validate"
  log_info "Validating $book_dir"

  # Check disk space (3x source size required)
  if ! check_disk_space "$book_dir" "$WORK_DIR"; then
    move_to_failed "$book_hash" "validate" "Insufficient disk space"
    return 1
  fi

  # Additional validation: MP3 files exist, etc.
  # ...

  manifest_set_stage "$book_hash" "validate" "completed"
}

check_disk_space() {
  local source_dir="$1"
  local work_dir="$2"

  local source_size_kb
  source_size_kb=$(du -sk "$source_dir" | awk '{print $1}')

  local required_kb=$((source_size_kb * 3))

  local available_kb
  available_kb=$(df -k "$work_dir" | awk 'NR==2 {print $4}')

  log_info "Disk space: source=${source_size_kb}KB required=${required_kb}KB available=${available_kb}KB"

  if (( available_kb < required_kb )); then
    log_error "Insufficient disk space: need ${required_kb}KB, have ${available_kb}KB"
    return 1
  fi

  return 0
}
```

**Source:** [Bash df Command](https://www.w3schools.com/bash/bash_df.php), [Bash du Command](https://www.w3schools.com/bash/bash_du.php)

### Retry Logic with Manifest Tracking

```bash
# Extend lib/manifest.sh with retry count field
# Manifest structure includes: .stages.<stage>.retry_count

# In bin/audiobook-convert, retry-aware stage execution
execute_stage_with_retry() {
  local book_hash="$1"
  local stage="$2"
  local stage_function="$3"  # Function name to call

  local max_retries=3
  local retry_count
  retry_count=$(manifest_read "$book_hash" "stages.${stage}.retry_count")
  [[ -z "$retry_count" ]] && retry_count=0

  log_info "Stage $stage attempt $((retry_count + 1))/$max_retries"

  if $stage_function "$book_hash"; then
    # Success: reset retry count, mark completed
    manifest_update "$book_hash" ".stages.${stage}.retry_count = 0"
    manifest_set_stage "$book_hash" "$stage" "completed"
    return 0
  else
    # Failure: increment retry count
    retry_count=$((retry_count + 1))
    manifest_update "$book_hash" ".stages.${stage}.retry_count = $retry_count"

    if (( retry_count >= max_retries )); then
      log_error "Stage $stage failed after $max_retries attempts"
      move_to_failed "$book_hash" "$stage" "Max retries exceeded"
      return 1
    else
      log_warn "Stage $stage failed (attempt $retry_count/$max_retries). Will retry on next run."
      manifest_set_stage "$book_hash" "$stage" "pending"
      return 1
    fi
  fi
}
```

**Source:** [Medium - Bash Retry/Backoff](https://medium.com/@obaff/12-bash-scripts-to-implement-intelligent-retry-backoff-error-recovery-a02ab682baae)

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| PID files for locking | flock with -n -E 0 | util-linux 2.20+ (2011) | Automatic cleanup, race-free |
| wait (all jobs) | wait -n (next job) | bash 4.3 (2014) | Fine-grained concurrency control |
| mkdir-based locks | flock on file descriptors | util-linux 2.x+ | Kernel-level mutual exclusion |
| Parsing df -h with regex | df -k + awk numeric comparison | Always available | Reliable arithmetic comparison |

**Deprecated/outdated:**
- **PID file locking:** Stale files on crash, race conditions on check-then-create. Use flock instead.
- **mkdir for mutual exclusion:** Similar issues to PID files. Use flock.
- **Polling with sleep:** Wastes CPU. Use wait -n to block until job completes.

## Open Questions

1. **Per-book lock cleanup strategy**
   - What we know: flock releases on FD close, but lock *files* accumulate
   - What's unclear: Should we delete lock files after book completes, or leave them?
   - Recommendation: Leave lock files (they're 0 bytes), or periodic cleanup via cron. Deleting immediately risks race with second instance checking lock.

2. **Retry delay for API failures vs file I/O failures**
   - What we know: Audnexus API failures benefit from retry with delay (rate limiting, temporary outages)
   - What's unclear: Should we distinguish between failure types in retry logic?
   - Recommendation: Start with immediate retry for all failures. If Audnexus rate limiting becomes an issue, add 5-second delay for metadata stage only.

3. **Disk space check: 3x multiplier sufficient?**
   - What we know: Need space for source + intermediate + output
   - What's unclear: Does ffmpeg create additional temp files? Does tone CLI?
   - Recommendation: Start with 3x, monitor actual usage, adjust if needed. Worst case: pre-flight check passes, conversion fails mid-process (caught by ERR trap).

## Sources

### Primary (HIGH confidence)
- [flock man page (man7.org)](https://man7.org/linux/man-pages/man1/flock.1.html) - Official Linux manual for flock utility
- [BashFAQ/045 (Greg's Wiki)](https://mywiki.wooledge.org/BashFAQ/045) - Canonical bash locking guide
- [Bash df Command (W3Schools)](https://www.w3schools.com/bash/bash_df.php) - df usage and examples
- [Bash du Command (W3Schools)](https://www.w3schools.com/bash/bash_du.php) - du usage and examples
- [Baeldung Linux - Ensure Only One Instance Running](https://www.baeldung.com/linux/bash-ensure-instance-running) - flock patterns for singleton enforcement
- [Baeldung Linux - Background Jobs in Loop](https://www.baeldung.com/linux/bash-background-jobs-loop) - wait -n concurrency patterns

### Secondary (MEDIUM confidence)
- [BashScript.net - Using flock in Bash Scripts](https://bashscript.net/using-flock-in-bash-scripts-manage-file-locks-and-prevent-task-overlaps/) - Practical flock examples
- [Putorius - Lock Files for Job Control](https://www.putorius.net/lock-files-bash-scripts.html) - Lock file best practices including trap handlers
- [Medium - 12 Bash Scripts for Retry/Backoff](https://medium.com/@obaff/12-bash-scripts-to-implement-intelligent-retry-backoff-error-recovery-a02ab682baae) - Retry counter and exponential backoff patterns
- [Baeldung Linux - df Filter by Filesystem Usage](https://www.baeldung.com/linux/df-filter-by-filesystem-usage) - awk patterns for df parsing
- [How To Geek - Bash Error Handling Patterns](https://www.howtogeek.com/bash-error-handling-patterns-i-use-in-every-script/) - Error handling and logging best practices

### Tertiary (LOW confidence, for context)
- [GitHub Gist - Retry with Exponential Backoff](https://gist.github.com/28611bfaa2395072119464521d48729a) - Example retry function (polynomial backoff)
- [Parallel Processing in Bash (jwkenney)](https://jwkenney.github.io/parellel-processing-in-bash/) - General parallel processing patterns

## Metadata

**Confidence breakdown:**
- flock patterns: HIGH - Official man pages, Greg's Wiki (canonical bash resource), verified with multiple sources
- Disk space checking: HIGH - Standard coreutils, well-documented behavior, simple arithmetic
- Retry logic: HIGH - Straightforward counter pattern, already using jq for manifest updates
- Per-book locking: MEDIUM - Pattern is sound, but implementation deferred to future (not tested in this phase)

**Research date:** 2026-02-20
**Valid until:** 60 days (bash/coreutils behavior is stable, minimal API churn)
