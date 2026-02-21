#!/usr/bin/env bash
# stages/04-cleanup.sh -- Validate output M4B, move to output dir, clean work dir
# Final stage in the conversion pipeline.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="cleanup"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/ffmpeg.sh"
source "$SCRIPT_DIR/lib/manifest.sh"
source "$SCRIPT_DIR/lib/sanitize.sh"

stage_cleanup() {
  log_info "Starting cleanup and output finalization"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"
  : "${OUTPUT_DIR:?OUTPUT_DIR not set}"

  # Locate the output M4B from the convert stage manifest entry
  local convert_output
  convert_output=$(manifest_read "$BOOK_HASH" "stages.convert.output_file")
  if [[ -z "$convert_output" ]]; then
    die "No output file recorded in manifest -- run stage 03 first"
  fi

  # In dry-run mode, skip file validation since no file was created
  if [[ "${DRY_RUN:-false}" != "true" ]]; then
    # Verify the M4B exists and has content
    if [[ ! -s "$convert_output" ]]; then
      die "Output M4B missing or empty: $convert_output"
    fi

    # Validate with ffprobe -- confirms it's a valid audio container
    if ! ffprobe -v error "$convert_output" >/dev/null 2>&1; then
      die "Output M4B failed ffprobe validation: $convert_output"
    fi
    log_info "Output M4B validated: $convert_output"
  fi

  # Derive output filename from source directory basename
  local book_basename
  book_basename=$(basename "$SOURCE_PATH")
  local safe_name
  safe_name=$(sanitize_filename "$book_basename")
  local final_filename="${safe_name}.m4b"
  local final_path="${OUTPUT_DIR}/${final_filename}"

  # Create output directory
  run mkdir -p "$OUTPUT_DIR"

  # Move M4B to output directory with correct permissions
  if [[ -n "${FILE_OWNER:-}" && "${DRY_RUN:-false}" != "true" ]]; then
    # Use install for atomic copy + permission setting
    # FILE_OWNER format is "uid:gid" from config
    local owner_user="${FILE_OWNER%%:*}"
    local owner_group="${FILE_OWNER##*:}"
    run install -m "${FILE_MODE:-644}" -o "$owner_user" -g "$owner_group" \
      "$convert_output" "$final_path"
  elif [[ "${DRY_RUN:-false}" == "true" ]]; then
    log_info "[DRY-RUN] Would copy $convert_output -> $final_path"
  else
    # No FILE_OWNER set -- simple copy + chmod
    run cp "$convert_output" "$final_path"
    run chmod "${FILE_MODE:-644}" "$final_path"
  fi

  # Get file size for logging
  local file_size="unknown"
  if [[ "${DRY_RUN:-false}" != "true" && -f "$final_path" ]]; then
    file_size=$(du -h "$final_path" | cut -f1 | tr -d ' ')
  fi

  # Update manifest -- mark cleanup completed with output path
  local completed_at
  completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  manifest_set_stage "$BOOK_HASH" "cleanup" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.cleanup.output_path = \"$final_path\"
     | .stages.cleanup.file_size = \"$file_size\"
     | .status = \"completed\"
     | .completed_at = \"$completed_at\""

  # Clean work directory if configured
  if [[ "${CLEANUP_WORK_DIR:-true}" == "true" ]]; then
    log_info "Cleaning work directory: $WORK_DIR"
    run rm -rf "$WORK_DIR"
  else
    log_info "Work directory preserved: $WORK_DIR"
  fi

  log_info "Output ready: $final_path ($file_size)"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_cleanup
fi
