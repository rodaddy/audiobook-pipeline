# Phase 3: Folder Organization & Output - Archive Research

**Researched:** 2026-02-20
**Domain:** File archiving, NFS operations, integrity validation
**Confidence:** HIGH

## Summary

This research focuses on the archive stage, NFS output handling, and verification gates for Phase 3. The primary challenges are: (1) validating M4B integrity before archiving originals, (2) safely moving files to NFS with root squash active, and (3) handling the archive operation atomically with proper error handling.

The current pipeline uses `install -o UID -g GID` for NFS file operations, which will fail with root squash because the readarr user (UID 2018) cannot chown files on NFS to arbitrary UIDs. The solution is to rely on the NFS server's automatic UID mapping and use `cp + chmod` or `rsync` without ownership flags.

**Primary recommendation:** Create separate organize (stage 07) and archive (stage 08) stages. Use ffprobe validation with format duration, codec presence, and container validity checks. Replace `install` with `cp` for NFS operations, letting root squash handle ownership mapping. Use marker files in manifest for idempotency.

## Standard Stack

### Core Tools

| Tool | Version | Purpose | Why Standard |
|------|---------|---------|--------------|
| ffprobe | 6.x+ | M4B integrity validation | Part of ffmpeg suite, already used in pipeline |
| cp | coreutils 9.x | File copy to NFS | Works correctly with root squash, no chown required |
| rsync | 3.2.x+ | Alternative for NFS copy | Better retry/resume, preserves permissions via -a flag |
| jq | 1.6+ | Manifest updates | Already used throughout pipeline |
| bash | 5.x+ | Scripting | Pipeline standard |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| lib/ffmpeg.sh | Current | Audio file validation helpers | Existing functions: get_duration(), get_codec(), validate_audio_file() |
| lib/manifest.sh | Current | State tracking | manifest_set_stage(), manifest_update() for idempotency |
| lib/core.sh | Current | Logging and dry-run support | run() wrapper for all file operations |
| lib/sanitize.sh | Current | Filename sanitization | Already used in cleanup stage for safe filenames |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| cp | install -D | install fails on NFS with root squash (cannot chown), cp works but needs mkdir -p |
| cp | rsync -a | rsync is more robust (retry/resume) but slower for single files, overkill for local to NFS |
| ffprobe validation | Full decode test | Decoding is 100-1000x slower, ffprobe is sufficient for container/stream validation |

**Installation:**
```bash
# Already available on standard Linux systems
# ffprobe comes with ffmpeg package (already required)
# cp, rsync are coreutils (always present)
```

## Architecture Patterns

### Recommended Stage Structure

Current pipeline has 6 stages. New stages will be:
- **Stage 07 (organize):** Create Plex-compatible folder structure, move M4B to OUTPUT_DIR
- **Stage 08 (archive):** Validate M4B integrity, archive original MP3s, cleanup work directory

```
stages/
├── 01-validate.sh        # Existing
├── 02-concat.sh          # Existing
├── 03-convert.sh         # Existing (creates M4B)
├── 04-cleanup.sh         # RENAME to 09-cleanup.sh (final stage)
├── 05-asin.sh            # Existing
├── 06-metadata.sh        # Existing
├── 07-organize.sh        # NEW: Plex folder structure + NFS output
└── 08-archive.sh         # NEW: Validate + archive originals
```

**Stage order in STAGE_ORDER array:**
```bash
STAGE_ORDER=(validate concat convert asin metadata organize archive cleanup)
```

### Pattern 1: NFS-Safe File Operations

**What:** Copy files to NFS without using install command or chown operations
**When to use:** Always when root squash is active on NFS mount
**Example:**
```bash
# DON'T: This fails with root squash (cannot chown on NFS)
install -m 644 -o 2018 -g 2000 "$source" "$nfs_dest"

# DO: Let NFS server map ownership automatically
cp "$source" "$nfs_dest"
chmod 644 "$nfs_dest"

# OR: Use rsync for better error handling
rsync -a --chmod=644 "$source" "$nfs_dest"
```

