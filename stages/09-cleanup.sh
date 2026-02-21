#!/usr/bin/env bash
# stages/09-cleanup.sh -- Clean work directory and mark pipeline complete
# Final stage in the conversion pipeline.
# Note: M4B output is handled by stage 07 (organize), not here.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="cleanup"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/manifest.sh"

stage_cleanup() {
  log_info "Starting cleanup"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"

  # Mark overall pipeline as completed
  local completed_at
  completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  manifest_set_stage "$BOOK_HASH" "cleanup" "completed"
  manifest_update "$BOOK_HASH" \
    ".status = \"completed\"
     | .completed_at = \"$completed_at\""

  # Clean work directory if configured
  if [[ "${CLEANUP_WORK_DIR:-true}" == "true" ]]; then
    log_info "Cleaning work directory: $WORK_DIR"
    run rm -rf "$WORK_DIR"
  else
    log_info "Work directory preserved: $WORK_DIR"
  fi

  log_info "Pipeline cleanup complete"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_cleanup
fi
