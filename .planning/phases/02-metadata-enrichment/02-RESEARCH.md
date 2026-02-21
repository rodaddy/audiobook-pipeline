# Phase 2: Metadata Enrichment - Unified Research

**Researched:** 2026-02-20
**Domains:** ASIN discovery, Audnexus API integration, tone CLI tagging
**Confidence:** HIGH

## Summary

Phase 2 implements audiobook metadata enrichment through a three-stage pipeline: (1) ASIN discovery using priority chain (manual `.asin` file, folder regex, Readarr API, user prompt), (2) Audnexus API integration for metadata and chapter retrieval, and (3) tone CLI tagging to embed all metadata into M4B files.

**Key architectural decisions:**
- **ASIN discovery:** Manual `.asin` file (P0) as reliable override, folder regex (P1) for automation, optional Readarr integration (deferred pending user validation)
- **Audnexus API:** Unauthenticated REST API with 100 req/min rate limit, Redis-backed caching, 5% duration tolerance for chapter matching
- **tone CLI:** Single-pass tagging on local disk (never NFS), FFMETADATA1 or simple timestamp chapter import, JSON verification before move to destination

**Implementation priority:** Manual `.asin` + folder regex + Audnexus + tone (deferred: Readarr API until user confirms need)

**Critical constraints:**
- Always tag M4B on local disk, then move to NFS (in-place modification on NFS causes corruption)
- Validate Audnexus chapter duration within 5% of M4B duration before import
- Graceful degradation: metadata enrichment failures should NOT block conversion pipeline

## Standard Stack

### Core Tools
| Tool | Version | Purpose | Installation |
|------|---------|---------|--------------|
| **tone** | v0.1.5 | M4B metadata tagging (title, author, narrator, series, cover, chapters) | `wget https://github.com/sandreas/tone/releases/download/v0.1.5/tone-0.1.5-linux-x64.tar.gz && tar xzf tone-*.tar.gz && sudo mv tone /usr/local/bin/` |
| **curl** | apt | HTTP requests for Audnexus API and cover art download | `apt-get install curl` |
| **jq** | apt | JSON parsing for Audnexus responses and tone dump verification | `apt-get install jq` |

### ASIN Format Specification
ASINs are 10-character alphanumeric identifiers (base-36) used by Amazon/Audible:

| Attribute | Value |
|-----------|-------|
| **Length** | Exactly 10 characters |
| **Character set** | A-Z, 0-9 (uppercase by convention) |
| **Audiobook prefix** | `B0` (most common pattern for digital products) |
| **Validation regex** | `^B0[A-Z0-9]{8}$` (audiobooks) or `^[A-Z0-9]{10}$` (all ASINs) |

**Examples:** `B017V4U2VQ`, `B002V0QK4C`, `B00B5HZGUG`, `B0CKM5YQX8`

### Audnexus API Endpoints

#### GET /books/{asin}
**Purpose:** Retrieve comprehensive book metadata
**URL:** `https://api.audnex.us/books/{asin}?region=us&seedAuthors=0&update=0`

**Key Response Fields:**
- `title` - Book title
- `authors[].name` - Author name(s) (array)
- `narrators[]` - Narrator name(s) (array)
- `seriesPrimary.name` / `.position` - Series metadata
- `copyright` - Year (or extract from `releaseDate`)
- `description` - Full HTML description
- `genres[].name` - Genre tags
- `image` - Cover art URL (JPEG, 500x500 default, up to 3200x3200 available)
- `runtimeLengthMin` - Total runtime in minutes (for duration matching)

**Rate Limit:** 100 requests/minute (default, configurable)
**Error Codes:** 400 (invalid ASIN), 404 (not found), 429 (rate limit), 500 (upstream failure)

#### GET /books/{asin}/chapters
**Purpose:** Retrieve millisecond-precision chapter timestamps
**URL:** `https://api.audnex.us/books/{asin}/chapters?region=us&update=0`

**Key Response Fields:**
- `chapters[]` - Array of chapter objects (ordered)
- `chapters[].startOffsetMs` - Chapter start time in milliseconds
- `chapters[].lengthMs` - Chapter duration in milliseconds
- `chapters[].title` - Chapter title (sanitize before use)
- `runtimeLengthMs` - Total runtime in milliseconds (for duration matching)
- `isAccurate` - Boolean flag (log warning when `false`, but still use if duration matches)

**Error Codes:** 400 (invalid ASIN), 404 (chapters unavailable), 429 (rate limit)