**Why it works:**
- NFS with root squash maps client UID 2018 (readarr) to same UID on server
- Server-side ownership is set correctly by NFS automatically
- Client cannot/should not attempt chown on NFS mount

### Pattern 2: ffprobe Integrity Validation

**What:** Validate M4B before archiving originals using ffprobe
**When to use:** Before any destructive operation (archiving/deleting source files)
**Example:**
```bash
# Source: Current pipeline lib/ffmpeg.sh + ffprobe documentation
validate_m4b_before_archive() {
  local m4b_file="$1"

  # 1. File exists and non-empty
  if [[ ! -s "$m4b_file" ]]; then
    log_error "M4B missing or empty: $m4b_file"
    return 1
  fi

  # 2. Container is valid (ffprobe can parse it)
  if ! ffprobe -v error "$m4b_file" >/dev/null 2>&1; then
    log_error "M4B failed ffprobe validation: $m4b_file"
    return 1
  fi

  # 3. Duration > 0 (not a zero-length audio)
  local duration
  duration=$(get_duration "$m4b_file")
  if (( $(echo "$duration <= 0" | bc -l) )); then
    log_error "M4B has zero duration: $m4b_file"
    return 1
  fi

  # 4. Codec is AAC (as expected from convert stage)
  local codec
  codec=$(get_codec "$m4b_file")
  if [[ "$codec" != "aac" ]]; then
    log_warn "Unexpected codec: $codec (expected aac)"
  fi

  # 5. Format is mov/mp4 container family
  local format
  format=$(ffprobe -v error -show_entries format=format_name \
    -of default=noprint_wrappers=1:nokey=1 "$m4b_file")
  if [[ "$format" != *"mov"* ]] && [[ "$format" != *"mp4"* ]]; then
    log_error "Invalid container format: $format"
    return 1
  fi

  log_info "M4B validated: duration=${duration}s codec=$codec format=$format"
  return 0
}
```

**Validation checklist:**
- ✅ File exists and has non-zero size
- ✅ ffprobe can parse container (no corruption)
- ✅ Duration > 0 (has actual audio content)
- ✅ Codec present (aac expected)
- ✅ Container valid (mov/mp4 family)

### Pattern 3: Idempotent Archive with Marker Files

**What:** Track archive completion in manifest to make stage re-runnable
**When to use:** Always for stages that move/delete files
**Example:**
```bash
stage_archive() {
  # Check if already archived
  local archive_status
  archive_status=$(manifest_read "$BOOK_HASH" "stages.archive.status")
  if [[ "$archive_status" == "completed" ]]; then
    log_info "Archive already completed, skipping"
    return 0
  fi

  # Validate M4B before archiving originals
  local m4b_path
  m4b_path=$(manifest_read "$BOOK_HASH" "stages.organize.output_path")
  if ! validate_m4b_before_archive "$m4b_path"; then
    die "M4B validation failed, refusing to archive originals"
  fi

  # Archive originals (only if validation passed)
  local archive_dir="/mnt/archive/audiobooks"
  local source_basename
  source_basename=$(basename "$SOURCE_PATH")
  local archive_path="$archive_dir/$source_basename"

  # Idempotent: check if already exists
  if [[ -d "$archive_path" ]]; then
    log_info "Archive already exists: $archive_path"
  else
    mkdir -p "$(dirname "$archive_path")"
    mv "$SOURCE_PATH" "$archive_path"
    log_info "Archived originals to: $archive_path"
  fi

  # Mark as completed AFTER successful archive
  manifest_set_stage "$BOOK_HASH" "archive" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.archive.archive_path = \"$archive_path\""
}
```

### Pattern 4: Plex-Compatible Output Structure

