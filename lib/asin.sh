#!/usr/bin/env bash
# lib/asin.sh -- ASIN discovery via priority chain (.asin file, folder regex, Readarr API)
# Sourced by stages/05-asin.sh; do not execute directly.
# Requires: lib/core.sh sourced first (for log_info, log_warn, log_debug, die)

set -euo pipefail

# Global variable set by discover_asin to indicate source method
ASIN_SOURCE=""

# Check for a manual .asin file in the source directory
# Args: SOURCE_DIR
# Stdout: valid ASIN or nothing
# Returns: 0 if found and valid format, 1 otherwise
check_manual_asin_file() {
  local source_dir="$1"
  local asin_file="$source_dir/.asin"

  if [[ ! -f "$asin_file" ]]; then
    log_debug "No .asin file found in $source_dir"
    return 1
  fi

  local raw
  raw=$(<"$asin_file")

  # Trim whitespace and uppercase
  local asin
  asin=$(echo "$raw" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')

  if [[ ! "$asin" =~ ^[A-Z0-9]{10}$ ]]; then
    log_warn "Invalid ASIN format in .asin file: $asin"
    return 1
  fi

  log_info "ASIN found in .asin file: $asin"
  echo "$asin"
  return 0
}

# Extract ASIN from folder name using regex patterns
# Args: SOURCE_DIR
# Stdout: valid ASIN or nothing
# Returns: 0 if found, 1 otherwise
extract_asin_from_folder() {
  local source_dir="$1"
  local folder_name
  folder_name=$(basename "$source_dir")

  # Uppercase for matching
  local upper_name
  upper_name=$(echo "$folder_name" | tr '[:lower:]' '[:upper:]')

  # Pattern 1: brackets [B00JCDK5ME]
  if [[ "$upper_name" =~ \[([A-Z0-9]{10})\] ]]; then
    local candidate="${BASH_REMATCH[1]}"
    if [[ "$candidate" =~ ^B0 ]]; then
      log_info "ASIN extracted from folder name (brackets): $candidate"
      echo "$candidate"
      return 0
    fi
  fi

  # Pattern 2: parens (B00JCDK5ME)
  if [[ "$upper_name" =~ \(([A-Z0-9]{10})\) ]]; then
    local candidate="${BASH_REMATCH[1]}"
    if [[ "$candidate" =~ ^B0 ]]; then
      log_info "ASIN extracted from folder name (parens): $candidate"
      echo "$candidate"
      return 0
    fi
  fi

  # Pattern 3: prefix B00JCDK5ME - Book Title
  if [[ "$upper_name" =~ ^([A-Z0-9]{10})[[:space:]]*- ]]; then
    local candidate="${BASH_REMATCH[1]}"
    if [[ "$candidate" =~ ^B0 ]]; then
      log_info "ASIN extracted from folder name (prefix): $candidate"
      echo "$candidate"
      return 0
    fi
  fi

  log_debug "No ASIN found in folder name: $folder_name"
  return 1
}

# Pure format validation for ASIN
# Args: ASIN
# Returns: 0 if valid format, 1 otherwise
validate_asin_format() {
  local asin="$1"
  # Accept both Amazon ASINs (B0...) and Audible ASINs (ISBN-10 format)
  [[ "$asin" =~ ^[A-Z0-9]{10}$ ]]
}

# Validate ASIN against Audnexus API
# Args: ASIN
# Returns: 0 if valid (HTTP 200), 1 otherwise
validate_asin_against_audnexus() {
  local asin="$1"
  local response

  if ! response=$(curl -s -w "\n%{http_code}" --max-time 10 \
    "https://api.audnex.us/books/$asin" 2>/dev/null); then
    log_warn "Audnexus API unreachable -- skipping validation"
    return 2
  fi

  local http_code
  http_code=$(echo "$response" | tail -n 1)

  case "$http_code" in
    200)
      log_debug "ASIN validated against Audnexus: $asin"
      return 0
      ;;
    404)
      log_warn "ASIN not found in Audnexus: $asin"
      return 1
      ;;
    422)
      log_warn "Invalid ASIN format rejected by Audnexus: $asin"
      return 1
      ;;
    *)
      log_error "Audnexus API returned unexpected status $http_code for ASIN: $asin"
      return 1
      ;;
  esac
}

