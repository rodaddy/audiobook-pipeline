#!/usr/bin/env bash
# lib/manifest.sh -- JSON manifest state management via jq
# Sourced by bin/audiobook-convert; do not execute directly.
# Requires: lib/core.sh sourced first (for log_info, log_error, die)
# Requires: jq

# Ensure manifest directory exists
MANIFEST_DIR="${MANIFEST_DIR:-/var/lib/audiobook-pipeline/manifests}"
mkdir -p "$MANIFEST_DIR" 2>/dev/null || true

# Get full path to a book's manifest file
manifest_path() {
  local book_hash="$1"
  echo "$MANIFEST_DIR/$book_hash.json"
}

# Create a new manifest for a book
# Args: BOOK_HASH SOURCE_PATH
manifest_create() {
  local book_hash="$1"
  local source_path="$2"
  local manifest
  manifest=$(manifest_path "$book_hash")

  local created_at
  created_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  local json
  json=$(jq -n \
    --arg hash "$book_hash" \
    --arg source "$source_path" \
    --arg created "$created_at" \
    '{
      book_hash: $hash,
      source_path: $source,
      created_at: $created,
      status: "pending",
      stages: {
        validate: { status: "pending" },
        concat:   { status: "pending" },
        convert:  { status: "pending" },
        asin:     { status: "pending" },
        cleanup:  { status: "pending" }
      },
      metadata: {}
    }')

  # Atomic write: temp file + mv
  local tmpfile="$manifest.tmp.$$"
  echo "$json" > "$tmpfile"
  mv "$tmpfile" "$manifest"

  log_info "Manifest created for $book_hash"
}

# Read a field from a manifest
# Args: BOOK_HASH FIELD (jq path without leading dot)
# Returns empty string if file missing
manifest_read() {
  local book_hash="$1"
  local field="$2"
  local manifest
  manifest=$(manifest_path "$book_hash")

  if [[ ! -f "$manifest" ]]; then
    echo ""
    return 0
  fi

  jq -r ".${field} // empty" "$manifest"
}

# Update a manifest by applying a jq filter
# Args: BOOK_HASH JQ_FILTER
manifest_update() {
  local book_hash="$1"
  local jq_filter="$2"
  local manifest
  manifest=$(manifest_path "$book_hash")

  if [[ ! -f "$manifest" ]]; then
    log_error "Manifest not found for $book_hash"
    return 1
  fi

  # Atomic write: temp file + mv
  local tmpfile="$manifest.tmp.$$"
  jq "$jq_filter" "$manifest" > "$tmpfile"
  mv "$tmpfile" "$manifest"

  log_debug "Manifest updated for $book_hash: $jq_filter"
}

# Set a stage's status (and completed_at if status is "completed")
# Args: BOOK_HASH STAGE STATUS
manifest_set_stage() {
  local book_hash="$1"
  local stage="$2"
  local status="$3"

  if [[ "$status" == "completed" ]]; then
    local completed_at
    completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    manifest_update "$book_hash" \
      ".stages.${stage}.status = \"${status}\" | .stages.${stage}.completed_at = \"${completed_at}\""
  else
    manifest_update "$book_hash" \
      ".stages.${stage}.status = \"${status}\""
  fi

  log_info "Stage $stage -> $status for $book_hash"
}

# Check book processing status
# Returns: "new" if no manifest, otherwise the status field value
# Exit code: 1 if status is "completed" (signal to skip), 0 otherwise
check_book_status() {
  local book_hash="$1"
  local manifest
  manifest=$(manifest_path "$book_hash")

  if [[ ! -f "$manifest" ]]; then
    echo "new"
    return 0
  fi

  local status
  status=$(jq -r '.status' "$manifest")
  echo "$status"

  [[ "$status" == "completed" ]] && return 1
  return 0
}

# Find the next stage that hasn't been completed
# Returns stage name, or "done" if all completed
get_next_stage() {
  local book_hash="$1"
  local manifest
  manifest=$(manifest_path "$book_hash")

  for stage in validate concat convert asin cleanup; do
    local stage_status
    stage_status=$(jq -r ".stages.${stage}.status // \"pending\"" "$manifest")
    if [[ "$stage_status" != "completed" ]]; then
      echo "$stage"
      return
    fi
  done

  echo "done"
}
