#!/usr/bin/env bash
# lib/audible.sh -- Audible catalog API client (metadata, chapters, cover art)
# Primary metadata source. Audnexus (lib/audnexus.sh) is the fallback.
# Sourced by stages/06-metadata.sh; do not execute directly.
# Requires: lib/core.sh sourced first; curl, jq available

# Reuse stat flavor detection from audnexus.sh (already sourced)
# If not available, detect here
if ! declare -p _AUDNEXUS_STAT_GNU &>/dev/null 2>&1; then
  if stat --version >/dev/null 2>&1; then
    _AUDNEXUS_STAT_GNU=1
  else
    _AUDNEXUS_STAT_GNU=0
  fi
fi

# Cache TTL check -- reuses _audnexus_cache_valid if available, otherwise provides own
if ! declare -f _audnexus_cache_valid &>/dev/null; then
  _audible_cache_valid() {
    local cache_file="$1"
    local max_age_days="${2:-30}"
    [[ -f "$cache_file" ]] || return 1
    local file_mtime now age_seconds max_age_seconds
    if [[ $_AUDNEXUS_STAT_GNU -eq 1 ]]; then
      file_mtime=$(stat -c %Y "$cache_file")
    else
      file_mtime=$(stat -f %m "$cache_file")
    fi
    now=$(date +%s)
    age_seconds=$((now - file_mtime))
    max_age_seconds=$((max_age_days * 86400))
    [[ $age_seconds -lt $max_age_seconds ]]
  }
else
  _audible_cache_valid() { _audnexus_cache_valid "$@"; }
fi

# Map AUDIBLE_REGION to API domain
# Args: $1 = region code (default: com)
# Outputs: full API base URL
_audible_api_base() {
  local region="${1:-com}"
  echo "https://api.audible.${region}/1.0"
}

# Fetch book metadata + chapters from Audible catalog API
# Args: $1 = ASIN, $2 = cache_dir (optional)
# Outputs: raw API JSON to stdout
# Returns: 0 on success, 1 on failure
fetch_audible_book() {
  local asin="$1"
  local cache_dir="${2:-${AUDNEXUS_CACHE_DIR:-${WORK_DIR:-/tmp}}}"
  local cache_file="$cache_dir/audible_book_${asin}.json"
  local cache_days="${AUDNEXUS_CACHE_DAYS:-30}"
  local region="${AUDIBLE_REGION:-com}"

  mkdir -p "$cache_dir" 2>/dev/null || true

  # Check cache first
  if _audible_cache_valid "$cache_file" "$cache_days"; then
    log_debug "Using cached Audible metadata for $asin"
    cat "$cache_file"
    return 0
  fi

  local api_base
  api_base=$(_audible_api_base "$region")

  local response_groups="category_ladders,contributors,media,product_desc"
  response_groups+=",product_attrs,product_extended_attrs,rating,series"
  response_groups+=",product_details,chapter_info"

  log_info "Fetching metadata from Audible API for $asin (region: $region)"

  local response
  if ! response=$(curl -fsSL --max-time 30 \
    "${api_base}/catalog/products/${asin}?response_groups=${response_groups}&image_sizes=2400,1000,700,500" \
    2>/dev/null); then
    log_warn "Audible API request failed for $asin"
    return 1
  fi

  # Validate JSON and check for product wrapper
  if ! echo "$response" | jq -e '.product' >/dev/null 2>&1; then
    log_warn "Audible API returned invalid or empty response for $asin"
    return 1
  fi

  # Cache valid response
  echo "$response" > "$cache_file"
  log_debug "Cached Audible metadata for $asin at $cache_file"

  echo "$response"
  return 0
}