# Stub for future Readarr API integration
# Args: SOURCE_DIR
# Returns: 1 always (not yet implemented)
query_readarr_for_asin() {
  local source_dir="$1"

  if [[ -z "${READARR_API_URL:-}" || -z "${READARR_API_KEY:-}" ]]; then
    log_debug "Readarr API not configured -- skipping"
    return 1
  fi

  log_info "Readarr API lookup not yet implemented -- skipping"
  return 1
}

# Priority chain orchestrator for ASIN discovery
# Sets ASIN_SOURCE global: "manual", "folder_regex", or "readarr"
# Args: SOURCE_DIR
# Stdout: validated ASIN or nothing
# Returns: 0 if ASIN found, 1 otherwise
discover_asin() {
  local source_dir="$1"
  local asin=""
  local audnexus_unreachable_count=0
  local methods_tried=0

  ASIN_SOURCE=""

  # Helper: try to validate an ASIN, handle network errors
  _try_validate() {
    local candidate="$1"
    local source_name="$2"

    if ! validate_asin_format "$candidate"; then
      log_warn "ASIN from $source_name has invalid format: $candidate"
      return 1
    fi

    local validate_result=0
    validate_asin_against_audnexus "$candidate" || validate_result=$?

    if [[ $validate_result -eq 0 ]]; then
      ASIN_SOURCE="$source_name"
      log_info "ASIN discovered via $source_name: $candidate"
      echo "$candidate"
      return 0
    elif [[ $validate_result -eq 2 ]]; then
      # Network error -- track but don't accept yet
      audnexus_unreachable_count=$((audnexus_unreachable_count + 1))
      return 1
    else
      log_warn "ASIN from $source_name failed Audnexus validation: $candidate"
      return 1
    fi
  }

  # Priority 1: manual .asin file
  methods_tried=$((methods_tried + 1))
  asin=$(check_manual_asin_file "$source_dir") || true
  if [[ -n "$asin" ]]; then
    if _try_validate "$asin" "manual"; then
      return 0
    fi
  fi

  # Priority 2: folder name regex
  methods_tried=$((methods_tried + 1))
  asin=$(extract_asin_from_folder "$source_dir") || true
  if [[ -n "$asin" ]]; then
    if _try_validate "$asin" "folder_regex"; then
      return 0
    fi
  fi

  # Priority 3: Readarr API
  methods_tried=$((methods_tried + 1))
  asin=$(query_readarr_for_asin "$source_dir") || true
  if [[ -n "$asin" ]]; then
    if _try_validate "$asin" "readarr"; then
      return 0
    fi
  fi

  # If Audnexus was unreachable for all attempts, accept format-valid ASINs
  if [[ $audnexus_unreachable_count -gt 0 ]]; then
    log_warn "Audnexus unreachable -- attempting to use unvalidated ASIN"

    # Retry methods for format-valid ASINs without API validation
    asin=$(check_manual_asin_file "$source_dir") || true
    if [[ -n "$asin" ]] && validate_asin_format "$asin"; then
      ASIN_SOURCE="manual"
      log_warn "Audnexus unreachable -- using unvalidated ASIN from .asin file: $asin"
      echo "$asin"
      return 0
    fi

    asin=$(extract_asin_from_folder "$source_dir") || true
    if [[ -n "$asin" ]] && validate_asin_format "$asin"; then
      ASIN_SOURCE="folder_regex"
      log_warn "Audnexus unreachable -- using unvalidated ASIN from folder name: $asin"
      echo "$asin"
      return 0
    fi
  fi

  log_warn "No valid ASIN found"
  return 1
}
