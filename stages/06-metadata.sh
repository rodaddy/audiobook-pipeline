#!/usr/bin/env bash
# stages/06-metadata.sh -- Enrich M4B with Audnexus metadata, cover art, and chapters
# Sources ASIN from manifest (set by stage 05), fetches cached Audnexus data,
# applies tags via tone CLI, and generates companion files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="metadata"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/ffmpeg.sh"
source "$SCRIPT_DIR/lib/manifest.sh"
source "$SCRIPT_DIR/lib/audnexus.sh"
source "$SCRIPT_DIR/lib/metadata.sh"

stage_metadata() {
  log_info "Starting metadata enrichment"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"

  # Check skip config
  if [[ "${METADATA_SKIP:-false}" == "true" ]]; then
    log_info "Metadata enrichment disabled via METADATA_SKIP"
    manifest_set_stage "$BOOK_HASH" "metadata" "completed"
    manifest_update "$BOOK_HASH" '.stages.metadata.enriched = false | .stages.metadata.skip_reason = "disabled"'
    return 0
  fi

  # Locate M4B from convert stage
  local m4b_file
  m4b_file=$(manifest_read "$BOOK_HASH" "stages.convert.output_file")
  if [[ -z "$m4b_file" ]]; then
    die "No output M4B found in manifest -- run stage 03 first"
  fi

  # Verify M4B exists (skip check in dry-run)
  if [[ "${DRY_RUN:-false}" != "true" && ! -f "$m4b_file" ]]; then
    die "M4B file not found: $m4b_file"
  fi

  # Read ASIN from manifest (set by stage 05)
  local asin
  asin=$(manifest_read "$BOOK_HASH" "metadata.asin")
  if [[ -z "$asin" || "$asin" == "null" ]]; then
    log_warn "No ASIN found in manifest -- skipping metadata enrichment"
    manifest_set_stage "$BOOK_HASH" "metadata" "completed"
    manifest_update "$BOOK_HASH" '.stages.metadata.enriched = false | .stages.metadata.skip_reason = "no_asin"'
    return 0
  fi

  # Read cached book JSON (written by stage 05-asin via audnexus.sh)
  local book_cache="$WORK_DIR/audnexus_book_${asin}.json"
  local book_json=""
  if [[ -f "$book_cache" ]]; then
    book_json=$(cat "$book_cache")
  else
    # Try fetching if not cached yet
    log_info "Book metadata not cached -- fetching from Audnexus"
    if ! book_json=$(fetch_audnexus_book "$asin"); then
      log_warn "Failed to fetch book metadata from Audnexus -- skipping enrichment"
      manifest_set_stage "$BOOK_HASH" "metadata" "completed"
      manifest_update "$BOOK_HASH" '.stages.metadata.enriched = false | .stages.metadata.skip_reason = "api_unavailable"'
      return 0
    fi
  fi

  if [[ -z "$book_json" ]]; then
    log_warn "Empty book metadata -- skipping enrichment"
    manifest_set_stage "$BOOK_HASH" "metadata" "completed"
    manifest_update "$BOOK_HASH" '.stages.metadata.enriched = false | .stages.metadata.skip_reason = "empty_metadata"'
    return 0
  fi

  # Download cover art (non-fatal on failure)
  if ! download_cover_art "$book_json" "$WORK_DIR/cover.jpg"; then
    log_warn "Cover art download failed -- continuing without cover"
  fi

  # Process chapters (non-fatal on failure)
  local chapters_file=""
  local chapters_cache="$WORK_DIR/audnexus_chapters_${asin}.json"
  local chapters_json=""

  if [[ -f "$chapters_cache" ]]; then
    chapters_json=$(cat "$chapters_cache")
  else
    # Try fetching if not cached
    chapters_json=$(fetch_audnexus_chapters "$asin") || true
  fi

  if [[ -n "$chapters_json" ]]; then
    # Validate duration match before importing chapters
    local audnexus_runtime_ms
    audnexus_runtime_ms=$(echo "$chapters_json" | jq -r '.runtimeLengthMs // empty')

    if [[ -n "$audnexus_runtime_ms" ]]; then
      if validate_chapter_duration "$m4b_file" "$audnexus_runtime_ms"; then
        # Convert chapters to tone format
        chapters_file=$(convert_chapters_to_tone "$chapters_json" "$WORK_DIR") || true
      else
        log_warn "Chapter duration mismatch -- keeping file-boundary chapters"
      fi
    else
      log_warn "No runtime length in chapters data -- skipping chapter import"
    fi
  else
    log_info "No Audnexus chapters available -- using file-boundary chapters"
  fi

  # Tag M4B -- this is the critical step; failure here is fatal
  if ! tag_m4b "$m4b_file" "$WORK_DIR" "$book_json" "$chapters_file"; then
    log_error "Metadata tagging failed -- M4B may be in inconsistent state"
    manifest_set_stage "$BOOK_HASH" "metadata" "failed"
    return 1
  fi

  # Verify metadata was applied (non-fatal on mismatch)
  local expected_title
  expected_title=$(echo "$book_json" | jq -r '.title // empty')
  if ! verify_metadata "$m4b_file" "$expected_title"; then
    log_warn "Metadata verification found issues -- continuing anyway"
  fi

  # Generate companion files
  local narrator
  narrator=$(echo "$book_json" | jq -r '[.narrators[]?.name // .narrators[]?] | join(", ") // empty')
  generate_companions "$WORK_DIR" "$book_json" "$narrator"

  # Determine chapter count for manifest
  local chapter_count=0
  if [[ -n "$chapters_file" && -f "$chapters_file" ]]; then
    chapter_count=$(wc -l < "$chapters_file" | tr -d ' ')
  fi

  # Update manifest with metadata details
  manifest_set_stage "$BOOK_HASH" "metadata" "completed"
  manifest_update "$BOOK_HASH" \
    ".stages.metadata.enriched = true
     | .stages.metadata.asin = \"$asin\"
     | .stages.metadata.title = \"$expected_title\"
     | .stages.metadata.chapter_count = $chapter_count
     | .stages.metadata.has_cover = $([ -f "$WORK_DIR/cover.jpg" ] && echo true || echo false)
     | .stages.metadata.has_companions = $([ -f "$WORK_DIR/desc.txt" ] && echo true || echo false)"

  log_info "Metadata enrichment complete for ASIN $asin"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_metadata
fi
