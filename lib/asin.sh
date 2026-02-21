#!/usr/bin/env bash
# lib/asin.sh -- ASIN discovery via priority chain (.asin file, folder regex, Audible search, Readarr API)
# Sourced by stages/05-asin.sh; do not execute directly.
# Requires: lib/core.sh sourced first (for log_info, log_warn, log_debug, die)
# Requires: lib/audible.sh sourced for search_audible_book()

set -euo pipefail

# Global variable set by discover_asin to indicate source method
ASIN_SOURCE=""

# Check for a manual .asin file in the source directory
# Args: SOURCE_DIR
# Stdout: valid ASIN or nothing
# Returns: 0 if found and valid format, 1 otherwise
check_manual_asin_file() {
  local source_dir="$1"

  # If source_dir is a file, check its parent directory
  if [[ -f "$source_dir" ]]; then
    source_dir=$(dirname "$source_dir")
  fi

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

  # If source_dir is a file, use its parent directory
  if [[ -f "$source_dir" ]]; then
    source_dir=$(dirname "$source_dir")
  fi

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

# Strip series numbering patterns from a string for cleaner search queries
# Patterns: "[05]", "05 ", "#1-", "01-", leading/trailing numbers
# Args: $1 = input string
# Stdout: cleaned string
_strip_series_numbers() {
  local s="$1"
  # Remove bracketed numbers: [05], [1], [12]
  s=$(echo "$s" | sed -E 's/\[[0-9]+\]//g')
  # Remove hash-number prefix: #1-, #05-
  s=$(echo "$s" | sed -E 's/#[0-9]+-//g')
  # Remove leading number sequences: "05 ", "01 - "
  s=$(echo "$s" | sed -E 's/^[0-9]+[[:space:]]*[-–]?[[:space:]]*//')
  # Remove standalone numbers between words (e.g., "Mistborn 05 Shadows")
  s=$(echo "$s" | sed -E 's/[[:space:]][0-9]{1,3}[[:space:]]/ /g')
  # Clean up whitespace
  s=$(echo "$s" | sed -E 's/  +/ /g; s/^ +//; s/ +$//')
  echo "$s"
}

# Search Audible catalog using title/author extracted from path
# Args: SOURCE_PATH (file or directory)
# Stdout: ASIN or nothing
# Returns: 0 if found, 1 otherwise
search_asin_by_title() {
  local source_path="$1"

  if [[ ! -e "$source_path" ]]; then
    log_warn "Source path does not exist: $source_path"
    return 1
  fi

  # Build search query from path components
  local basename_part
  basename_part=$(basename "$source_path")

  # Strip file extension if present
  basename_part="${basename_part%.*}"

  # Strip pipeline hash suffix (e.g., " - a7edd490030561fb")
  basename_part=$(echo "$basename_part" | sed -E 's/ - [a-f0-9]{16}$//')

  # Get parent dir name for author context (strip hash there too)
  local parent_name=""
  local parent_dir
  parent_dir=$(dirname "$source_path")
  if [[ "$parent_dir" != "/" && "$parent_dir" != "." ]]; then
    parent_name=$(basename "$parent_dir")
    parent_name=$(echo "$parent_name" | sed -E 's/ - [a-f0-9]{16}$//')
  fi

  # Walk to grandparent when parent == basename (dedup collapsed the context)
  # e.g., .../Mistborn/Mistborn [05] Shadows Of Self - hash/Mistborn [05] Shadows Of Self.m4b
  # parent="Mistborn [05] Shadows Of Self", basename="Mistborn [05] Shadows Of Self"
  # grandparent="Mistborn" (the series/author context we lost)
  local author_hint=""
  if [[ -n "$parent_name" && "$parent_name" == "$basename_part" ]]; then
    local grandparent_dir
    grandparent_dir=$(dirname "$parent_dir")
    if [[ "$grandparent_dir" != "/" && "$grandparent_dir" != "." ]]; then
      local grandparent_name
      grandparent_name=$(basename "$grandparent_dir")
      grandparent_name=$(echo "$grandparent_name" | sed -E 's/ - [a-f0-9]{16}$//')
      if [[ -n "$grandparent_name" ]]; then
        log_debug "Using grandparent dir for context: $grandparent_name"
        parent_name="$grandparent_name"
      fi
    fi
  fi

  # Extract title hint (basename with series numbers stripped)
  local title_hint
  title_hint=$(_strip_series_numbers "$basename_part")
  # Remove brackets, parens, braces for clean hint
  title_hint=$(echo "$title_hint" | sed -E 's/[][(){}]//g; s/  +/ /g; s/^ +//; s/ +$//')

  # Build query: "Author Title" or just "Title"
  local query=""
  if [[ -n "$parent_name" && "$parent_name" != "$basename_part" ]]; then
    author_hint="$parent_name"
    # Strip series numbers from parent too (might be "Mistborn" which is fine,
    # but could be "Series 03" which isn't)
    local clean_parent
    clean_parent=$(_strip_series_numbers "$parent_name")
    query="$clean_parent $title_hint"
  else
    query="$title_hint"
  fi

  # Clean up query -- remove brackets, parens, braces, excess punctuation
  query=$(echo "$query" | sed -E 's/[][(){}]//g; s/  +/ /g; s/^ +//; s/ +$//')

  if [[ -z "$query" ]]; then
    log_debug "Empty search query from path: $source_path"
    return 1
  fi

  log_info "Searching Audible for: $query"
  if [[ -n "$title_hint" ]]; then
    log_debug "Search hints: title='$title_hint' author='$author_hint'"
  fi

  local asin
  asin=$(search_audible_book "$query" "$title_hint" "$author_hint") || return 1

  if [[ -n "$asin" ]]; then
    echo "$asin"
    return 0
  fi

  return 1
}

# Priority chain orchestrator for ASIN discovery
# Sets ASIN_SOURCE global: "manual", "folder_regex", "audible_search", or "readarr"
# Args: SOURCE_DIR
# Stdout: validated ASIN or nothing
# Returns: 0 if ASIN found, 1 otherwise
discover_asin() {
  local source_dir="$1"
  local asin=""
  local audnexus_unreachable_count=0
  local methods_tried=0

  ASIN_SOURCE=""

  # Verify source exists before the priority chain
  if [[ ! -e "$source_dir" ]]; then
    log_error "Source does not exist: $source_dir"
    return 1
  fi

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

  # Priority 0: CLI override via --asin flag
  if [[ -n "${OVERRIDE_ASIN:-}" ]]; then
    local override
    override=$(echo "$OVERRIDE_ASIN" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')
    if validate_asin_format "$override"; then
      local validate_result=0
      validate_asin_against_audnexus "$override" || validate_result=$?
      if [[ $validate_result -eq 0 ]]; then
        ASIN_SOURCE="cli_override"
        log_info "ASIN from --asin flag: $override"
        echo "$override"
        return 0
      elif [[ $validate_result -eq 2 ]]; then
        # Audnexus unreachable -- accept format-valid override
        ASIN_SOURCE="cli_override"
        log_warn "Audnexus unreachable -- using unvalidated --asin override: $override"
        echo "$override"
        return 0
      else
        log_warn "ASIN from --asin flag failed Audnexus validation: $override"
      fi
    else
      log_warn "Invalid ASIN format from --asin flag: $override"
    fi
  fi

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

  # Priority 4: Audible catalog search by title/author
  methods_tried=$((methods_tried + 1))
  asin=$(search_asin_by_title "$source_dir") || true
  if [[ -n "$asin" ]]; then
    if _try_validate "$asin" "audible_search"; then
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