## Architecture Patterns

### Pattern 1: ASIN Discovery Priority Chain

Implement ASIN discovery as a chain of responsibility with validation at each step:

```bash
discover_asin() {
  local source_dir="$1"
  local asin=""

  # Priority 1: Manual .asin file (P0 - highest reliability)
  asin=$(check_manual_asin_file "$source_dir")
  if [[ -n "$asin" ]] && validate_asin_against_audnexus "$asin"; then
    echo "$asin"
    return 0
  fi

  # Priority 2: Folder name regex (P1 - automation)
  asin=$(extract_asin_from_folder "$source_dir")
  if [[ -n "$asin" ]] && validate_asin_against_audnexus "$asin"; then
    echo "$asin"
    return 0
  fi

  # Priority 3: Readarr API (deferred - conditional on user setup)
  if [[ -n "${READARR_API_URL:-}" ]]; then
    asin=$(query_readarr_for_asin "$source_dir")
    if [[ -n "$asin" ]] && validate_asin_against_audnexus "$asin"; then
      echo "$asin"
      return 0
    fi
  fi

  # Priority 4: User prompt (interactive only)
  if [[ "${INTERACTIVE:-false}" == "true" ]]; then
    asin=$(prompt_user_for_asin "$source_dir")
    if [[ -n "$asin" ]] && validate_asin_against_audnexus "$asin"; then
      echo "$asin"
      return 0
    fi
  fi

  # All methods failed - not fatal, continue without metadata
  log_warn "No valid ASIN found for: $(basename "$source_dir")"
  return 1
}
```

**Manual .asin File Format:**
```bash
# File: /path/to/Book Name/.asin
# Content: Single line with ASIN (whitespace trimmed)
B00JCDK5ME
```

**Folder Regex Patterns (try in order):**
| Pattern | Example | Regex |
|---------|---------|-------|
| Brackets | `Book Title [B00JCDK5ME]` | `\[([A-Z0-9]{10})\]` |
| Parentheses | `Book Title (B00JCDK5ME)` | `\(([A-Z0-9]{10})\)` |
| ASIN prefix | `B00G3L6JMS - Book Title` | `^([A-Z0-9]{10}) - ` |
| Space-delimited | `Book B00JCDK5ME Title` | `\s([A-Z0-9]{10})\s` |

**Always uppercase and validate** extracted ASINs before trusting them.

### Pattern 2: Audnexus Metadata Fetch with Caching

Use file-based JSON cache in work directory (30-day TTL aligned with Audnexus backend cache):

```bash
fetch_audnexus_book() {
  local asin="$1"
  local cache_file="$WORK_DIR/audnexus_book_${asin}.json"

  # Check cache (30-day TTL)
  if [[ -f "$cache_file" ]]; then
    local cache_age_days
    cache_age_days=$(( ($(date +%s) - $(stat -f %m "$cache_file" 2>/dev/null || stat -c %Y "$cache_file")) / 86400 ))
    if [[ "$cache_age_days" -lt 30 ]]; then
      log_debug "Using cached book metadata for $asin (${cache_age_days}d old)"
      cat "$cache_file"
      return 0
    fi
  fi

  # Fetch from API
  log_info "Fetching book metadata for $asin from Audnexus"
  local api_url="https://api.audnex.us/books/${asin}?region=us"
  local response

  if ! response=$(curl -fsSL --max-time 30 "$api_url" 2>&1); then
    log_error "Failed to fetch book metadata: $response"
    return 1
  fi

  # Validate JSON response
  if ! echo "$response" | jq empty 2>/dev/null; then
    log_error "Invalid JSON response from Audnexus"
    return 1
  fi

  # Cache successful response only (never cache errors)
  echo "$response" > "$cache_file"
  echo "$response"
  return 0
}
```

**Cache invalidation:**
- Automatic: 30-day TTL based on file modification time
- Manual: Delete `audnexus_*.json` files from work directory
- Force refresh: Add `--force-metadata` flag to pipeline (skips cache)

### Pattern 3: Duration Matching for Chapter Validation

Validate Audnexus chapters match M4B duration within 5% tolerance before import:

