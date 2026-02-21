#!/usr/bin/env bash
# bin/queue-processor.sh -- Asynchronous trigger file processor
# Processes queued trigger files by invoking bin/audiobook-convert for each book
# Run via cron: */5 * * * * flock -n /var/lock/audiobook-processor.lock /opt/audiobook-pipeline/bin/queue-processor.sh
set -euo pipefail

# Source config (resolve relative to script location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config.env"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

QUEUE_DIR="${QUEUE_DIR:-/var/lib/audiobook-pipeline/queue}"
PROCESSING_DIR="${PROCESSING_DIR:-/var/lib/audiobook-pipeline/processing}"
COMPLETED_DIR="${COMPLETED_DIR:-/var/lib/audiobook-pipeline/completed}"
FAILED_DIR="${FAILED_DIR:-/var/lib/audiobook-pipeline/failed}"
PIPELINE_BIN="${PIPELINE_BIN:-${SCRIPT_DIR}/audiobook-convert}"

mkdir -p "$QUEUE_DIR" "$PROCESSING_DIR" "$COMPLETED_DIR" "$FAILED_DIR"

# Process each trigger file in queue
for trigger_file in "$QUEUE_DIR"/*.json; do
  [[ ! -f "$trigger_file" ]] && continue

  base_name=$(basename "$trigger_file")
  lock_file="$PROCESSING_DIR/${base_name}.lock"

  # Atomic claim: mv prevents concurrent processing by multiple instances
  if ! mv "$trigger_file" "$lock_file" 2>/dev/null; then
    continue  # Another processor claimed it
  fi

  # Parse trigger file
  book_paths=$(jq -r '.book_paths' "$lock_file")

  # Track overall success for this trigger file
  all_succeeded=true

  # Handle pipe-separated paths (Readarr can send multiple books)
  IFS='|' read -ra PATHS <<< "$book_paths"

  for book_path in "${PATHS[@]}"; do
    # Skip if directory doesn't exist
    if [[ ! -d "$book_path" ]]; then
      echo "WARN: Book path does not exist, skipping: $book_path" >&2
      all_succeeded=false
      continue
    fi

    # Invoke pipeline (blocking call)
    if "$PIPELINE_BIN" "$book_path"; then
      echo "INFO: Pipeline succeeded for: $book_path" >&2
    else
      echo "ERROR: Pipeline failed for: $book_path (exit=$?)" >&2
      all_succeeded=false
    fi
  done

  # Move trigger file based on result
  if [[ "$all_succeeded" == "true" ]]; then
    mv "$lock_file" "$COMPLETED_DIR/${base_name}"
  else
    mv "$lock_file" "$FAILED_DIR/${base_name}"
  fi
done
