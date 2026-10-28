#!/usr/bin/env bash
# stages/01-validate.sh -- Validate input directory contains processable audio files
# Detects bitrate, calculates total duration, writes sorted file list to work dir.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="validate"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/ffmpeg.sh"
source "$SCRIPT_DIR/lib/manifest.sh"
source "$SCRIPT_DIR/lib/concurrency.sh"

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

  # Pre-flight disk space check (NFR-04)
  if ! check_disk_space "$SOURCE_PATH" "$WORK_DIR"; then
    die "Insufficient disk space to process $SOURCE_PATH (need 3x source size)"
  fi

  # Find all audio files with natural sort
  local audio_files
  audio_files=$(find "$SOURCE_PATH" -type f \( -iname "*.mp3" -o -iname "*.flac" -o -iname "*.ogg" -o -iname "*.m4a" -o -iname "*.wma" \) | sort -V)

  if [[ -z "$audio_files" ]]; then
    die "No audio files found in $SOURCE_PATH"
  fi

  local FILE_COUNT
  FILE_COUNT=$(echo "$audio_files" | wc -l | tr -d ' ')
  log_info "Found $FILE_COUNT audio file(s) in $SOURCE_PATH"

  # Validate each audio file
  while IFS= read -r audio_file; do
    if ! validate_audio_file "$audio_file"; then
      die "Invalid audio file: $audio_file"
    fi
  done <<< "$audio_files"

  # Detect source bitrate from first file
  local first_file
  first_file=$(echo "$audio_files" | head -n 1)
  local bitrate_bps
  bitrate_bps=$(get_bitrate "$first_file")
  local bitrate_kbps=$((bitrate_bps / 1000))
  log_info "Source bitrate: ${bitrate_kbps}kbps"

  # Calculate total duration across all audio files
  local TOTAL_DURATION="0"
  while IFS= read -r audio_file; do
    local dur
    dur=$(get_duration "$audio_file")
    TOTAL_DURATION=$(echo "$TOTAL_DURATION + $dur" | bc)
  done <<< "$audio_files"
  log_info "Total duration: ${TOTAL_DURATION}s"

  # Determine target bitrate -- match source up to cap, never upscale
  local max_bitrate="${MAX_BITRATE:-128}"
  local TARGET_BITRATE
  if [[ "$bitrate_kbps" -le "$max_bitrate" ]]; then
    TARGET_BITRATE="${bitrate_kbps}k"
    log_info "Target bitrate: ${bitrate_kbps}k (matching source)"
  else
    TARGET_BITRATE="${max_bitrate}k"
    log_info "Target bitrate: ${max_bitrate}k (capped from ${bitrate_kbps}kbps source)"
  fi

  # Write sorted file list for downstream stages
  mkdir -p "$WORK_DIR"
  echo "$audio_files" > "$WORK_DIR/audio_files.txt"
  log_info "File list written to $WORK_DIR/audio_files.txt"

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