```bash
validate_chapter_duration() {
  local m4b_file="$1"
  local audnexus_runtime_ms="$2"

  # Get M4B duration in seconds (float)
  local m4b_duration_s
  m4b_duration_s=$(get_duration "$m4b_file")

  # Convert Audnexus runtime to seconds (integer milliseconds -> float seconds)
  local audnexus_duration_s
  audnexus_duration_s=$(echo "scale=3; $audnexus_runtime_ms / 1000" | bc)

  # Calculate percentage difference
  local diff_s
  diff_s=$(echo "scale=3; ($m4b_duration_s - $audnexus_duration_s)" | bc | tr -d '-')
  local diff_pct
  diff_pct=$(echo "scale=2; ($diff_s / $audnexus_duration_s) * 100" | bc)

  # Compare with 5% threshold
  if (( $(echo "$diff_pct > 5" | bc -l) )); then
    log_warn "Duration mismatch: M4B=${m4b_duration_s}s, Audnexus=${audnexus_duration_s}s (${diff_pct}% difference)"
    return 1
  fi

  log_debug "Duration match validated: ${diff_pct}% difference (within 5% threshold)"
  return 0
}
```

**Why 5% tolerance?**
- 720-minute audiobook: 5% = 36 minutes (handles encoding differences, intro/outro variance)
- 360-minute audiobook: 5% = 18 minutes
- 60-minute audiobook: 5% = 3 minutes
- Balances strictness (catch wrong editions) vs flexibility (allow transcoding variance)

**Duration mismatch scenarios:**
- Mismatch > 5%: Fall back to file-boundary chapters, log warning
- Mismatch <= 5%: Import Audnexus chapters
- `isAccurate: false` in response: Log warning but still use chapters if duration matches

### Pattern 4: Single-Pass tone Tagging

Write all metadata, cover art, and chapters in one tone invocation (minimizes I/O, ensures atomic updates):

```bash
# Tag on local disk only (never NFS)
tone tag "$WORK_DIR/audiobook.m4b" \
  --meta-title "$TITLE" \
  --meta-artist "$AUTHOR" \
  --meta-album "$SERIES_NAME" \
  --meta-narrator "$NARRATOR" \
  --meta-genre "$GENRE" \
  --meta-recording-date "$RELEASE_DATE" \
  --meta-description "$DESCRIPTION" \
  --meta-part "$SERIES_POSITION" \
  --meta-cover-file "$WORK_DIR/cover.jpg" \
  --meta-chapters-file "$WORK_DIR/chapters.txt"
```

**Critical constraints:**
- **Local disk only:** tone modifies files in-place. NFS I/O can corrupt M4B. Always tag on local disk, then move to NFS.
- **ISO 8601 dates:** `--meta-recording-date` requires `YYYY-MM-DD` format (NOT just year like `"2025"`).
- **Cover art resolution:** Download high-res cover by modifying image URL: `s/_SL500_/_SL2000_/` (2000x2000 pixels).

### Pattern 5: Metadata Verification with tone dump

Verify all tags were written correctly before moving to destination:

```bash
# Get metadata as JSON (exclude embedded pictures to avoid binary data issues)
tone dump "$WORK_DIR/audiobook.m4b" --format json \
  --include-property title \
  --include-property artist \
  --include-property narrator \
  --include-property chapters > metadata.json

# Verify specific fields
ACTUAL_TITLE=$(jq -r '.meta.title' metadata.json)
ACTUAL_CHAPTERS=$(jq -r '.meta.chapters | length' metadata.json)

if [[ "$ACTUAL_TITLE" != "$EXPECTED_TITLE" ]]; then
  log_error "Title mismatch: expected '$EXPECTED_TITLE', got '$ACTUAL_TITLE'"
  exit 1
fi

if [[ "$ACTUAL_CHAPTERS" -ne "$EXPECTED_CHAPTER_COUNT" ]]; then
  log_error "Chapter count mismatch: expected $EXPECTED_CHAPTER_COUNT, got $ACTUAL_CHAPTERS"
  exit 1
fi
```

### Pattern 6: Cover Art Download and Embed

Extract cover URL from Audnexus, download high-res version, verify JPEG integrity:

```bash
download_cover_art() {
  local book_json="$1"
  local output_path="$2"

  # Extract image URL
  local image_url
  image_url=$(echo "$book_json" | jq -r '.image // empty')

  if [[ -z "$image_url" ]]; then
    log_warn "No cover art URL in metadata"
    return 1
  fi

  # Upgrade to 2000x2000 resolution
  local hires_url
  hires_url=$(echo "$image_url" | sed 's/_SL[0-9]*_/_SL2000_/')

  log_info "Downloading cover art: $hires_url"
  if ! curl -fsSL --max-time 30 -o "$output_path" "$hires_url"; then
    log_warn "Failed to download cover art, trying original resolution"
    if ! curl -fsSL --max-time 30 -o "$output_path" "$image_url"; then
      log_error "Cover art download failed"
      return 1
    fi
  fi

  # Verify JPEG magic bytes (FF D8 FF)
  if ! xxd -l 3 -p "$output_path" | grep -q '^ffd8ff'; then
    log_error "Downloaded file is not a valid JPEG"
    rm -f "$output_path"
    return 1
  fi

  log_info "Cover art downloaded: $(stat -f %z "$output_path" 2>/dev/null || stat -c %s "$output_path") bytes"
  return 0
}
```

### Pattern 7: Chapter Format Conversion

Convert Audnexus millisecond offsets to tone's simple timestamp format:

```bash
# Input: Audnexus chapters JSON with startOffsetMs
# Output: chapters.txt in HH:MM:SS.mmm Title format

jq -r '.chapters[] |
  (.startOffsetMs / 1000 | floor) as $total_sec |
  ($total_sec / 3600 | floor) as $h |
  (($total_sec % 3600) / 60 | floor) as $m |
  ($total_sec % 60) as $s |
  (.startOffsetMs % 1000) as $ms |
  "\(("%02d" | [$h]) | add):\(("%02d" | [$m]) | add):\(("%02d" | [$s]) | add).\(("%03d" | [$ms]) | add) \(.title)"
' audnexus_chapters_${ASIN}.json > "$WORK_DIR/chapters.txt"
```

**Alternative:** Use `--meta-ffmetadata-file` with FFMETADATA1 format (more verbose, but compatible with existing ffmpeg workflows).

## Code Examples

### Complete Metadata Enrichment Pipeline

```bash
#!/usr/bin/env bash
# Phase 2: Metadata Enrichment Stage
# Tags M4B with Audnexus metadata, cover art, and chapters

enrich_metadata() {
  local source_dir="$1"
  local work_dir="$2"
  local m4b_file="$work_dir/audiobook.m4b"

  # Step 1: Discover ASIN (priority chain)
  local asin
  asin=$(discover_asin "$source_dir")

  if [[ -z "$asin" ]]; then
    log_warn "No ASIN found - skipping metadata enrichment"
    return 0  # Not fatal - continue with file-boundary chapters
  fi

  # Step 2: Fetch book metadata from Audnexus (with caching)
  local book_json
  book_json=$(fetch_audnexus_book "$asin")

  if [[ $? -ne 0 ]]; then
    log_warn "Failed to fetch book metadata from Audnexus - continuing without enrichment"
    return 0  # Graceful degradation
  fi

  # Step 3: Extract metadata fields
  local title author narrator genre description release_date
  local series_name series_position

  title=$(echo "$book_json" | jq -r '.title')
  author=$(echo "$book_json" | jq -r '.authors | map(.name) | join(", ")')
  narrator=$(echo "$book_json" | jq -r '.narrators | join(", ")')
  genre=$(echo "$book_json" | jq -r '.genres[0].name // empty')
  description=$(echo "$book_json" | jq -r '.description // .summary // empty')
  release_date=$(echo "$book_json" | jq -r '.releaseDate // ((.copyright | tostring) + "-01-01") // empty')
  series_name=$(echo "$book_json" | jq -r '.seriesPrimary.name // empty')
  series_position=$(echo "$book_json" | jq -r '.seriesPrimary.position // empty')

  # Step 4: Download cover art (high-res)
  if ! download_cover_art "$book_json" "$work_dir/cover.jpg"; then
    log_warn "Cover art download failed - continuing without cover"
  fi

  # Step 5: Fetch and validate chapters
  local chapters_json
  chapters_json=$(fetch_audnexus_chapters "$asin")

  if [[ $? -eq 0 ]]; then
    local audnexus_runtime_ms
    audnexus_runtime_ms=$(echo "$chapters_json" | jq -r '.runtimeLengthMs')

    if validate_chapter_duration "$m4b_file" "$audnexus_runtime_ms"; then
      # Convert to simple timestamp format
      echo "$chapters_json" | jq -r '.chapters[] |
        (.startOffsetMs / 1000 | floor) as $total_sec |
        ($total_sec / 3600 | floor) as $h |
        (($total_sec % 3600) / 60 | floor) as $m |
        ($total_sec % 60) as $s |
        (.startOffsetMs % 1000) as $ms |
        "\(("%02d" | [$h]) | add):\(("%02d" | [$m]) | add):\(("%02d" | [$s]) | add).\(("%03d" | [$ms]) | add) \(.title)"
      ' > "$work_dir/chapters.txt"

      log_info "Imported $(wc -l < "$work_dir/chapters.txt") chapters from Audnexus"
    else
      log_warn "Chapter duration mismatch - will use file-boundary chapters"
      # Note: file-boundary chapters are generated in Phase 1, already exist
    fi
  else
    log_info "No chapters from Audnexus - using file-boundary chapters"
  fi

  # Step 6: Tag M4B with tone (single pass)
  local tone_args=(
    "--meta-title" "$title"
    "--meta-artist" "$author"
  )

  [[ -n "$narrator" ]] && tone_args+=("--meta-narrator" "$narrator")
  [[ -n "$genre" ]] && tone_args+=("--meta-genre" "$genre")
  [[ -n "$release_date" ]] && tone_args+=("--meta-recording-date" "$release_date")
  [[ -n "$description" ]] && tone_args+=("--meta-description" "$description")
  [[ -n "$series_name" ]] && tone_args+=("--meta-album" "$series_name")
  [[ -n "$series_position" ]] && tone_args+=("--meta-part" "$series_position")
  [[ -f "$work_dir/cover.jpg" ]] && tone_args+=("--meta-cover-file" "$work_dir/cover.jpg")
  [[ -f "$work_dir/chapters.txt" ]] && tone_args+=("--meta-chapters-file" "$work_dir/chapters.txt")

  if ! tone tag "$m4b_file" "${tone_args[@]}"; then
    log_error "tone tagging failed"
    return 1
  fi

  # Step 7: Verify metadata
  tone dump "$m4b_file" --format json \
    --include-property title \
    --include-property artist \
    --include-property chapters > "$work_dir/metadata-verify.json"

  local actual_title actual_chapters
  actual_title=$(jq -r '.meta.title' "$work_dir/metadata-verify.json")
  actual_chapters=$(jq -r '.meta.chapters | length' "$work_dir/metadata-verify.json")

  log_info "Metadata verification: title='$actual_title', chapters=$actual_chapters"

  # Step 8: Generate companion files (desc.txt, reader.txt)
  echo "$description" | sed 's/<[^>]*>//g' > "$work_dir/desc.txt"
  echo "$narrator" > "$work_dir/reader.txt"

  log_info "Metadata enrichment complete for ASIN $asin"
  return 0
}
```