**What:** Author/Title folder structure for Plex audiobook library
**When to use:** Stage 07 (organize) - creating final output structure
**Example:**
```bash
# Source: Plex Audiobook Guide (seanap/Plex-Audiobook-Guide)
stage_organize() {
  # Read metadata from stage 06
  local author
  author=$(manifest_read "$BOOK_HASH" "metadata.author")
  local title
  title=$(manifest_read "$BOOK_HASH" "metadata.title")

  # Sanitize for filesystem
  local safe_author
  safe_author=$(sanitize_filename "$author")
  local safe_title
  safe_title=$(sanitize_filename "$title")

  # Create Author/Title structure on NFS
  local output_base="/mnt/media/AudioBooks"
  local book_dir="$output_base/$safe_author/$safe_title"

  # mkdir -p is idempotent
  run mkdir -p "$book_dir"

  # Get M4B from cleanup stage (currently stage 04)
  local m4b_source
  m4b_source=$(manifest_read "$BOOK_HASH" "stages.cleanup.output_path")

  # Copy to Plex structure (NFS-safe, no chown)
  local m4b_dest="$book_dir/${safe_title}.m4b"
  if [[ -f "$m4b_dest" ]]; then
    log_info "Already organized: $m4b_dest"
  else
    run cp "$m4b_source" "$m4b_dest"
    run chmod 644 "$m4b_dest"
    log_info "Organized to: $m4b_dest"
  fi

  # Update manifest
  manifest_set_stage "$BOOK_HASH" "organize" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.organize.output_path = \"$m4b_dest\"
     | .stages.organize.author = \"$safe_author\"
     | .stages.organize.title = \"$safe_title\""
}
```

### Anti-Patterns to Avoid

- **Using install command on NFS with root squash:** Fails because client cannot chown on NFS
- **Archiving before validating M4B:** Risk of losing originals if M4B is corrupted
- **Renaming files across filesystems:** Not atomic, use cp/rsync then delete source
- **Skipping idempotency checks:** Re-running stage will fail if files already moved
- **Flat output structure:** Plex requires Author/Title structure for proper metadata

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Audio integrity validation | Custom parser | ffprobe with show_entries | Container/codec validation is complex, ffprobe handles all formats |
| NFS permission handling | Custom chown wrapper | cp + chmod OR rsync -a | Root squash behavior varies by server, standard tools handle it correctly |
| Atomic file moves | Custom temp file logic | cp + sync + rm OR rsync --remove-source-files | Cross-filesystem moves aren't atomic anyway, standard tools provide best-effort |
| Directory path sanitization | Custom regex | lib/sanitize.sh (existing) | Edge cases (unicode, reserved chars) already handled |
| Idempotency tracking | Custom marker files | Manifest stages (existing) | State already tracked, no new mechanism needed |

**Key insight:** The pipeline already has robust patterns for validation (lib/ffmpeg.sh), state tracking (lib/manifest.sh), and safe execution (lib/core.sh run()). Don't reinvent these for archive stage.

## Common Pitfalls

### Pitfall 1: Install Command Fails on NFS with Root Squash

**What goes wrong:** `install -o 2018 -g 2000 file.m4b /mnt/nfs/dest/` fails with "Operation not permitted"

**Why it happens:**
- Root squash maps root UID to nobody (or anonymous UID)
- Even non-root users (readarr UID 2018) cannot chown files on NFS to arbitrary UIDs
- The `install` command tries to chown after copy, which NFS rejects

**How to avoid:**
- Use `cp` + `chmod` instead of `install`
- Let NFS server handle ownership mapping automatically
- Client UID 2018 maps to same UID on server via NFS ID mapping

**Warning signs:**
- "Operation not permitted" errors on NFS mounts
- Files created with wrong ownership (nobody:nogroup)
- Works locally but fails on NFS

**Code fix:**
```bash
# BEFORE (fails on NFS)
install -m 644 -o 2018 -g 2000 "$source" "$nfs_dest"

# AFTER (works with root squash)
cp "$source" "$nfs_dest"
chmod 644 "$nfs_dest"
```

