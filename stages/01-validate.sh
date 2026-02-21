#!/usr/bin/env bash
# stages/01-validate.sh -- Validate input directory contains processable MP3 files
# Detects bitrate, calculates total duration, writes sorted file list to work dir.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="validate"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/ffmpeg.sh"
source "$SCRIPT_DIR/lib/manifest.sh"

stage_validate() {
  log_info "Starting input validation"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"

  # Verify source directory exists
  if [[ ! -d "$SOURCE_PATH" ]]; then
    die "Source path is not a directory: $SOURCE_PATH"
  fi

  # Find all MP3 files with natural sort
  local mp3_files
  mp3_files=$(find "$SOURCE_PATH" -type f -iname "*.mp3" | sort -V)

  if [[ -z "$mp3_files" ]]; then
    die "No MP3 files found in $SOURCE_PATH"
  fi

  local FILE_COUNT
  FILE_COUNT=$(echo "$mp3_files" | wc -l | tr -d ' ')
  log_info "Found $FILE_COUNT MP3 file(s) in $SOURCE_PATH"

  # Validate each MP3
  while IFS= read -r mp3; do
    if ! validate_audio_file "$mp3"; then
      die "Invalid audio file: $mp3"
    fi
  done <<< "$mp3_files"

  # Detect source bitrate from first file
  local first_mp3
  first_mp3=$(echo "$mp3_files" | head -n 1)
  local bitrate_bps
  bitrate_bps=$(get_bitrate "$first_mp3")
  local bitrate_kbps=$((bitrate_bps / 1000))
  log_info "Source bitrate: ${bitrate_kbps}kbps"

  # Calculate total duration across all MP3s
  local TOTAL_DURATION="0"
  while IFS= read -r mp3; do
    local dur
    dur=$(get_duration "$mp3")
    TOTAL_DURATION=$(echo "$TOTAL_DURATION + $dur" | bc)
  done <<< "$mp3_files"
  log_info "Total duration: ${TOTAL_DURATION}s"

  # Determine target bitrate -- don't upscale low-bitrate sources
  local TARGET_BITRATE
  if [[ "$bitrate_kbps" -le 64 ]]; then
    TARGET_BITRATE="${bitrate_kbps}k"
    log_warn "Low-bitrate source (${bitrate_kbps}kbps) -- using source rate instead of 64k"
  else
    TARGET_BITRATE="64k"
  fi

  # Write sorted file list for downstream stages
  mkdir -p "$WORK_DIR"
  echo "$mp3_files" > "$WORK_DIR/mp3_files.txt"
  log_info "File list written to $WORK_DIR/mp3_files.txt"

  # Update manifest
  manifest_set_stage "$BOOK_HASH" "validate" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.validate.file_count = $FILE_COUNT
     | .stages.validate.total_duration_sec = $TOTAL_DURATION
     | .stages.validate.source_bitrate_kbps = $bitrate_kbps
     | .stages.validate.target_bitrate = \"$TARGET_BITRATE\""

  # Export for downstream stages
  export TARGET_BITRATE
  export FILE_COUNT

  log_info "Validation complete: $FILE_COUNT files, ${TOTAL_DURATION}s total, target=${TARGET_BITRATE}"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_validate
fi