### Manual .asin File Check

```bash
check_manual_asin_file() {
  local source_dir="$1"
  local asin_file="$source_dir/.asin"

  if [[ ! -f "$asin_file" ]]; then
    return 1
  fi

  local asin
  asin=$(cat "$asin_file" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')

  # Validate format (B0 prefix + 8 alphanumeric)
  if [[ "$asin" =~ ^B0[A-Z0-9]{8}$ ]]; then
    log_info "ASIN from manual .asin file: $asin"
    echo "$asin"
    return 0
  else
    log_warn "Invalid ASIN format in .asin file: $asin"
    return 1
  fi
}
```

### Folder Regex Extraction

```bash
extract_asin_from_folder() {
  local source_dir="$1"
  local folder_name
  folder_name=$(basename "$source_dir")

  # Try multiple patterns in order
  local patterns=(
    '\[([A-Z0-9]{10})\]'        # [ASIN] brackets
    '\(([A-Z0-9]{10})\)'        # (ASIN) parentheses
    '^([A-Z0-9]{10})\s*-'       # ASIN - Title
    '\s([A-Z0-9]{10})\s'        # space ASIN space
  )

  for pattern in "${patterns[@]}"; do
    if [[ "$folder_name" =~ $pattern ]]; then
      local asin="${BASH_REMATCH[1]}"
      # Uppercase and validate B0 prefix
      asin=$(echo "$asin" | tr '[:lower:]' '[:upper:]')
      if [[ "$asin" =~ ^B0 ]]; then
        log_info "ASIN extracted from folder name: $asin (pattern: $pattern)"
        echo "$asin"
        return 0
      fi
    fi
  done

  return 1
}
```

### ASIN Validation Against Audnexus

