#!/usr/bin/env bash
# stages/02-concat.sh -- Generate ffmpeg concat file list and FFMETADATA1 chapter file
# Reads mp3_files.txt from stage 01, produces files.txt + metadata.txt for stage 03.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="concat"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/ffmpeg.sh"
source "$SCRIPT_DIR/lib/manifest.sh"
source "$SCRIPT_DIR/lib/sanitize.sh"

stage_concat() {
  log_info "Starting concat preparation"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"

  local mp3_list="$WORK_DIR/mp3_files.txt"
  if [[ ! -f "$mp3_list" ]]; then
    die "mp3_files.txt not found -- run stage 01 first"
  fi

  local FILE_COUNT
  FILE_COUNT=$(wc -l < "$mp3_list" | tr -d ' ')

  # Generate ffmpeg concat demuxer file list
  # Escape apostrophes with triple-apostrophe per ffmpeg concat format
  local concat_file="$WORK_DIR/files.txt"
  : > "$concat_file"
  while IFS= read -r mp3; do
    local escaped
    escaped=$(echo "$mp3" | sed "s/'/'''/g")
    echo "file '$escaped'" >> "$concat_file"
  done < "$mp3_list"
  log_info "Concat file list written to $concat_file ($FILE_COUNT entries)"

  # Derive book title from source directory name
  local BOOK_TITLE
  BOOK_TITLE=$(sanitize_chapter_title "$(basename "$SOURCE_PATH")")

  # Generate FFMETADATA1 header
  local metadata_file="$WORK_DIR/metadata.txt"
  {
    echo ";FFMETADATA1"
    echo "title=$BOOK_TITLE"
    echo "artist=Unknown"
    echo "album=$BOOK_TITLE"
  } > "$metadata_file"

  # Single-file books get no chapter markers
  if [[ "$FILE_COUNT" -eq 1 ]]; then
    log_info "Single-file input -- skipping chapter generation"
    manifest_set_stage "$BOOK_HASH" "concat" "completed"
    manifest_update "$BOOK_HASH" ".stages.concat.chapter_count = 0"
    return 0
  fi

  # Multi-file: generate chapter entries from cumulative durations
  local chapter_start=0
  local counter=0

  while IFS= read -r mp3; do
    local duration_s
    duration_s=$(get_duration "$mp3")

    # Convert float seconds to integer milliseconds
    local duration_ms
    duration_ms=$(echo "$duration_s * 1000" | bc | cut -d. -f1)

    local chapter_end=$((chapter_start + duration_ms))

    # Chapter title from filename without extension
    local base_name
    base_name=$(basename "$mp3" .mp3)
    local chapter_title
    chapter_title=$(sanitize_chapter_title "$base_name")

    {
      echo ""
      echo "[CHAPTER]"
      echo "TIMEBASE=1/1000"
      echo "START=$chapter_start"
      echo "END=$chapter_end"
      echo "title=$chapter_title"
    } >> "$metadata_file"

    chapter_start=$chapter_end
    counter=$((counter + 1))
  done < "$mp3_list"

  log_info "Generated $counter chapter entries in $metadata_file"

  # Update manifest
  manifest_set_stage "$BOOK_HASH" "concat" "completed"
  manifest_update "$BOOK_HASH" ".stages.concat.chapter_count = $counter"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_concat
fi
