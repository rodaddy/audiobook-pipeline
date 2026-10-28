#!/usr/bin/env bash
# stages/05-asin.sh -- Discover ASIN for the audiobook via priority chain
# Stores discovered ASIN in manifest for downstream metadata stages.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGE="asin"

source "$SCRIPT_DIR/lib/core.sh"
source "$SCRIPT_DIR/lib/manifest.sh"
source "$SCRIPT_DIR/lib/audible.sh"
source "$SCRIPT_DIR/lib/asin.sh"

stage_asin() {
  log_info "Starting ASIN discovery"

  # Check required env vars
  : "${WORK_DIR:?WORK_DIR not set}"
  : "${SOURCE_PATH:?SOURCE_PATH not set}"
  : "${BOOK_HASH:?BOOK_HASH not set}"

  local asin=""
  asin=$(discover_asin "$SOURCE_PATH") || true

  if [[ -n "$asin" ]]; then
    # ASIN_SOURCE is set by discover_asin
    local source="${ASIN_SOURCE:-unknown}"

    # Store in manifest
    manifest_update "$BOOK_HASH" \
      ".metadata.asin = \"$asin\" | .metadata.asin_source = \"$source\""

    # Write to work dir for downstream stages that don't read manifests
    echo "$asin" > "$WORK_DIR/asin.txt"

    manifest_set_stage "$BOOK_HASH" "asin" "completed"
    log_info "ASIN discovery complete: $asin (source: $source)"
  else
    # Graceful degradation -- missing ASIN is not a failure
    manifest_update "$BOOK_HASH" \
      '.metadata.asin = null | .metadata.asin_source = "none"'
    manifest_set_stage "$BOOK_HASH" "asin" "completed"
    log_info "No ASIN found -- metadata enrichment will be skipped in downstream stages"
  fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  stage_asin
fi