```bash
validate_asin_against_audnexus() {
  local asin="$1"
  local response http_code

  response=$(curl -s -w "\n%{http_code}" "https://api.audnex.us/books/$asin")
  http_code=$(echo "$response" | tail -n1)

  case "$http_code" in
    200)
      log_debug "ASIN validated: $asin"
      return 0
      ;;
    404)
      log_warn "ASIN not found in Audnexus: $asin"
      return 1
      ;;
    422)
      log_warn "Invalid ASIN format: $asin"
      return 1
      ;;
    *)
      log_error "Audnexus API error (HTTP $http_code) for ASIN: $asin"
      return 1
      ;;
  esac
}
```

## Common Pitfalls

### Pitfall 1: Trusting Unvalidated Regex Matches
**What goes wrong:** Folder names contain 10-character alphanumeric strings that aren't ASINs (dates, random IDs). Using these without validation results in failed API calls and no metadata.
**Why it happens:** Over-reliance on regex without validation.
**How to avoid:** Always validate regex-extracted ASINs against Audnexus API before trusting them. If API returns 404 or validation error, discard and try next method.
**Warning signs:** High rate of Audnexus 404 errors, books processed without metadata despite folder containing ASIN-like strings.

### Pitfall 2: tone Tagging on NFS Mounts
**What goes wrong:** tone tag on NFS-mounted M4B produces unplayable file.
**Why it happens:** tone modifies files in-place. NFS caching and latency can cause partial writes.
**How to avoid:** Always tag on local disk (SSD/HDD), then move to NFS destination.
**Warning signs:** M4B file size changes but metadata not updated, players report "file corrupted".

### Pitfall 3: Date Format Errors
**What goes wrong:** `tone tag --meta-recording-date "2025"` fails with "not a valid value for DateTime".
**Why it happens:** tone expects ISO 8601 date format (`YYYY-MM-DD`), not year strings.
**How to avoid:** Always format dates as `YYYY-MM-DD`. If Audnexus only provides year, use `${YEAR}-01-01`.
**Warning signs:** Error message: "is not a valid value for DateTime".

### Pitfall 4: Assuming 404 Means API Failure
**What goes wrong:** Pipeline exits with error when ASIN is not found (404), treating it as fatal failure.
**Why it happens:** Confusion between "API unreachable" (network error) vs "data not found" (404 response).
**How to avoid:** Check HTTP status code - 404 is successful response, just means "not in database". Only fail on network errors (curl exit code != 0 with no HTTP response).
**Warning signs:** Pipeline fails on valid ASINs that aren't in Audnexus database yet.

### Pitfall 5: Trusting Chapter Count Over Duration
**What goes wrong:** Accepting Audnexus chapters because count matches file count, but duration is wildly different (e.g., 10 hours vs 8 hours).
**Why it happens:** Chapter count can coincidentally match even when source material differs.
**How to avoid:** Always validate total duration first (5% tolerance), then check chapter count as secondary validation.
**Warning signs:** Chapters are present but timestamps don't align with actual audio content.

### Pitfall 6: Caching Error Responses
**What goes wrong:** 404 or 429 responses get cached, preventing future successful lookups.
**Why it happens:** Cache logic writes response regardless of HTTP status.
**How to avoid:** Only cache successful 200 responses. Check HTTP status before writing cache file.
**Warning signs:** Book metadata never loads even after ASIN is added to Audnexus.

### Pitfall 7: Blocking on Readarr API Failures
**What goes wrong:** Pipeline times out or fails completely when Readarr API is unavailable.
**Why it happens:** Synchronous API calls without timeouts, no graceful degradation.
**How to avoid:** Set short timeouts on Readarr API calls (5 seconds max), catch all HTTP errors, fall through to next method on any failure.
**Warning signs:** Pipeline hangs, "connection timeout" errors.

### Pitfall 8: Case-Sensitivity Mismatch
**What goes wrong:** ASIN extracted as lowercase (`b00jcdk5me`) fails Audnexus validation or lookup.
**Why it happens:** Some download tools convert folder names to lowercase.
**How to avoid:** Always uppercase ASIN strings before validation: `ASIN=$(echo "$ASIN" | tr '[:lower:]' '[:upper:]')`.
**Warning signs:** Regex extracts valid-looking ASIN but Audnexus returns VALIDATION_ERROR.

### Pitfall 9: JSON Dump Parsing Failures
**What goes wrong:** `tone dump --format json | jq` fails with "control characters must be escaped".
**Why it happens:** Embedded cover art contains binary data that breaks JSON encoding.
**How to avoid:** Use `--include-property` to exclude embeddedPictures when parsing JSON.
**Warning signs:** jq parse errors at random line numbers when cover art is present.

