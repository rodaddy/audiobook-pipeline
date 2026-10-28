#!/usr/bin/env bash
# bin/readarr-hook.sh -- Readarr OnReleaseImport webhook handler
# Fast-exit pattern: writes JSON trigger file to queue, exits <5s
# Actual processing is handled by bin/queue-processor.sh (separation of concerns)
set -euo pipefail

# Exit immediately on test events (Readarr connectivity check)
[[ "${readarr_eventtype:-}" == "Test" ]] && exit 0

# Validate required env var
if [[ -z "${readarr_addedbookpaths:-}" ]]; then
  echo "ERROR: readarr_addedbookpaths not set (required for Download/Upgrade events)" >&2
  exit 1
fi

# Source config for QUEUE_DIR (resolve relative to script location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config.env"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

QUEUE_DIR="${QUEUE_DIR:-/var/lib/audiobook-pipeline/queue}"
mkdir -p "$QUEUE_DIR"

# Generate unique trigger filename: YYYYMMDD-HHMMSS-{6-char-random}.json
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RANDOM_ID=$(head -c 8 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 6)
TRIGGER_FILE="${QUEUE_DIR}/${TIMESTAMP}-${RANDOM_ID}.json"

# Write JSON payload to queue
cat > "$TRIGGER_FILE" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "event_type": "${readarr_eventtype}",
  "book_paths": "${readarr_addedbookpaths}",
  "book_id": "${readarr_book_id:-}",
  "book_title": "${readarr_book_title:-}",
  "author_name": "${readarr_author_name:-}",
  "asin": "${readarr_bookfile_edition_asin:-}"
}
EOF

exit 0
