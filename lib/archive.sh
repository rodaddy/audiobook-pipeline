#!/usr/bin/env bash
# lib/archive.sh -- M4B integrity validation and original file archival
# Sourced by stages/08-archive.sh; do not execute directly.
# Requires: lib/core.sh (logging, die), lib/ffmpeg.sh (get_duration, get_codec)

# Validate M4B integrity with 6-point check
# Returns 0 on success, 1 on any failure
# Args: $1 = path to M4B file
validate_m4b_integrity() {
  local file="$1"
  local checks_passed=0

  log_info "Validating M4B integrity: $file"

  # Check 1: File exists and non-empty
  if [[ ! -s "$file" ]]; then
    log_error "M4B validation FAILED [check 1/6]: file missing or empty: $file"
    return 1
  fi
  checks_passed=$((checks_passed + 1))
  log_debug "M4B check 1/6 passed: file exists and non-empty"

  # Check 2: ffprobe can parse container
  if ! ffprobe -v error "$file" >/dev/null 2>&1; then
    log_error "M4B validation FAILED [check 2/6]: ffprobe cannot parse container"
    return 1
  fi
  checks_passed=$((checks_passed + 1))
  log_debug "M4B check 2/6 passed: ffprobe parses container"

  # Check 3: Duration > 0
  local duration
  duration=$(get_duration "$file" 2>/dev/null || echo "0")
  # Compare as float -- strip any trailing whitespace
  duration=$(echo "$duration" | tr -d '[:space:]')
  if [[ -z "$duration" ]] || [[ "$duration" == "0" ]] || [[ "$duration" == "N/A" ]]; then
    log_error "M4B validation FAILED [check 3/6]: duration is zero or unreadable"
    return 1
  fi
  # Use bc for float comparison
  if ! echo "$duration > 0" | bc -l | grep -q '^1'; then
    log_error "M4B validation FAILED [check 3/6]: duration <= 0 ($duration)"
    return 1
  fi
  checks_passed=$((checks_passed + 1))
  log_debug "M4B check 3/6 passed: duration=$duration seconds"

  # Check 4: Codec is AAC
  local codec
  codec=$(get_codec "$file" 2>/dev/null || echo "")
  if [[ "$codec" != "aac" ]]; then
    log_error "M4B validation FAILED [check 4/6]: codec is '$codec', expected 'aac'"
    return 1
  fi
  checks_passed=$((checks_passed + 1))
  log_debug "M4B check 4/6 passed: codec=aac"

  # Check 5: Container format is mov/mp4 family
  local format_name
  format_name=$(ffprobe -v error -show_entries format=format_name \
    -of default=noprint_wrappers=1:nokey=1 "$file" 2>/dev/null || echo "")
  # M4B containers report as "mov,mp4,m4a,3gp,3g2,mj2" or similar
  if [[ "$format_name" != *"mp4"* && "$format_name" != *"mov"* ]]; then
    log_error "M4B validation FAILED [check 5/6]: format '$format_name' not in mov/mp4 family"
    return 1
  fi
  checks_passed=$((checks_passed + 1))
  log_debug "M4B check 5/6 passed: format=$format_name"

  # Check 6: File size within 10% of expected (bitrate x duration / 8)
  local bitrate
  bitrate=$(ffprobe -v error -show_entries format=bit_rate \
    -of default=noprint_wrappers=1:nokey=1 "$file" 2>/dev/null || echo "0")
  bitrate=$(echo "$bitrate" | tr -d '[:space:]')

  if [[ -n "$bitrate" && "$bitrate" != "0" && "$bitrate" != "N/A" ]]; then
    local expected_size actual_size lower_bound upper_bound
    expected_size=$(echo "$bitrate * $duration / 8" | bc -l | cut -d. -f1)
    actual_size=$(wc -c < "$file" | tr -d '[:space:]')

    if [[ -n "$expected_size" && "$expected_size" -gt 0 ]]; then
      lower_bound=$(echo "$expected_size * 0.9" | bc -l | cut -d. -f1)
      upper_bound=$(echo "$expected_size * 1.1" | bc -l | cut -d. -f1)

      if [[ "$actual_size" -lt "$lower_bound" || "$actual_size" -gt "$upper_bound" ]]; then
        log_error "M4B validation FAILED [check 6/6]: file size $actual_size outside 10% of expected $expected_size"
        return 1
      fi
    else
      log_warn "M4B check 6/6: could not compute expected size, skipping size check"
    fi
  else
    log_warn "M4B check 6/6: bitrate unavailable, skipping size check"
  fi
  checks_passed=$((checks_passed + 1))
  log_debug "M4B check 6/6 passed: file size within expected range"

  log_info "M4B validation passed: all $checks_passed/6 checks OK"
  return 0
}