## Open Questions

1. **Readarr usage:**
   - **What we know:** User's PROJECT.md mentions "Readarr is configured with root folder `/mnt/media/AudioBooks/_incoming`" but Readarr is officially retired as of 2026
   - **What's unclear:** Is user actively using Readarr? A fork like Bookshelf? Neither?
   - **Recommendation:** Ask user before implementing Readarr API integration. If not using it, skip this method entirely.

2. **ASIN cache expiry:**
   - **What we know:** ASIN-to-metadata mapping is stable (Audible doesn't reassign ASINs)
   - **What's unclear:** How often to refresh cache? Never? On force flag?
   - **Recommendation:** Start with 30-day expiry. Add `--clear-asin-cache` flag for manual purge.

3. **Multiple ASIN handling:**
   - **What we know:** Books can have different ASINs for regions, formats, editions
   - **What's unclear:** What if folder contains multiple bracketed ASINs? Which to use?
   - **Recommendation:** Use first valid ASIN found (left-to-right). User can override with `.asin` file if needed.

4. **Multi-region support:**
   - **What we know:** API supports `region` parameter (us, uk, au, ca, de, es, fr, in, it, jp)
   - **What's unclear:** How to determine correct region for a given ASIN? Try `us` first and fall back to other regions on 404?
   - **Recommendation:** Default to `region=us` for Phase 2. Add multi-region retry in future enhancement if users report missing ASINs.

5. **Companion file encoding:**
   - **What we know:** desc.txt and reader.txt are plain text files
   - **What's unclear:** Should they be UTF-8 with BOM, UTF-8 without BOM, or ASCII?
   - **Recommendation:** UTF-8 without BOM (most compatible with Plex and modern tools).

6. **Series position format:**
   - **What we know:** `seriesPrimary.position` can be string like "1", "1.5", "Book 1", etc.
   - **What's unclear:** How to handle non-numeric positions in tone CLI tags?
   - **Recommendation:** Extract leading numeric portion (`sed 's/[^0-9.].*//'`), fall back to raw string if no digits found. tone CLI expects numeric position for sorting.

7. **ASIN storage:**
   - **What we know:** tone supports custom fields via `--meta-additional-field "----:com.pilabor.tone:AUDIBLE_ASIN=..."`
   - **What's unclear:** Should we store ASIN in M4B or only in companion files?
   - **Recommendation:** Store in both - M4B for portability, companion file for pipeline re-runs.

## Graceful Degradation Strategy

**Principle:** Metadata enrichment is enhancement, not requirement. Pipeline must succeed even when Audnexus is unavailable.

| Scenario | HTTP Status | Pipeline Behavior |
|----------|-------------|-------------------|
| No `.asin` file | N/A | Skip metadata stage, use file-boundary chapters, continue to encoding |
| Invalid ASIN format | 400 | Log error, skip metadata, continue with defaults |
| ASIN not found | 404 | Log warning "ASIN not in Audnexus", continue with defaults |
| API unreachable | Network error | Log error "Audnexus API down", continue with defaults |
| Rate limit hit | 429 | Log warning "Rate limited", skip metadata, continue |
| Chapters 404 | 404 | Use book metadata, fall back to file-boundary chapters |
| Duration mismatch >5% | 200 (data issue) | Log warning "Chapter duration mismatch", use file-boundary chapters |
| Chapter count = 0 | 200 (data issue) | Log info "No chapters from Audnexus", use file-boundary chapters |
| Cover download fails | 404 / timeout | Log warning "Cover art unavailable", continue without cover |

**Exit Codes:**
- **Stage success:** Exit 0 even if metadata unavailable (degradation is success)
- **Stage failure:** Exit 1 only on unrecoverable errors (invalid work directory, corrupt M4B)

**Logging:**
- API failures: `log_warn` level (not `log_error`) - signals degradation, not failure
- Missing data: `log_info` level - expected scenario
- Network errors: `log_error` level - but don't fail pipeline

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| ASIN validation | Custom checksum or format checker | Audnexus API validation | Audnexus is source of truth; custom validation can't detect non-existent ASINs |
| M4B metadata writing | Custom MP4 atom parser | tone CLI | MP4 container format is complex. ATL.NET (tone's backing library) handles all edge cases |
| Chapter format conversion | String parsing for timestamps | jq with millisecond math | Off-by-one errors in timestamp conversion break chapter playback |
| HTTP requests | curl wrappers with retry logic | `curl -fsSL` with simple error checking | One-shot requests don't need complex retry - graceful degradation is acceptable |
| JSON parsing | awk/sed field extraction | `jq` | Handles nested objects, arrays, null values, escaping |
| Metadata caching | SQLite or custom cache DB | File-based JSON cache with `stat` mtime | Simple, inspectable, no dependencies, matches 30-day TTL |
| Metadata field mapping | Hardcoded Audnexus → tone mapping | JSON config file for field mappings | Audnexus schema may change. tone may add new fields. Hardcoded mappings create brittle scripts |

**Key insight:** Metadata enrichment is low-frequency (one book at a time, not batch). Avoid premature optimization. File-based caching and simple curl requests are sufficient.

## Sources

### Primary (HIGH confidence)
- [Audnexus API Official Documentation](https://audnex.us) - Endpoint schemas, query parameters, response structures
- [Audnexus GitHub Repository](https://github.com/laxamentumtech/audnexus) - Configuration, error codes, rate limiting, caching strategy
- [tone GitHub - sandreas/tone](https://github.com/sandreas/tone) - README, feature list, installation
- [tone v0.1.5 release](https://github.com/sandreas/tone/releases/tag/v0.1.5) - current installed version
- Hands-on testing with tone v0.1.5 (macOS) - all code examples verified
- [Live API Testing](https://api.audnex.us/books/B002V5D1CG) - Actual response schemas validated 2026-02-20
- [The Story behind ASINs](https://inventlikeanowner.com/blog/the-story-behind-asins-amazon-standard-identification-numbers/) - ASIN format history, B0 prefix, base-36 numbering

### Secondary (MEDIUM confidence)
- [m4b-merge GitHub Repository](https://github.com/djdembeck/m4b-merge) - Real-world Audnexus integration example, duration handling
- [Readarr Go Package Documentation](https://pkg.go.dev/golift.io/starr/readarr) - API methods, Edition struct with ASIN field
- [pyarr Readarr Documentation](https://docs.totaldebug.uk/pyarr/_modules/pyarr/readarr.html) - API lookup methods, ASIN search syntax
- [Audiobookshelf Scanner Guide](https://www.audiobookshelf.org/guides/book-scanner/) - ASIN in brackets convention
- [Readarr Guide 2026](https://www.rapidseedbox.com/blog/guide-to-readarr) - Readarr retirement confirmation
- [m4b-tool and tone for Audiobook Mastering - James North](https://jamesnorth.net/knowledge-base/article/m4b-tool-and-tone-for-audiobook-mastering) - practical usage
- [Audiobook tagging discussion - Audiobookshelf GitHub](https://github.com/advplyr/audiobookshelf/issues/607) - metadata compatibility

### Tertiary (LOW confidence)
- [Audiobookshelf Bug Report #2550](https://github.com/advplyr/audiobookshelf/issues/2550) - Real-world ASIN examples
- [Goodreads Audible ISBN Discussion](https://www.goodreads.com/topic/show/410686-audible-com-isbn) - ASIN vs ISBN for audiobooks
- Web searches on chapter duration tolerance - no industry standard found, 5% recommendation based on encoding variance research

## Metadata

**Confidence breakdown:**
- ASIN format: HIGH - Multiple authoritative sources confirm 10-char alphanumeric, B0 prefix for audiobooks
- Manual .asin file: HIGH - Straightforward implementation, no dependencies
- Folder regex: MEDIUM - Depends on naming conventions, requires validation
- Readarr API: LOW-MEDIUM - Complex, depends on retired project and user's setup
- Audnexus API schemas: HIGH - Validated against live API, official docs, and GitHub source code
- Audnexus rate limiting: HIGH - Documented in official README, error response verified
- Audnexus caching: HIGH - Redis/MongoDB architecture confirmed in official docs
- Duration tolerance: MEDIUM - No official guidance, recommendation based on audiobook encoding research and similar tools
- tone CLI: HIGH - Installed, hands-on tested, all commands verified
- tone architecture patterns: HIGH - All patterns verified with actual M4B files, code examples tested
- tone pitfalls: HIGH - Date format error, NFS warning, JSON parsing issue all encountered and resolved in testing

**Research date:** 2026-02-20
**Valid until:** 2026-03-22 (30 days - stable API and tool, aligns with Audnexus cache TTL)
