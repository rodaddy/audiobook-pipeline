#!/usr/bin/env bash
# lib/core.sh -- Structured logging, die(), run(), require_cmd()
# Sourced by bin/audiobook-convert; do not execute directly.

set -euo pipefail

# Log level constants
LOG_LEVEL_DEBUG=0
LOG_LEVEL_INFO=1
LOG_LEVEL_WARN=2
LOG_LEVEL_ERROR=3

# Set current log level from config (default: INFO)
case "${LOG_LEVEL:-INFO}" in
  DEBUG) CURRENT_LOG_LEVEL=$LOG_LEVEL_DEBUG ;;
  INFO)  CURRENT_LOG_LEVEL=$LOG_LEVEL_INFO ;;
  WARN)  CURRENT_LOG_LEVEL=$LOG_LEVEL_WARN ;;
  ERROR) CURRENT_LOG_LEVEL=$LOG_LEVEL_ERROR ;;
  *)     CURRENT_LOG_LEVEL=$LOG_LEVEL_INFO ;;
esac

# Ensure log directory exists
LOG_DIR="${LOG_DIR:-/var/log/audiobook-pipeline}"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG_FILE="${LOG_DIR}/convert.log"

# Core log function -- structured key=value output
log() {
  local level="$1"
  shift
  local message="$*"

  local level_num
  case "$level" in
    DEBUG) level_num=$LOG_LEVEL_DEBUG ;;
    INFO)  level_num=$LOG_LEVEL_INFO ;;
    WARN)  level_num=$LOG_LEVEL_WARN ;;
    ERROR) level_num=$LOG_LEVEL_ERROR ;;
    *)     level_num=$LOG_LEVEL_INFO ;;
  esac

  [[ $level_num -lt $CURRENT_LOG_LEVEL ]] && return 0

  local timestamp
  timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  local log_line="timestamp=$timestamp level=$level"
  [[ -n "${STAGE:-}" ]] && log_line="$log_line stage=$STAGE"
  [[ -n "${BOOK_HASH:-}" ]] && log_line="$log_line book_hash=$BOOK_HASH"

  local escaped_msg="${message//\"/\\\"}"
  log_line="$log_line message=\"$escaped_msg\""

  # Write to stderr (terminal) and append to log file
  echo "$log_line" >&2
  echo "$log_line" >> "$LOG_FILE" 2>/dev/null || true
}

# Convenience functions
log_debug() { log DEBUG "$@"; }
log_info()  { log INFO "$@"; }
log_warn()  { log WARN "$@"; }
log_error() { log ERROR "$@"; }

# Log error and exit
die() {
  log_error "$@"
  exit 1
}

# Dry-run aware command runner
run() {
  if [[ "${DRY_RUN:-false}" == "true" ]]; then
    log_info "[DRY-RUN] Would execute: $*"
    return 0
  fi

  log_debug "Executing: $*"
  "$@"
  local exit_code=$?

  if [[ $exit_code -ne 0 ]]; then
    log_error "Command failed (exit=$exit_code): $*"
    return $exit_code
  fi

  return 0
}

# Check that a required command exists
require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    die "Required command not found: $cmd -- install it and retry"
  fi
}
