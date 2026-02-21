#!/usr/bin/env bash
# lib/audnexus.sh -- Audnexus API client (metadata, chapters, cover art, caching)
# Sourced by bin/audiobook-convert; do not execute directly.
# Requires: lib/core.sh, lib/ffmpeg.sh sourced first; curl, jq available

# Detect stat flavor once at source time (GNU coreutils vs BSD)
if stat --version >/dev/null 2>&1; then
  _AUDNEXUS_STAT_GNU=1
else
  _AUDNEXUS_STAT_GNU=0
fi

# Get file modification time as epoch seconds
_audnexus_stat_mtime() {
  local file="$1"
  if [[ $_AUDNEXUS_STAT_GNU -eq 1 ]]; then
    stat -c %Y "$file"
  else
    stat -f %m "$file"
  fi
}

# Check if a cache file exists and is within TTL
# Returns 0 if cache is valid, 1 otherwise
_audnexus_cache_valid() {
  local cache_file="$1"
  local max_age_days="${2:-30}"

  [[ -f "$cache_file" ]] || return 1

  local file_mtime now age_seconds max_age_seconds
  file_mtime=$(_audnexus_stat_mtime "$cache_file")
  now=$(date +%s)
  age_seconds=$((now - file_mtime))
  max_age_seconds=$((max_age_days * 86400))

  [[ $age_seconds -lt $max_age_seconds ]]
}

# Fetch book metadata from Audnexus API
# Args: $1 = ASIN, $2 = cache_dir (optional, defaults to AUDNEXUS_CACHE_DIR or WORK_DIR)
# Outputs: JSON to stdout
# Returns: 0 on success, 1 on failure
fetch_audnexus_book() {
  local asin="$1"
  local cache_dir="${2:-${AUDNEXUS_CACHE_DIR:-${WORK_DIR:-/tmp}}}"
  local cache_file="$cache_dir/audnexus_book_${asin}.json"
  local cache_days="${AUDNEXUS_CACHE_DAYS:-30}"
  local region="${AUDNEXUS_REGION:-us}"

  mkdir -p "$cache_dir" 2>/dev/null || true

  # Check cache first
  if _audnexus_cache_valid "$cache_file" "$cache_days"; then
    log_debug "Using cached book metadata for $asin"
    cat "$cache_file"
    return 0
  fi

  log_info "Fetching book metadata from Audnexus for $asin"

  local response
  if ! response=$(curl -fsSL --max-time 30 \
    "https://api.audnex.us/books/${asin}?region=${region}" 2>/dev/null); then
    log_warn "Audnexus API request failed for book $asin"
    return 1
  fi

  # Validate JSON
  if ! echo "$response" | jq empty 2>/dev/null; then
    log_warn "Audnexus returned invalid JSON for book $asin"
    return 1
  fi

  # Cache valid response
  echo "$response" > "$cache_file"
  log_debug "Cached book metadata for $asin at $cache_file"

  echo "$response"
  return 0
}

# Fetch chapter data from Audnexus API
# Args: $1 = ASIN, $2 = cache_dir (optional)
# Outputs: JSON to stdout
# Returns: 0 on success, 1 on failure (404 is expected for some books)
fetch_audnexus_chapters() {
  local asin="$1"
  local cache_dir="${2:-${AUDNEXUS_CACHE_DIR:-${WORK_DIR:-/tmp}}}"
  local cache_file="$cache_dir/audnexus_chapters_${asin}.json"
  local cache_days="${AUDNEXUS_CACHE_DAYS:-30}"
  local region="${AUDNEXUS_REGION:-us}"

  mkdir -p "$cache_dir" 2>/dev/null || true

  # Check cache first
  if _audnexus_cache_valid "$cache_file" "$cache_days"; then
    log_debug "Using cached chapter data for $asin"
    cat "$cache_file"
    return 0
  fi

  log_info "Fetching chapter data from Audnexus for $asin"

  local response
  if ! response=$(curl -fsSL --max-time 30 \
    "https://api.audnex.us/books/${asin}/chapters?region=${region}" 2>/dev/null); then
    log_info "No chapter data available from Audnexus for $asin (may not exist)"
    return 1
  fi

  # Validate JSON
  if ! echo "$response" | jq empty 2>/dev/null; then
    log_warn "Audnexus returned invalid JSON for chapters $asin"
    return 1
  fi

  # Check accuracy flag
  local is_accurate
  is_accurate=$(echo "$response" | jq -r '.isAccurate // true')
  if [[ "$is_accurate" == "false" ]]; then
    log_warn "Audnexus reports chapters may be inaccurate for $asin"
  fi

  # Cache valid response
  echo "$response" > "$cache_file"
  log_debug "Cached chapter data for $asin at $cache_file"

  echo "$response"
  return 0
}