# Normalize Audible API JSON to Audnexus-compatible shape with extra fields
# Input: raw Audible API JSON (with .product wrapper) on stdin or $1
# Output: normalized JSON to stdout
# Extra fields (not in Audnexus): subtitle, copyright, publisher, isbn, language,
#   rating, format, runtimeMin, audibleUrl, _source
normalize_audible_json() {
  local raw_json="${1:-$(cat)}"
  local region="${AUDIBLE_REGION:-com}"

  echo "$raw_json" | jq --arg region "$region" '
    .product as $p |

    # Build genre list from category ladders
    ([$p.category_ladders[]?.ladder[]? | {name: .name}] | unique_by(.name)) as $genres |

    # Build full category path (joined with " / ")
    ([$p.category_ladders[]?.ladder | select(. != null) |
      [.[].name] | join(" / ")] | first // "") as $genre_path |

    # First author with ASIN (for album-artist)
    ($p.authors // [] | map(select(.asin != null and .asin != "")) | first // null) as $primary_author |

    # Series info (first entry, matching Mp3tag behavior)
    ($p.series // [] | first // null) as $series |

    # Cover image -- prefer highest resolution available
    (if $p.product_images then
      ($p.product_images["2400"] //
       $p.product_images["1000"] //
       $p.product_images["700"] //
       $p.product_images["500"] // null)
    else null end) as $image |

    # Chapter data from content_metadata
    ($p.content_metadata.chapter_info // null) as $chapter_info |

    {
      asin: $p.asin,
      title: $p.title,
      subtitle: ($p.subtitle // null),
      authors: [($p.authors // [])[] | {asin: (.asin // null), name: .name}],
      narrators: [($p.narrators // [])[] | {name: .name}],
      seriesPrimary: (if $series then {
        name: $series.title,
        position: ($series.sequence // null)
      } else null end),
      genres: $genres,
      genrePath: $genre_path,
      description: ($p.publisher_summary // null),
      summary: ($p.publisher_summary // null),
      releaseDate: ($p.release_date // null),
      image: $image,
      copyright: ($p.copyright // null),
      publisher: ($p.publisher_name // null),
      isbn: ($p.isbn // null),
      language: ($p.language // null),
      rating: ($p.rating.overall_distribution.display_average_rating // null),
      isAdult: ($p.is_adult_product // false),
      format: ($p.format_type // null),
      runtimeMin: ($p.runtime_length_min // null),
      audibleUrl: ("https://www.audible." + $region + "/pd/" + $p.asin),
      primaryAuthor: (if $primary_author then {
        asin: $primary_author.asin,
        name: $primary_author.name
      } else null end),
      chapters: (if $chapter_info then {
        isAccurate: ($chapter_info.is_accurate // true),
        runtimeLengthMs: ($chapter_info.runtime_length_ms // null),
        brandIntroDurationMs: ($chapter_info.brandIntroDurationMs // null),
        brandOutroDurationMs: ($chapter_info.brandOutroDurationMs // null),
        chapters: [($chapter_info.chapters // [])[] | {
          lengthMs: .length_ms,
          startOffsetMs: .start_offset_ms,
          startOffsetSec: .start_offset_sec,
          title: .title
        }]
      } else null end),
      _source: "audible"
    }
  '
}

# Extract chapter data from normalized book JSON in Audnexus-compatible format
# This produces output compatible with convert_chapters_to_tone()
# Args: $1 = normalized book JSON (string)
# Outputs: JSON with .chapters[] and .runtimeLengthMs (same shape as Audnexus chapters)
# Returns: 0 on success, 1 if no chapter data
extract_audible_chapters() {
  local book_json="$1"

  local has_chapters
  has_chapters=$(echo "$book_json" | jq -r '.chapters.chapters | length // 0')

  if [[ "$has_chapters" -eq 0 ]]; then
    log_debug "No chapter data in Audible response"
    return 1
  fi

  echo "$book_json" | jq '{
    asin: .asin,
    brandIntroDurationMs: .chapters.brandIntroDurationMs,
    brandOutroDurationMs: .chapters.brandOutroDurationMs,
    chapters: .chapters.chapters,
    isAccurate: .chapters.isAccurate,
    runtimeLengthMs: .chapters.runtimeLengthMs
  }'
}

# Download cover art from Audible product images
# Args: $1 = normalized book JSON (string), $2 = output_path
# Returns: 0 on success, 1 on failure
download_audible_cover() {
  local book_json="$1"
  local output_path="$2"

  local image_url
  image_url=$(echo "$book_json" | jq -r '.image // empty')

  if [[ -z "$image_url" ]]; then
    log_warn "No cover art URL found in Audible metadata"
    return 1
  fi

  log_info "Downloading cover art from Audible"

  if ! curl -fsSL --max-time 60 -o "$output_path" "$image_url" 2>/dev/null; then
    log_warn "Failed to download cover art from Audible"
    return 1
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

# Search Audible catalog by keywords (for ASIN-less lookups)
# Args: $1 = search query, $2 = expected_title (optional), $3 = expected_author (optional)
# When hints are provided, scores results and picks best match above threshold.
# Without hints, returns first result (backward compat).
# Outputs: best matching ASIN to stdout, or empty
# Returns: 0 if found, 1 if no results
search_audible_book() {
  local query="$1"
  local expected_title="${2:-}"
  local expected_author="${3:-}"
  local region="${AUDIBLE_REGION:-com}"

  # Prefer Python for search + scoring (handles its own API call)
  local py_script="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}/python/asin_search.py"
  local py_venv="${py_script%/*}/.venv/bin/python3"
  local py_cmd="python3"
  [[ -x "$py_venv" ]] && py_cmd="$py_venv"

  if [[ -f "$py_script" ]] && command -v "$py_cmd" >/dev/null 2>&1; then
    local result
    local stderr_file="/tmp/asin_search_$$.err"
    result=$("$py_cmd" "$py_script" \
      --query "$query" \
      --expected-title "$expected_title" \
      --expected-author "$expected_author" \
      --region "$region" \
      ${AI_API_URL:+--ai-api-url "$AI_API_URL"} \
      ${AI_API_KEY:+--ai-api-key "$AI_API_KEY"} \
      ${AI_MODEL:+--ai-model "$AI_MODEL"} \
      --threshold "${ASIN_SEARCH_THRESHOLD:-65}" 2>"$stderr_file") || true

    [[ -s "$stderr_file" ]] && log_debug "Python asin_search: $(cat "$stderr_file")"
    rm -f "$stderr_file"

    if [[ -n "$result" ]]; then
      local asin score method result_title result_author
      asin=$(echo "$result" | jq -r '.asin // empty')
      score=$(echo "$result" | jq -r '.score // "?"')
      method=$(echo "$result" | jq -r '.method // "unknown"')
      result_title=$(echo "$result" | jq -r '.title // ""')
      result_author=$(echo "$result" | jq -r '.author // ""')

      if [[ -n "$asin" ]]; then
        log_info "Audible search matched: '$result_title' by $result_author (score: $score, method: $method, ASIN: $asin)"
        echo "$asin"
        return 0
      fi
    fi

    log_warn "Audible search: Python scoring found no confident match for: $query"
    return 1
  fi

  # Fallback: no Python available -- use bash curl + first result
  local api_base
  api_base=$(_audible_api_base "$region")

  local encoded_query
  encoded_query=$(printf '%s' "$query" | jq -sRr @uri)

  local response
  if ! response=$(curl -fsSL --max-time 30 \
    "${api_base}/catalog/products?keywords=${encoded_query}&num_results=10&products_sort_by=Relevance&response_groups=contributors,media,product_desc,product_attrs,series&image_sizes=100" \
    2>/dev/null); then
    log_warn "Audible search API request failed"
    return 1
  fi

  local first_asin
  first_asin=$(echo "$response" | jq -r '.products[0].asin // empty')
  if [[ -n "$first_asin" ]]; then
    log_info "Audible search found ASIN: $first_asin (no Python, first result)"
    echo "$first_asin"
    return 0
  fi

  log_info "No Audible results for query: $query"
  return 1
}