### Pitfall 2: Archiving Before M4B Validation

**What goes wrong:** Original MP3s archived/deleted, then M4B discovered to be corrupted

**Why it happens:**
- Archive stage runs before validation
- ffprobe check in cleanup stage is basic (container parseable)
- Deeper corruption (truncated audio, missing frames) not detected until playback

**How to avoid:**
- Archive stage MUST validate M4B before moving originals
- Validation checks: duration > 0, codec present, container valid, file size reasonable
- Use ffprobe with multiple show_entries (format + stream validation)

**Warning signs:**
- M4B plays but stops partway through (truncated)
- Duration reported correctly but actual playback shorter
- File size much smaller than expected for bitrate × duration

**Validation code:**
```bash
# Check duration matches expected size
local duration=$(get_duration "$m4b_file")
local file_size=$(stat -f%z "$m4b_file")
local bitrate=$(get_bitrate "$m4b_file")

# Expected size = duration * bitrate / 8 (bits to bytes)
local expected_size=$(echo "$duration * $bitrate / 8" | bc)
local size_ratio=$(echo "$file_size / $expected_size" | bc -l)

# Should be close to 1.0 (within 10% tolerance for container overhead)
if (( $(echo "$size_ratio < 0.9 || $size_ratio > 1.1" | bc -l) )); then
  log_warn "File size mismatch: expected=$expected_size actual=$file_size"
fi
```

### Pitfall 3: Cross-Filesystem Move Not Atomic

**What goes wrong:** `mv /local/file /nfs/dest` fails partway, leaving file in inconsistent state

**Why it happens:**
- `mv` across filesystems becomes `cp` + `rm`
- If copy fails partway (NFS disconnect, disk full), source remains but partial dest exists
- If rm fails after successful copy, file exists in both places

**How to avoid:**
- Use `cp` then verify, then `rm` source (explicit two-step)
- For NFS, add retry logic with mount checks
- Store state in manifest between copy and delete
- Use rsync with --remove-source-files for atomic-ish behavior

**Warning signs:**
- Partial files on destination after failure
- Source file missing but destination incomplete
- Disk space inconsistencies

**Safe pattern:**
```bash
# DON'T: Non-atomic across filesystems
mv "$SOURCE_PATH" "$archive_path"

# DO: Explicit copy, verify, delete
cp -r "$SOURCE_PATH" "$archive_path"
if [[ ! -d "$archive_path" ]]; then
  die "Archive copy failed: $archive_path"
fi
manifest_update "$BOOK_HASH" ".stages.archive.copied = true"

# Now safe to delete source (state recorded in manifest)
rm -rf "$SOURCE_PATH"
```

### Pitfall 4: NFS Unavailable During Archive

**What goes wrong:** NFS mount is stale/unmounted when archive stage runs

**Why it happens:**
- NFS server rebooted
- Network interruption
- Mount became stale (file handle errors)

**How to avoid:**
- Check mount point is accessible before operations
- Use timeout on NFS operations (not built into cp, need wrapper)
- Log clear error for manual intervention
- Don't retry indefinitely -- fail fast and preserve work directory

**Warning signs:**
- "Stale file handle" errors
- Operations hang indefinitely
- df/ls commands hang on mount point

**Safety check:**
```bash
check_nfs_available() {
  local nfs_mount="$1"

  # Quick check: can we stat the mount point?
  if ! timeout 5 stat "$nfs_mount" >/dev/null 2>&1; then
    log_error "NFS mount unavailable or stale: $nfs_mount"
    return 1
  fi

  # Try to create a test file (write test)
  local test_file="$nfs_mount/.pipeline-test-$$"
  if ! timeout 5 touch "$test_file" 2>/dev/null; then
    log_error "NFS mount not writable: $nfs_mount"
    return 1
  fi
  rm -f "$test_file"

  log_debug "NFS mount accessible: $nfs_mount"
  return 0
}

# Use before archive operations
if ! check_nfs_available "/mnt/media/AudioBooks"; then
  die "NFS unavailable -- preserve work directory for retry"
fi
```

