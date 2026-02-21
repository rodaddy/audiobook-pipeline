#!/usr/bin/env bash
# stages/07-organize.sh -- Organize tagged M4B into Plex-compatible folder structure
# Deploys M4B and companion files to NFS mount with metadata-driven paths.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="organize"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/manifest.sh"
source "$SCRIPT_DIR/lib/organize.sh"
source "$SCRIPT_DIR/lib/metadata.sh"

stage_organize() {
  log_info "Starting folder organization"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"

  # Use NFS_OUTPUT_DIR if set, otherwise fall back to OUTPUT_DIR
  local output_base="${NFS_OUTPUT_DIR:-$OUTPUT_DIR}"
  if [[ -z "$output_base" ]]; then
    die "NFS_OUTPUT_DIR and OUTPUT_DIR both unset"
  fi

  # Idempotency check
  local stage_status
  stage_status=$(manifest_read "$BOOK_HASH" "stages.organize.status")
  if [[ "$stage_status" == "completed" ]]; then
    log_info "Organize stage already completed, skipping"
    return 0
  fi

  # Locate M4B from convert stage (same as metadata stage does)
  local m4b_file
  m4b_file=$(manifest_read "$BOOK_HASH" "stages.convert.output_file")
  if [[ -z "$m4b_file" ]]; then
    die "No output M4B found in manifest -- run stage 03 first"
  fi

  # Verify M4B exists (skip check in dry-run)
  if [[ "${DRY_RUN:-false}" != "true" && ! -f "$m4b_file" ]]; then
    die "M4B file not found: $m4b_file"
  fi

  # Check NFS availability
  if ! check_nfs_available "$output_base"; then
    die "NFS mount unavailable or stale: $output_base"
  fi

  # Build Plex folder path from metadata
  local output_dir
  output_dir=$(build_plex_path "$output_base" "$WORK_DIR" "$BOOK_HASH" "$SOURCE_PATH")
  log_info "Target folder: $output_dir"

  # Get sanitized filename from M4B basename
  local m4b_basename
  m4b_basename=$(basename "$m4b_file")
  local final_path="$output_dir/$m4b_basename"

  # Copy M4B to final location
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    log_info "[DRY-RUN] Would copy: $m4b_file -> $final_path"
  else
    local file_mode="${FILE_MODE:-644}"
    local dir_mode="${DIR_MODE:-755}"
    if ! copy_to_nfs_safe "$m4b_file" "$final_path" "$file_mode" "$dir_mode"; then
      die "Failed to copy M4B to final location"
    fi
  fi

  # Deploy companion files if enabled
  if [[ "${CREATE_COMPANION_FILES:-true}" == "true" ]]; then
    if [[ "${DRY_RUN:-false}" == "true" ]]; then
      log_info "[DRY-RUN] Would deploy companion files to: $output_dir"
    else
      deploy_companion_files "$WORK_DIR" "$output_dir"
    fi
  else
    log_debug "Companion file deployment disabled"
  fi

  # Update manifest
  manifest_set_stage "$BOOK_HASH" "organize" "completed"
  manifest_update "$BOOK_HASH" ".stages.organize.output_path = \"$final_path\""

  log_info "Organization complete: $final_path"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_organize
fi