# Download cover art from Audnexus book metadata
# Args: $1 = book_json (string), $2 = output_path
# Returns: 0 on success, 1 on failure
download_cover_art() {
  local book_json="$1"
  local output_path="$2"

  # Extract image URL
  local image_url
  image_url=$(echo "$book_json" | jq -r '.image // empty')

  if [[ -z "$image_url" ]]; then
    log_warn "No cover art URL found in book metadata"
    return 1
  fi

  # Upgrade to high resolution
  local hires_url
  # shellcheck disable=SC2001
  hires_url=$(echo "$image_url" | sed 's/_SL[0-9]*_/_SL2000_/')

  log_info "Downloading cover art from Audnexus"

  # Try high-res first, fall back to original
  if ! curl -fsSL --max-time 60 -o "$output_path" "$hires_url" 2>/dev/null; then
    log_debug "High-res cover art failed, trying original URL"
    if ! curl -fsSL --max-time 60 -o "$output_path" "$image_url" 2>/dev/null; then
      log_warn "Failed to download cover art"
      return 1
    fi
  fi

  # Validate JPEG magic bytes
  if ! xxd -l 3 -p "$output_path" | grep -q '^ffd8ff'; then
    log_warn "Downloaded cover art is not a valid JPEG"
    rm -f "$output_path"
    return 1
  fi

  local file_size
  file_size=$(wc -c < "$output_path" | tr -d ' ')
  log_info "Cover art saved: $output_path (${file_size} bytes)"
  return 0
}

# Validate chapter duration against M4B file duration
# Args: $1 = m4b_file, $2 = audnexus_runtime_ms
# Returns: 0 if within tolerance, 1 if exceeded
validate_chapter_duration() {
  local m4b_file="$1"
  local audnexus_runtime_ms="$2"
  local tolerance="${CHAPTER_DURATION_TOLERANCE:-5}"

  # Get M4B duration in seconds (float)
  local m4b_duration
  m4b_duration=$(get_duration "$m4b_file")

  if [[ -z "$m4b_duration" ]]; then
    log_warn "Could not determine duration of $m4b_file"
    return 1
  fi

  # Convert Audnexus ms to seconds
  local audnexus_seconds
  audnexus_seconds=$(awk "BEGIN {printf \"%.3f\", $audnexus_runtime_ms / 1000}")

  # Calculate percentage difference
  local pct_diff
  pct_diff=$(awk "BEGIN {
    diff = $m4b_duration - $audnexus_seconds
    if (diff < 0) diff = -diff
    if ($audnexus_seconds > 0) {
      printf \"%.2f\", (diff / $audnexus_seconds) * 100
    } else {
      print \"100.00\"
    }
  }")

  # Compare against tolerance
  local within_tolerance
  within_tolerance=$(awk "BEGIN { print ($pct_diff <= $tolerance) ? 1 : 0 }")

  if [[ "$within_tolerance" == "1" ]]; then
    log_debug "Chapter duration valid: ${pct_diff}% difference (tolerance: ${tolerance}%)"
    return 0
  else
    log_warn "Chapter duration mismatch: ${pct_diff}% difference exceeds ${tolerance}% tolerance"
    return 1
  fi
}

# Convert Audnexus chapter JSON to timestamp format
# Args: $1 = chapters_json (string, full JSON from chapters endpoint)
# Outputs: HH:MM:SS.mmm Title lines to stdout
convert_chapters_to_timestamps() {
  local chapters_json="$1"

  echo "$chapters_json" | jq -r '
    .chapters[] |
    .startOffsetMs as $ms |
    ($ms / 3600000 | floor) as $h |
    (($ms % 3600000) / 60000 | floor) as $m |
    (($ms % 60000) / 1000 | floor) as $s |
    ($ms % 1000) as $frac |
    (.title | gsub("[\\p{Cc}]"; "") | ltrimstr(" ") | rtrimstr(" ")) as $title |
    "\($h | tostring | if length < 2 then "0" + . else . end):\($m | tostring | if length < 2 then "0" + . else . end):\($s | tostring | if length < 2 then "0" + . else . end).\($frac | tostring | if length < 3 then ("000" + .)[-3:] else . end) \($title)"
  '
}

# Extract metadata fields from Audnexus book JSON
# Args: $1 = book_json (string)
# Outputs: shell-evaluable variable assignments to stdout
extract_metadata_fields() {
  local book_json="$1"

  echo "$book_json" | jq -r '
    def shell_escape: gsub("'\''"; "'\''\\'\'''\''");
    def safe_val: if . == null then "" else tostring | shell_escape end;

    "META_TITLE='\''" + (.title | safe_val) + "'\''",
    "META_AUTHOR='\''" + ([.authors[]?.name] | join(", ") | safe_val) + "'\''",
    "META_NARRATOR='\''" + ([.narrators[]?.name] | join(", ") | safe_val) + "'\''",
    "META_GENRE='\''" + (.genres[0]?.asin // .genres[0]?.name // "" | safe_val) + "'\''",
    "META_DESCRIPTION='\''" + (.summary | if . == null then "" else gsub("<[^>]*>"; "") end | safe_val) + "'\''",
    "META_RELEASE_DATE='\''" + (.releaseDate | if . == null then ""
      elif test("^[0-9]{4}$") then . + "-01-01"
      elif test("^[0-9]{4}-[0-9]{2}-[0-9]{2}") then .[:10]
      else . end | safe_val) + "'\''",
    "META_SERIES_NAME='\''" + (.seriesPrimary?.name // "" | safe_val) + "'\''",
    "META_SERIES_POSITION='\''" + (.seriesPrimary?.position // "" | tostring | capture("^(?<n>[0-9]+(\\.[0-9]+)?)").n // "" | safe_val) + "'\''",
    "META_ASIN='\''" + (.asin // "" | safe_val) + "'\''"
  '
}
