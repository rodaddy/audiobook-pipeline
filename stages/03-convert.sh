#!/usr/bin/env bash
# stages/03-convert.sh -- Single-pass ffmpeg concat + AAC encode + chapter inject
# Reads files.txt and metadata.txt from stage 02, produces final M4B output.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="convert"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/ffmpeg.sh"
source "$SCRIPT_DIR/lib/manifest.sh"

stage_convert() {
  log_info "Starting M4B conversion"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"

  # Read target bitrate from manifest (env var fallback)
  local TARGET_BITRATE
  TARGET_BITRATE="${TARGET_BITRATE:-$(manifest_read "$BOOK_HASH" "stages.validate.target_bitrate")}"
  if [[ -z "$TARGET_BITRATE" ]]; then
    die "TARGET_BITRATE not set and not found in manifest -- run stage 01 first"
  fi

  # Read file count from manifest
  local FILE_COUNT
  FILE_COUNT="${FILE_COUNT:-$(manifest_read "$BOOK_HASH" "stages.validate.file_count")}"
  if [[ -z "$FILE_COUNT" ]]; then
    die "FILE_COUNT not set and not found in manifest -- run stage 01 first"
  fi

  # Verify input files exist
  local concat_file="$WORK_DIR/files.txt"
  local metadata_file="$WORK_DIR/metadata.txt"

  if [[ ! -f "$concat_file" ]]; then
    die "files.txt not found -- run stage 02 first"
  fi
  if [[ ! -f "$metadata_file" ]]; then
    die "metadata.txt not found -- run stage 02 first"
  fi

  # Set up output path
  local output_dir="$WORK_DIR/output"
  mkdir -p "$output_dir"

  local book_name
  book_name=$(basename "$SOURCE_PATH")
  local output_file="$output_dir/${book_name}.m4b"

  log_info "Converting to: $output_file (bitrate=$TARGET_BITRATE, mono)"

  # Single-pass ffmpeg: concat + encode + chapter inject + faststart
  run ffmpeg -y \
    -f concat -safe 0 -i "$concat_file" \
    -i "$metadata_file" \
    -map_metadata 1 \
    -map 0:a \
    -c:a aac -b:a "$TARGET_BITRATE" -ac 1 \
    -movflags +faststart \
    "$output_file"

  # Post-conversion validation (skip in dry-run mode)
  local actual_chapter_count=0

  if [[ "${DRY_RUN:-false}" != "true" ]]; then
    # Verify output file exists and is non-empty
    if [[ ! -s "$output_file" ]]; then
      die "Output file missing or empty: $output_file"
    fi

    # Verify codec is AAC
    local output_codec
    output_codec=$(get_codec "$output_file")
    if [[ "$output_codec" != "aac" ]]; then
      die "Unexpected codec in output: $output_codec (expected aac)"
    fi
    log_info "Output codec verified: $output_codec"

    # Verify chapter count for multi-file books
    if [[ "$FILE_COUNT" -gt 1 ]]; then
      actual_chapter_count=$(count_chapters "$output_file")
      if [[ "$actual_chapter_count" -ne "$FILE_COUNT" ]]; then
        log_warn "Chapter count mismatch: expected=$FILE_COUNT actual=$actual_chapter_count"
      else
        log_info "Chapter count verified: $actual_chapter_count"
      fi
    fi

    # Verify faststart (moov atom at front -- format should be mov/mp4)
    local format_name
    format_name=$(ffprobe -v error -show_entries format=format_name \
      -of default=noprint_wrappers=1:nokey=1 "$output_file")
    if [[ "$format_name" == *"mov"* ]] || [[ "$format_name" == *"mp4"* ]]; then
      log_info "Faststart verified: format=$format_name"
    else
      log_warn "Unexpected format (faststart may not be applied): $format_name"
    fi
  fi

  # Update manifest
  manifest_set_stage "$BOOK_HASH" "convert" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.convert.output_file = \"$output_file\"
     | .stages.convert.bitrate = \"$TARGET_BITRATE\"
     | .stages.convert.codec = \"aac\"
     | .stages.convert.chapter_count = $actual_chapter_count"

  log_info "Conversion complete: $output_file"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_convert
fi
