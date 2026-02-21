#!/usr/bin/env bash
# lib/organize.sh -- Plex folder structure generation and NFS-safe file operations
# Sourced by stages/07-organize.sh; do not execute directly.
# Requires: lib/core.sh (logging), lib/manifest.sh (metadata reads)

# Sanitize a folder component (author, series, title) -- space-based replacement
# Args: $1 = component string
# Returns: sanitized string truncated to 255 bytes (UTF-8 byte-aware)
sanitize_folder_component() {
  local component="$1"

  # Replace invalid filesystem chars with spaces
  local sanitized
  sanitized=$(echo "$component" | sed -E 's/[\/\\:"*?<>|;]+/ /g')

  # Normalize whitespace and strip leading/trailing dots
  sanitized=$(echo "$sanitized" | sed -E 's/  +/ /g; s/^ +//; s/ +$//; s/^\.+//; s/\.+$//')

  # Truncate to 255 bytes (UTF-8 byte-aware)
  local byte_count
  byte_count=$(printf '%s' "$sanitized" | wc -c | tr -d ' ')

  if [[ "$byte_count" -gt 255 ]]; then
    # Use head -c to truncate by bytes, not characters
    # This may split a multi-byte UTF-8 character at the boundary,
    # so we'll use iconv to validate and truncate safely
    sanitized=$(printf '%s' "$sanitized" | head -c 255 | iconv -f utf-8 -t utf-8 -c)
  fi

  echo "$sanitized"
}

# Build Plex-compatible folder path from metadata
# Args: $1 = base_dir, $2 = work_dir, $3 = book_hash, $4 = source_path
# Returns: full directory path (not including filename)
build_plex_path() {
  local base_dir="$1"
  local work_dir="$2"
  local book_hash="$3"
  local source_path="$4"

  local author="" title="" series_name="" series_position="" year=""

  # Try to find Audnexus-format JSON cache in work_dir
  # (Audible data is normalized to Audnexus shape and saved here too)
  local meta_json=""
  local meta_file
  meta_file=$(find "$work_dir" -maxdepth 1 -name "audnexus_book_*.json" | head -n1)

  if [[ -n "$meta_file" && -f "$meta_file" ]]; then
    meta_json=$(cat "$meta_file")

    author=$(echo "$meta_json" | jq -r '.authors[0].name // empty')
    title=$(echo "$meta_json" | jq -r '.title // empty')
    series_name=$(echo "$meta_json" | jq -r '.seriesPrimary.name // empty')
    series_position=$(echo "$meta_json" | jq -r '.seriesPrimary.position // empty')

    # Extract year from releaseDate (ISO 8601)
    year=$(echo "$meta_json" | jq -r '
      if .releaseDate != null and .releaseDate != "" then
        if (.releaseDate | test("^[0-9]{4}")) then
          .releaseDate[:4]
        else
          empty
        end
      else
        empty
      end
    ')
  fi

  # Fallback when no Audnexus JSON
  if [[ -z "$author" ]]; then
    # Try to extract author from parent directory name
    local parent_dir
    parent_dir=$(basename "$(dirname "$source_path")")
    if [[ -n "$parent_dir" && "$parent_dir" != "/" && "$parent_dir" != "." ]]; then
      author="$parent_dir"
    else
      author="Unknown Author"
    fi
  fi

  if [[ -z "$title" ]]; then
    title=$(basename "$source_path")
    # Add hash suffix to avoid collisions
    title="$title - $book_hash"
  fi

  # Sanitize each component
  author=$(sanitize_folder_component "$author")
  title=$(sanitize_folder_component "$title")

  # Build path based on series presence
  local path_components=()
  path_components+=("$base_dir")
  path_components+=("$author")

  if [[ -n "$series_name" ]]; then
    series_name=$(sanitize_folder_component "$series_name")
    path_components+=("$series_name")

    # Zero-pad series position to 2 digits, handle decimals
    if [[ -n "$series_position" ]]; then
      # Handle decimal positions (1.5 -> 01.5)
      if [[ "$series_position" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
        local int_part="${BASH_REMATCH[1]}"
        local dec_part="${BASH_REMATCH[2]}"
        series_position=$(printf "%02d.%s" "$int_part" "$dec_part")
      elif [[ "$series_position" =~ ^[0-9]+$ ]]; then
        series_position=$(printf "%02d" "$series_position")
      fi
    fi
  fi

  # Title folder with optional year and series position
  local title_folder=""
  if [[ -n "$series_name" && -n "$series_position" ]]; then
    title_folder="$series_position - $title"
  else
    title_folder="$title"
  fi

  if [[ -n "$year" ]]; then
    title_folder="$title_folder ($year)"
  fi

  path_components+=("$title_folder")

  # Join path components
  local full_path=""
  for component in "${path_components[@]}"; do
    if [[ -z "$full_path" ]]; then
      full_path="$component"
    else
      full_path="$full_path/$component"
    fi
  done

  echo "$full_path"
}

# Copy file to NFS mount using cp+chmod (not install, which fails on NFS root squash)
# Args: $1 = source_file, $2 = dest_path, $3 = file_mode (default 644), $4 = dir_mode (default 755)
copy_to_nfs_safe() {
  local source_file="$1"
  local dest_path="$2"
  local file_mode="${3:-644}"
  local dir_mode="${4:-755}"

  if [[ ! -f "$source_file" ]]; then
    log_error "Source file not found: $source_file"
    return 1
  fi

  # Create parent directory
  local dest_dir
  dest_dir=$(dirname "$dest_path")
  mkdir -p "$dest_dir"
  chmod "$dir_mode" "$dest_dir" || true

  # Copy file (NOT install -- NFS root squash fails chown)
  if ! cp "$source_file" "$dest_path"; then
    log_error "Failed to copy $source_file to $dest_path"
    return 1
  fi

  # Apply permissions
  chmod "$file_mode" "$dest_path" || true

  # Log operation with file size
  local file_size
  file_size=$(stat -f %z "$source_file" 2>/dev/null || stat -c %s "$source_file" 2>/dev/null || echo "unknown")
  log_info "Copied to NFS: $dest_path ($file_size bytes)"

  return 0
}

# Check if NFS mount is available (timeout-based stale mount detection)
# Args: $1 = nfs_path
# Returns: 0 if accessible, 1 if timeout/error
check_nfs_available() {
  local nfs_path="$1"

  if ! timeout 5 ls "$nfs_path" >/dev/null 2>&1; then
    log_warn "NFS mount unavailable or stale: $nfs_path"
    return 1
  fi

  log_debug "NFS mount accessible: $nfs_path"
  return 0
}

# Deploy companion files (cover.jpg, desc.txt, reader.txt) to output folder
# Args: $1 = work_dir, $2 = output_dir
deploy_companion_files() {
  local work_dir="$1"
  local output_dir="$2"

  local deployed_count=0

  for file in cover.jpg desc.txt reader.txt; do
    if [[ -f "$work_dir/$file" ]]; then
      if copy_to_nfs_safe "$work_dir/$file" "$output_dir/$file"; then
        deployed_count=$((deployed_count + 1))
        log_info "Deployed companion file: $file"
      fi
    else
      log_debug "Companion file not found: $file"
    fi
  done

  if [[ "$deployed_count" -eq 0 ]]; then
    log_debug "No companion files to deploy"
  else
    log_info "Deployed $deployed_count companion file(s)"
  fi

  return 0
}