### Pitfall 5: Re-Run Idempotency Not Handled

**What goes wrong:** Running stage again after partial completion causes errors

**Why it happens:**
- Files already moved but manifest not updated
- Stage expects source files in original location
- No checks for "already done" state

**How to avoid:**
- Check manifest stage status at start of each stage function
- Use idempotent operations (mkdir -p, test before move)
- Update manifest AFTER each major operation
- Test re-running stages multiple times

**Warning signs:**
- "File not found" errors on re-run
- Duplicate files in destination
- Stage fails but manual inspection shows work completed

**Idempotent pattern:**
```bash
stage_archive() {
  # Early exit if already done
  local status
  status=$(manifest_read "$BOOK_HASH" "stages.archive.status")
  if [[ "$status" == "completed" ]]; then
    log_info "Archive stage already completed"
    return 0
  fi

  # Check if files already moved (partial completion)
  local archive_path
  archive_path=$(manifest_read "$BOOK_HASH" "stages.archive.archive_path")
  if [[ -n "$archive_path" ]] && [[ -d "$archive_path" ]]; then
    log_info "Archive already exists at: $archive_path"
    # Still mark as completed if not already
    manifest_set_stage "$BOOK_HASH" "archive" "completed"
    return 0
  fi

  # Normal archive logic here...
}
```

## Code Examples

Verified patterns from existing codebase and research:

### ffprobe M4B Validation (Before Archive)

```bash
# Source: lib/ffmpeg.sh (existing) + ffprobe documentation
# This is the comprehensive validation before archiving originals

validate_m4b_integrity() {
  local m4b_file="$1"
  local expected_file_count="${2:-}"  # Optional: validate chapter count

  log_info "Validating M4B integrity: $m4b_file"

  # 1. File exists and non-empty
  if [[ ! -s "$m4b_file" ]]; then
    log_error "M4B missing or empty: $m4b_file"
    return 1
  fi

  # 2. ffprobe can parse (container valid)
  if ! ffprobe -v error "$m4b_file" >/dev/null 2>&1; then
    log_error "M4B failed ffprobe parse: $m4b_file"
    return 1
  fi

  # 3. Duration > 0
  local duration
  duration=$(get_duration "$m4b_file")
  if [[ -z "$duration" ]] || (( $(echo "$duration <= 0" | bc -l) )); then
    log_error "M4B has invalid duration: ${duration:-none}"
    return 1
  fi

  # 4. Codec present and is AAC
  local codec
  codec=$(get_codec "$m4b_file")
  if [[ -z "$codec" ]]; then
    log_error "M4B has no audio codec"
    return 1
  fi
  if [[ "$codec" != "aac" ]]; then
    log_warn "Unexpected codec: $codec (expected aac)"
  fi

  # 5. Container format is mov/mp4 family
  local format
  format=$(ffprobe -v error -show_entries format=format_name \
    -of default=noprint_wrappers=1:nokey=1 "$m4b_file")
  if [[ "$format" != *"mov"* ]] && [[ "$format" != *"mp4"* ]]; then
    log_error "Invalid container: $format (expected mov/mp4)"
    return 1
  fi

  # 6. Optional: Verify chapter count matches source file count
  if [[ -n "$expected_file_count" ]] && [[ "$expected_file_count" -gt 1 ]]; then
    local chapter_count
    chapter_count=$(count_chapters "$m4b_file")
    if [[ "$chapter_count" -ne "$expected_file_count" ]]; then
      log_warn "Chapter mismatch: expected=$expected_file_count actual=$chapter_count"
    fi
  fi

  # 7. File size sanity check (bitrate × duration should match file size)
  local bitrate
  bitrate=$(get_bitrate "$m4b_file")
  if [[ -n "$bitrate" ]] && [[ "$bitrate" -gt 0 ]]; then
    local file_size
    file_size=$(stat -f%z "$m4b_file" 2>/dev/null || stat -c%s "$m4b_file")
    local expected_size
    expected_size=$(echo "$duration * $bitrate / 8" | bc)
    local ratio
    ratio=$(echo "scale=2; $file_size / $expected_size" | bc -l)

    # Allow 10% variance for container overhead
    if (( $(echo "$ratio < 0.90 || $ratio > 1.10" | bc -l) )); then
      log_warn "File size mismatch: ratio=$ratio expected=$expected_size actual=$file_size"
    fi
  fi

  log_info "M4B validation passed: duration=${duration}s codec=$codec format=$format"
  return 0
}
```