# Archive original source files to archive directory
# Args: $1 = SOURCE_PATH, $2 = ARCHIVE_BASE_DIR, $3 = BOOK_BASENAME
# Outputs: archive path on stdout
# Returns: 0 on success, 1 on failure
archive_originals() {
  local source_path="$1"
  local archive_base="$2"
  local book_basename="$3"

  local archive_path="$archive_base/$book_basename"

  log_info "Archiving originals: $source_path -> $archive_path"

  # Create archive directory
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    log_info "[DRY-RUN] Would create archive directory: $archive_path"
    log_info "[DRY-RUN] Would move files from: $source_path"
    echo "$archive_path"
    return 0
  fi

  mkdir -p "$archive_path" || {
    log_error "Failed to create archive directory: $archive_path"
    return 1
  }

  # Detect if same filesystem
  # Use stat --version to detect GNU vs BSD stat (not uname -- GNU coreutils
  # on macOS via Homebrew breaks uname-based detection)
  local source_dev archive_dev
  if stat --version >/dev/null 2>&1; then
    # GNU stat
    source_dev=$(stat -c '%d' "$source_path" 2>/dev/null || echo "0")
    archive_dev=$(stat -c '%d' "$archive_path" 2>/dev/null || echo "1")
  else
    # BSD stat (macOS native)
    source_dev=$(stat -f '%d' "$source_path" 2>/dev/null || echo "0")
    archive_dev=$(stat -f '%d' "$archive_path" 2>/dev/null || echo "1")
  fi

  local same_fs=false
  [[ "$source_dev" == "$archive_dev" ]] && same_fs=true

  # Move each file
  local file_count=0
  while IFS= read -r -d '' mp3_file; do
    local filename
    filename=$(basename "$mp3_file")
    local dest="$archive_path/$filename"

    if [[ "$same_fs" == "true" ]]; then
      # Same filesystem -- atomic mv
      mv "$mp3_file" "$dest" || {
        log_error "Failed to move: $mp3_file -> $dest"
        return 1
      }
    else
      # Cross-filesystem -- cp + verify size + rm
      cp "$mp3_file" "$dest" || {
        log_error "Failed to copy: $mp3_file -> $dest"
        return 1
      }
      chmod "${FILE_MODE:-644}" "$dest" 2>/dev/null || true

      # Verify copy matches source size
      local src_size dest_size
      src_size=$(wc -c < "$mp3_file" | tr -d '[:space:]')
      dest_size=$(wc -c < "$dest" | tr -d '[:space:]')
      if [[ "$src_size" != "$dest_size" ]]; then
        log_error "Size mismatch after copy: $mp3_file ($src_size) vs $dest ($dest_size)"
        rm -f "$dest"
        return 1
      fi

      # Safe to remove source
      rm "$mp3_file" || {
        log_error "Failed to remove source after verified copy: $mp3_file"
        return 1
      }
    fi

    file_count=$((file_count + 1))
    log_debug "Archived: $filename"
  done < <(find "$source_path" -type f -iname "*.mp3" -print0)

  if [[ "$file_count" -eq 0 ]]; then
    log_warn "No MP3 files found to archive in $source_path (already archived?)"
  else
    log_info "Archived $file_count MP3 files to $archive_path"
  fi

  echo "$archive_path"
  return 0
}
