#!/usr/bin/env bash
# lib/error-recovery.sh -- Failed book handling and notifications
# Sourced by bin/audiobook-convert; do not execute directly.
# Requires: lib/core.sh, lib/manifest.sh sourced first
set -euo pipefail

# Move failed book to failed/ directory with error context
# Args: BOOK_HASH SOURCE_PATH
move_to_failed() {
  local book_hash="$1"
  local source_path="$2"
  local failed_dir="${FAILED_DIR:-/var/lib/audiobook-pipeline/failed}"

  mkdir -p "$failed_dir"

  local book_name
  book_name=$(basename "$source_path")
  local failed_path="$failed_dir/$book_name"

  # Avoid clobbering if name collision
  local counter=1
  while [[ -e "$failed_path" ]]; do
    failed_path="$failed_dir/${book_name}.${counter}"
    counter=$((counter + 1))
  done

  log_error "Moving failed book to: $failed_path"

  if [[ "${DRY_RUN:-false}" != "true" ]]; then
    mv "$source_path" "$failed_path"

    # Copy manifest for debugging context
    local manifest
    manifest=$(manifest_path "$book_hash")
    if [[ -f "$manifest" ]]; then
      cp "$manifest" "$failed_path/pipeline-manifest.json"
    fi

    # Write human-readable error summary
    local retry_count
    retry_count=$(manifest_read "$book_hash" "retry_count" || echo "0")
    local error_stage
    error_stage=$(manifest_read "$book_hash" "last_error.stage" || echo "unknown")
    local error_timestamp
    error_timestamp=$(manifest_read "$book_hash" "last_error.timestamp" || echo "unknown")
    local error_exit_code
    error_exit_code=$(manifest_read "$book_hash" "last_error.exit_code" || echo "unknown")
    local error_category
    error_category=$(manifest_read "$book_hash" "last_error.category" || echo "unknown")
    local error_message
    error_message=$(manifest_read "$book_hash" "last_error.message" || echo "unknown")

    cat > "$failed_path/ERROR.txt" <<EOF
Pipeline failed after $retry_count attempts.

Last error:
  Stage: $error_stage
  Time: $error_timestamp
  Exit code: $error_exit_code
  Category: $error_category
  Message: $error_message

Work directory: ${WORK_DIR:-unknown}
Manifest: pipeline-manifest.json
EOF
  fi

  log_info "Failed book moved to $failed_path"
}

# Send webhook notification for permanent or retry-exhausted failures
# Args: BOOK_HASH STAGE MESSAGE
send_failure_notification() {
  local book_hash="$1"
  local stage="$2"
  local message="$3"

  local webhook_url="${FAILURE_WEBHOOK_URL:-}"
  [[ -z "$webhook_url" ]] && return 0  # Skip if not configured

  local book_name
  book_name=$(manifest_read "$book_hash" "source_path" | xargs basename)

  local payload
  payload=$(jq -n \
    --arg text "Audiobook pipeline failure: $book_name" \
    --arg stage "$stage" \
    --arg msg "$message" \
    --arg hash "$book_hash" \
    '{
      text: $text,
      fields: [
        { title: "Book", value: $hash, short: true },
        { title: "Stage", value: $stage, short: true },
        { title: "Error", value: $msg }
      ]
    }')

  # Non-blocking: short timeout, ignore failures
  curl -s -m 5 -X POST -H 'Content-Type: application/json' \
    --data "$payload" "$webhook_url" >/dev/null 2>&1 || true

  log_debug "Failure notification sent to webhook"
}