### NFS-Safe File Copy (Organize Stage)

```bash
# Source: Research on NFS root squash + install command limitations
# This replaces the install command pattern from stages/04-cleanup.sh

copy_to_nfs_safe() {
  local source="$1"
  local dest="$2"
  local mode="${3:-644}"

  # Create parent directory if needed (idempotent)
  local dest_dir
  dest_dir=$(dirname "$dest")
  run mkdir -p "$dest_dir"

  # Copy file (works with root squash, no chown)
  run cp "$source" "$dest"

  # Set permissions (ownership handled by NFS mapping)
  run chmod "$mode" "$dest"

  log_info "Copied to NFS: $dest (mode=$mode)"
}

# Usage in stage 07 (organize)
stage_organize() {
  # ... read metadata, create paths ...

  local m4b_source
  m4b_source=$(manifest_read "$BOOK_HASH" "stages.cleanup.output_path")

  local m4b_dest="$book_dir/${safe_title}.m4b"

  # Idempotent: skip if already exists
  if [[ -f "$m4b_dest" ]]; then
    log_info "Already organized: $m4b_dest"
  else
    copy_to_nfs_safe "$m4b_source" "$m4b_dest" "644"
  fi

  manifest_set_stage "$BOOK_HASH" "organize" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.organize.output_path = \"$m4b_dest\""
}
```

### Archive Stage with Safety Checks

```bash
# Source: Idempotent bash patterns + NFS safety research
# Complete archive stage implementation

stage_archive() {
  log_info "Starting archive of original files"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"

  # Idempotency: check if already done
  local archive_status
  archive_status=$(manifest_read "$BOOK_HASH" "stages.archive.status")
  if [[ "$archive_status" == "completed" ]]; then
    log_info "Archive already completed, skipping"
    return 0
  fi

  # Get organized M4B path from previous stage
  local m4b_path
  m4b_path=$(manifest_read "$BOOK_HASH" "stages.organize.output_path")
  if [[ -z "$m4b_path" ]]; then
    die "No organized M4B found -- run stage 07 first"
  fi

  # CRITICAL: Validate M4B before archiving originals
  local file_count
  file_count=$(manifest_read "$BOOK_HASH" "stages.validate.file_count")

  if [[ "${DRY_RUN:-false}" != "true" ]]; then
    if ! validate_m4b_integrity "$m4b_path" "$file_count"; then
      die "M4B validation failed, refusing to archive originals"
    fi
  fi

  # Set up archive path (preserve original directory structure)
  local archive_base="${ARCHIVE_DIR:-/var/lib/audiobook-pipeline/archive}"
  local source_basename
  source_basename=$(basename "$SOURCE_PATH")
  local archive_path="$archive_base/$source_basename"

  # Check if archive already exists (idempotency)
  if [[ -d "$archive_path" ]]; then
    log_info "Archive already exists: $archive_path"
    manifest_set_stage "$BOOK_HASH" "archive" "completed"
    manifest_update "$BOOK_HASH" \
      ".stages.archive.archive_path = \"$archive_path\""
    return 0
  fi

  # Create archive parent directory
  run mkdir -p "$(dirname "$archive_path")"

  # Move originals to archive (mv works here, same filesystem)
  if [[ -d "$SOURCE_PATH" ]]; then
    run mv "$SOURCE_PATH" "$archive_path"
    log_info "Archived originals: $archive_path"
  else
    log_warn "Source path no longer exists: $SOURCE_PATH (may be already archived)"
  fi

  # Update manifest
  local archived_at
  archived_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  manifest_set_stage "$BOOK_HASH" "archive" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.archive.archive_path = \"$archive_path\"
     | .stages.archive.archived_at = \"$archived_at\""

  log_info "Archive complete: $archive_path"
}
```

