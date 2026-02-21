#!/usr/bin/env bash
# stages/06-metadata.sh -- Enrich M4B with metadata, cover art, and chapters
# Primary source: Audible catalog API. Fallback: Audnexus API.
# Sources ASIN from manifest (set by stage 05), fetches metadata,
# applies tags via tone CLI, and generates companion files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="metadata"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/ffmpeg.sh"
source "$SCRIPT_DIR/lib/manifest.sh"
source "$SCRIPT_DIR/lib/audnexus.sh"
source "$SCRIPT_DIR/lib/audible.sh"
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

  # Determine metadata source priority
  local metadata_source="${METADATA_SOURCE:-audible}"
  local book_json=""
  local actual_source=""

  if [[ "$metadata_source" == "audible" ]]; then
    # Primary: Audible API
    _fetch_audible_metadata "$asin" && actual_source="audible"

    # Fallback: Audnexus
    if [[ -z "$book_json" ]]; then
      log_info "Falling back to Audnexus API"
      _fetch_audnexus_metadata "$asin" && actual_source="audnexus"
    fi
  else
    # Primary: Audnexus (user preference)
    _fetch_audnexus_metadata "$asin" && actual_source="audnexus"

    # Fallback: Audible API
    if [[ -z "$book_json" ]]; then
      log_info "Falling back to Audible API"
      _fetch_audible_metadata "$asin" && actual_source="audible"
    fi
  fi

  if [[ -z "$book_json" ]]; then
    log_warn "No metadata available from any source -- skipping enrichment"
    manifest_set_stage "$BOOK_HASH" "metadata" "completed"
    manifest_update "$BOOK_HASH" '.stages.metadata.enriched = false | .stages.metadata.skip_reason = "api_unavailable"'
    return 0
  fi

  log_info "Using metadata from: $actual_source"

  # Download cover art (non-fatal on failure)
  if [[ "$actual_source" == "audible" ]]; then
    if ! download_audible_cover "$book_json" "$WORK_DIR/cover.jpg"; then
      log_warn "Audible cover art download failed -- trying Audnexus fallback"
      download_cover_art "$book_json" "$WORK_DIR/cover.jpg" || \
        log_warn "Cover art download failed -- continuing without cover"
    fi
  else
    if ! download_cover_art "$book_json" "$WORK_DIR/cover.jpg"; then
      log_warn "Cover art download failed -- continuing without cover"
    fi
  fi

  # Process chapters (non-fatal on failure)
  # Chapters are always at audnexus_chapters_{ASIN}.json regardless of source.
  # _fetch_audible_metadata() pre-caches Audible chapters there.
  # If not present, try fetching from Audnexus as fallback.
  local chapters_file=""
  local chapters_json=""
  local chapters_cache="$WORK_DIR/audnexus_chapters_${asin}.json"

  if [[ -f "$chapters_cache" ]]; then
    chapters_json=$(cat "$chapters_cache")
  else
    chapters_json=$(fetch_audnexus_chapters "$asin") || true
  fi

  if [[ -n "$chapters_json" ]]; then
    # Validate duration match before importing chapters
    local runtime_ms
    runtime_ms=$(echo "$chapters_json" | jq -r '.runtimeLengthMs // empty')

    if [[ -n "$runtime_ms" ]]; then
      if validate_chapter_duration "$m4b_file" "$runtime_ms"; then
        # Convert chapters to tone format
        chapters_file=$(convert_chapters_to_tone "$chapters_json" "$WORK_DIR") || true
      else
        log_warn "Chapter duration mismatch -- keeping file-boundary chapters"
      fi
    else
      log_warn "No runtime length in chapters data -- skipping chapter import"
    fi
  else
    log_info "No chapter data available -- using file-boundary chapters"
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
     | .stages.metadata.source = \"$actual_source\"
     | .stages.metadata.chapter_count = $chapter_count
     | .stages.metadata.has_cover = $([ -f "$WORK_DIR/cover.jpg" ] && echo true || echo false)
     | .stages.metadata.has_companions = $([ -f "$WORK_DIR/desc.txt" ] && echo true || echo false)"

  log_info "Metadata enrichment complete for ASIN $asin (source: $actual_source)"
}

# Internal: fetch Audible metadata, normalize to Audnexus shape, cache as audnexus_book_*.json
# This means downstream consumers (organize stage, Plex) see Audnexus-format JSON
# regardless of whether data came from Audible or Audnexus.
# Sets book_json in caller scope. Returns 0 on success.
_fetch_audible_metadata() {
  local asin="$1"
  local raw_json=""
  local audnexus_cache="$WORK_DIR/audnexus_book_${asin}.json"
  local chapters_cache="$WORK_DIR/audnexus_chapters_${asin}.json"

  # If audnexus-format cache already exists (from previous run), check _source field
  if [[ -f "$audnexus_cache" ]]; then
    local cached_source
    cached_source=$(jq -r '._source // "audnexus"' "$audnexus_cache")
    if [[ "$cached_source" == "audible" ]]; then
      book_json=$(cat "$audnexus_cache")
      return 0
    fi
    # Cache exists but from Audnexus -- re-fetch from Audible to get richer data
  fi

  # Check for cached raw Audible JSON
  local raw_cache="$WORK_DIR/audible_book_${asin}.json"
  if [[ -f "$raw_cache" ]]; then
    raw_json=$(cat "$raw_cache")
  else
    raw_json=$(fetch_audible_book "$asin") || return 1
  fi

  if [[ -z "$raw_json" ]]; then
    return 1
  fi

  # Normalize to Audnexus shape and overwrite the audnexus cache file
  book_json=$(normalize_audible_json "$raw_json")
  if [[ -z "$book_json" ]] || ! echo "$book_json" | jq -e '.asin' >/dev/null 2>&1; then
    log_warn "Failed to normalize Audible metadata"
    book_json=""
    return 1
  fi

  echo "$book_json" > "$audnexus_cache"
  log_debug "Cached Audible metadata (Audnexus format) at $audnexus_cache"

  # Also extract and cache chapters in Audnexus format
  local ch_json
  if ch_json=$(extract_audible_chapters "$book_json" 2>/dev/null) && [[ -n "$ch_json" ]]; then
    echo "$ch_json" > "$chapters_cache"
    log_debug "Cached Audible chapters (Audnexus format) at $chapters_cache"
  fi

  return 0
}

# Internal: fetch Audnexus metadata into book_json
# Sets book_json in caller scope. Returns 0 on success.
_fetch_audnexus_metadata() {
  local asin="$1"
  local audnexus_cache="$WORK_DIR/audnexus_book_${asin}.json"

  if [[ -f "$audnexus_cache" ]]; then
    book_json=$(cat "$audnexus_cache")
  else
    book_json=$(fetch_audnexus_book "$asin") || return 1
  fi

  [[ -n "$book_json" ]]
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_metadata
fi
