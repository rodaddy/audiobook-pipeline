#!/usr/bin/env bash
# bin/cron-scanner.sh -- Cron-triggered fallback scanner for incoming audiobooks
# Detects books missed by Readarr webhook, creates trigger files for queue processor
# Run via cron: */15 * * * * flock -n /var/lock/audiobook-scanner.lock /opt/audiobook-pipeline/bin/cron-scanner.sh
set -euo pipefail

# Source config (resolve relative to script location)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/../config.env"
if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

INCOMING_DIR="${INCOMING_DIR:-/mnt/media/AudioBooks/_incoming}"
QUEUE_DIR="${QUEUE_DIR:-/var/lib/audiobook-pipeline/queue}"
MANIFEST_DIR="${MANIFEST_DIR:-/var/lib/audiobook-pipeline/manifests}"
STABILITY_THRESHOLD="${STABILITY_THRESHOLD:-120}"  # seconds (2 minutes)

mkdir -p "$QUEUE_DIR"

# Generate book hash matching pipeline's generate_book_hash() in lib/sanitize.sh
# Uses: path + sorted MP3 file list -> sha256 -> first 16 chars
compute_book_hash() {
  local book_dir="$1"
  {
    echo "$book_dir"
    find "$book_dir" -type f -name "*.mp3" | sort -V
  } | shasum -a 256 | cut -d' ' -f1 | cut -c1-16
}

# Get newest file mtime in directory (cross-platform)
get_newest_mtime() {
  local dir="$1"
  local newest_epoch=0

  while IFS= read -r file; do
    local mtime
    # macOS stat vs GNU stat
    mtime=$(stat -f%m "$file" 2>/dev/null || stat -c%Y "$file" 2>/dev/null) || continue
    if (( mtime > newest_epoch )); then
      newest_epoch=$mtime
    fi
  done < <(find "$dir" -type f)

  echo "$newest_epoch"
}

# Scan incoming directory for unprocessed books
find "$INCOMING_DIR" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | while IFS= read -r book_dir; do
  # Skip directories with no audio files
  mp3_count=$(find "$book_dir" -type f -iname "*.mp3" | wc -l | tr -d ' ')
  [[ "$mp3_count" -eq 0 ]] && continue

  # Generate hash matching pipeline's method
  book_hash=$(compute_book_hash "$book_dir")

  # Skip if manifest already exists (already processed by pipeline)
  [[ -f "$MANIFEST_DIR/${book_hash}.json" ]] && continue

  # Skip if trigger file already queued for this book
  if grep -rl "\"book_paths\".*$(printf '%s' "$book_dir" | sed 's/[[\.*^$()+?{}|/]/\\&/g')" "$QUEUE_DIR"/*.json 2>/dev/null | head -n1 | grep -q .; then
    continue
  fi

  # Stability check: newest file must be at least STABILITY_THRESHOLD seconds old
  newest_mtime=$(get_newest_mtime "$book_dir")
  [[ "$newest_mtime" -eq 0 ]] && continue

  current_time=$(date +%s)
  age=$((current_time - newest_mtime))

  if [[ $age -lt $STABILITY_THRESHOLD ]]; then
    continue  # Still being downloaded/modified
  fi

  # Stable -- create trigger file
  timestamp=$(date +%Y%m%d-%H%M%S)
  trigger_file="$QUEUE_DIR/${timestamp}-${book_hash}.json"

  cat > "$trigger_file" <<EOF
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "event_type": "CronScan",
  "book_paths": "${book_dir}",
  "source": "cron-scanner"
}
EOF
done