### Stage Orchestration Updates

```bash
# Source: bin/audiobook-convert (existing)
# Updates needed for new stages

# BEFORE (current):
STAGE_MAP=(
  [validate]="01"
  [concat]="02"
  [convert]="03"
  [asin]="05"
  [metadata]="06"
  [cleanup]="04"
)
STAGE_ORDER=(validate concat convert asin metadata cleanup)

# AFTER (with organize + archive):
STAGE_MAP=(
  [validate]="01"
  [concat]="02"
  [convert]="03"
  [asin]="05"
  [metadata]="06"
  [organize]="07"
  [archive]="08"
  [cleanup]="09"  # Renumbered from 04
)
STAGE_ORDER=(validate concat convert asin metadata organize archive cleanup)

# Manifest initialization (lib/manifest.sh)
# Add new stages to manifest_create():
stages: {
  validate: { status: "pending" },
  concat:   { status: "pending" },
  convert:  { status: "pending" },
  asin:     { status: "pending" },
  metadata: { status: "pending" },
  organize: { status: "pending" },  # NEW
  archive:  { status: "pending" },  # NEW
  cleanup:  { status: "pending" }
}

# get_next_stage() update:
for stage in validate concat convert asin metadata organize archive cleanup; do
  # ... existing logic ...
done
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| install command for NFS | cp + chmod | 2020+ (NFS v4 root squash) | Avoids permission errors on NFS mounts |
| Basic ffprobe check | Multi-field validation | 2023+ (better reliability) | Catches truncation, codec mismatches |
| Flat audiobook structure | Author/Title nesting | 2024+ (Plex/Audiobookshelf) | Required for modern audiobook servers |
| Delete originals immediately | Archive with validation | 2025+ (safety) | Recoverable from M4B corruption |
| Single cleanup stage | Separate organize/archive | 2026 (this phase) | Better stage separation, idempotency |

**Deprecated/outdated:**
- **install command on NFS:** Fails with root squash, replaced by cp + chmod
- **Flat output directory:** Plex requires Author/Title structure since v1.30+
- **No pre-archive validation:** Risk of data loss, now standard practice to validate M4B first

## Open Questions

1. **Archive location decision**
   - What we know: Currently SOURCE_PATH could be on download volume (e.g., /mnt/downloads/)
   - What's unclear: Should ARCHIVE_DIR be configurable or hardcoded? Local disk vs NFS?
   - Recommendation: Add ARCHIVE_DIR to config.env (default: /var/lib/audiobook-pipeline/archive), document that it should be local disk for safety

2. **Cleanup stage renumbering**
   - What we know: Current stage 04-cleanup.sh needs to become 09-cleanup.sh
   - What's unclear: Should old manifests be migrated, or just document version break?
   - Recommendation: Manifest migration not needed (stage names are stable, numbers are internal), document in CHANGELOG

3. **Re-run behavior after organize**
   - What we know: If organize stage completed but archive failed, SOURCE_PATH still exists
   - What's unclear: Should organize stage be re-run, or skip to archive?
   - Recommendation: get_next_stage() already handles this -- each stage checks "completed" status independently

4. **NFS mount staleness detection**
   - What we know: Stale file handles can cause hangs
   - What's unclear: Should we add proactive staleness checks, or just document manual recovery?
   - Recommendation: Add check_nfs_available() helper with timeout, call before organize/archive stages

5. **Archive directory structure**
   - What we know: Flat archive (ARCHIVE_DIR/book-name/) preserves simplicity
   - What's unclear: Should we mirror original source path structure for multiple libraries?
   - Recommendation: Start with flat (ARCHIVE_DIR/basename), add complexity later if needed

## Sources

### Primary (HIGH confidence)

- [ffprobe Documentation](https://ffmpeg.org/ffprobe.html) - Official ffmpeg documentation for validation commands
- [Audiobook Guide - ffprobe usage](https://www.audiobookshelf.org/guides/ffprobe/) - Practical M4B validation examples
- [NFS Root Squash - Microsoft Azure](https://learn.microsoft.com/en-us/azure/storage/files/nfs-root-squash) - Official documentation on root squash behavior
- [Red Hat - NFS Permissions](https://access.redhat.com/solutions/100013) - Unable to change permissions on NFS with root squash
- [install command Linux documentation](https://www.linuxfordevices.com/tutorials/linux/install-command-in-linux) - install command behavior and limitations
- Existing codebase:
  - `/Volumes/ThunderBolt/Development/audiobook-pipeline/stages/04-cleanup.sh` - Current cleanup stage pattern
  - `/Volumes/ThunderBolt/Development/audiobook-pipeline/lib/ffmpeg.sh` - Existing validation functions
  - `/Volumes/ThunderBolt/Development/audiobook-pipeline/lib/manifest.sh` - State tracking patterns

### Secondary (MEDIUM confidence)

- [Plex Audiobook Guide](https://github.com/seanap/Plex-Audiobook-Guide) - Author/Title folder structure requirement
- [How to Write Idempotent Bash Scripts](https://arslan.io/2019/07/03/how-to-write-idempotent-bash-scripts/) - File existence checks and idempotent patterns
- [Docker Idempotent Entrypoints 2026](https://oneuptime.com/blog/post/2026-02-08-how-to-write-idempotent-docker-entrypoint-scripts/view) - Current best practices for idempotent operations
- [Atomic Cross-Filesystem Moves](https://alexwlchan.net/2019/atomic-cross-filesystem-moves-in-python/) - Limitations of mv across filesystems
- [NFS rename not atomic](https://tech.openbsd.narkive.com/TUZVpPJ2/nfs-rename-is-not-atomic) - NFS operation safety concerns
- [rsync vs cp for NFS](https://bobcares.com/blog/how-to-preserve-permissions-in-rsync/) - Permission preservation comparison

### Tertiary (LOW confidence)

- [NFS Troubleshooting - Stale handles](https://diymediaserver.com/post/2026/fix-stale-nfs-file-handles-mergerfs/) - Error handling patterns (needs validation)
- [Audiobookshelf folder structure discussion](https://github.com/advplyr/audiobookshelf/issues/2208) - Community preferences (not official)

## Metadata

**Confidence breakdown:**
- ffprobe validation: HIGH - Official documentation + existing codebase usage + multiple verified sources
- NFS operations: HIGH - Official Red Hat/Microsoft documentation + verified testing patterns
- Archive structure: MEDIUM - Industry standard (Plex) but not technically mandated
- Idempotency patterns: HIGH - Well-established bash patterns + recent 2026 best practices
- Error handling: MEDIUM - Research-backed but implementation needs testing

**Research date:** 2026-02-20
**Valid until:** 2026-03-30 (30 days -- stable domain, slow-moving standards)

**Next steps for planner:**
1. Create PLAN files for stage 07 (organize) and stage 08 (archive)
2. Plan for renumbering stage 04-cleanup.sh to 09-cleanup.sh
3. Plan for bin/audiobook-convert updates (STAGE_MAP, STAGE_ORDER)
4. Plan for lib/manifest.sh updates (new stages in manifest_create)
5. Plan for config.env updates (ARCHIVE_DIR variable)
6. Plan for verification tests (idempotency, NFS failure, validation edge cases)
