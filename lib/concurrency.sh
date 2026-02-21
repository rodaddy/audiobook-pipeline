#!/usr/bin/env bash
# lib/concurrency.sh -- Global singleton lock and disk space pre-flight check
# Sourced by bin/audiobook-convert; do not execute directly.

set -euo pipefail

# Default lock directory
LOCK_DIR="${LOCK_DIR:-/var/lib/audiobook-pipeline/locks}"

# Acquire a global flock-based lock to ensure singleton pipeline execution.
# Uses FD 200 on $LOCK_DIR/pipeline.lock with non-blocking mode.
# On contention: logs informational message and exits 0 (clean exit, not error).
# Lock is automatically released when FD 200 closes (script exit, signal, etc).
acquire_global_lock() {
  # flock is Linux-only; skip locking on systems without it (macOS)
  if ! command -v flock &>/dev/null; then
    log_warn "flock not available -- skipping global lock (non-Linux system)"
    return 0
  fi

  mkdir -p "$LOCK_DIR" 2>/dev/null || true

  local lock_file="$LOCK_DIR/pipeline.lock"

  # Open FD 200 for flock
  exec 200>"$lock_file"

  # Non-blocking lock attempt -- exit 0 on failure (not error)
  if ! flock -n 200; then
    log_info "Another pipeline instance is running. Exiting cleanly."
    exit 0
  fi

  # Lock acquired -- FD 200 stays open until script exits
  return 0
}

# Check that the work directory has enough free space to process the source.
# Requires at least 3x the source size (concat + convert + headroom).
# Args: SOURCE_DIR WORK_DIR
# Returns: 0 if sufficient, 1 if insufficient
check_disk_space() {
  local source_dir="$1"
  local work_dir="$2"

  # Calculate source size in KB
  local source_size_kb
  source_size_kb=$(du -sk "$source_dir" | awk '{print $1}')

  # Required: 3x source size
  local required_kb=$((source_size_kb * 3))

  # Available space on the work directory filesystem
  local available_kb
  available_kb=$(df -k "$work_dir" | awk 'NR==2 {print $4}')

  log_info "Disk space check: source=${source_size_kb}KB required=${required_kb}KB available=${available_kb}KB"

  if [[ "$available_kb" -lt "$required_kb" ]]; then
    log_error "Insufficient disk space: need ${required_kb}KB but only ${available_kb}KB available"
    return 1
  fi

  return 0
}
