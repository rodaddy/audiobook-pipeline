#!/usr/bin/env bash
# stages/08-archive.sh -- Validate M4B integrity then archive original MP3 files
# Safety gate: originals only moved after M4B passes 6-point validation.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="archive"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/ffmpeg.sh"
source "$SCRIPT_DIR/lib/manifest.sh"
source "$SCRIPT_DIR/lib/archive.sh"

stage_archive() {
  log_info "Starting archive stage (validation gate)"

  # Check required env vars
  : "${BOOK_HASH:?BOOK_HASH not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"
  : "${ARCHIVE_DIR:?ARCHIVE_DIR not set}"

  # Idempotency check
  local stage_status
  stage_status=$(manifest_read "$BOOK_HASH" "stages.archive.status")
  if [[ "$stage_status" == "completed" ]]; then
    log_info "Archive stage already completed, skipping"
    return 0
  fi

  # Get organized M4B path from manifest (set by stage 07)
  local m4b_path
  m4b_path=$(manifest_read "$BOOK_HASH" "stages.organize.output_path")
  if [[ -z "$m4b_path" ]]; then
    die "No output_path found in manifest stages.organize -- run organize stage first"
  fi

  # Validate M4B integrity -- this is the safety gate
  # If validation fails, originals are preserved (we die before archiving)
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    log_info "[DRY-RUN] Would validate M4B integrity: $m4b_path"
  else
    if ! validate_m4b_integrity "$m4b_path"; then
      die "M4B validation failed -- originals preserved in $SOURCE_PATH"
    fi
  fi

  # Archive original MP3 files
  local book_basename
  book_basename=$(basename "$SOURCE_PATH")
  local archive_path
  archive_path=$(archive_originals "$SOURCE_PATH" "$ARCHIVE_DIR" "$book_basename")

  if [[ -z "$archive_path" ]]; then
    die "archive_originals returned empty path"
  fi

  # Count archived files for manifest
  local original_count
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    original_count=$(find "$SOURCE_PATH" -type f -iname "*.mp3" | wc -l | tr -d ' ')
  else
    # After archive, count files in archive dir
    original_count=$(find "$archive_path" -type f -iname "*.mp3" 2>/dev/null | wc -l | tr -d ' ')
  fi

  # Update manifest
  local archived_at
  archived_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  manifest_set_stage "$BOOK_HASH" "archive" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.archive.archive_path = \"$archive_path\"
     | .stages.archive.archived_at = \"$archived_at\"
     | .stages.archive.original_count = $original_count"

  log_info "Archive complete: $original_count files -> $archive_path"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_archive
fi
