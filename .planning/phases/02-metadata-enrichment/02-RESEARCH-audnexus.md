# Phase 2: Metadata Enrichment - Audnexus API Research

**Researched:** 2026-02-20
**Domain:** Audnexus REST API integration, audiobook metadata aggregation
**Confidence:** HIGH

## Summary

Audnexus (https://api.audnex.us) is an open-source audiobook metadata aggregation API that harmonizes data from Audible and other sources into a consistent JSON API. It provides two primary endpoints for audiobook enrichment: `/books/{asin}` for metadata (title, authors, narrators, series, genres, cover art, runtime) and `/books/{asin}/chapters` for millisecond-precision chapter timestamps.

The API is **unauthenticated for basic read operations**, rate-limited to **100 requests/minute** (default, configurable by deployment), and uses **Redis caching** on the backend. It returns structured JSON with predictable schemas and supports graceful degradation when data is unavailable.

**Primary recommendation:** Use Audnexus as the primary metadata source with curl-based HTTP requests. Cache responses locally (file-based JSON cache in work directory). Implement 5% duration tolerance for chapter matching. Fall back to file-boundary chapters when API is unreachable or chapter count/duration mismatches exceed tolerance.

## API Endpoints

### GET /books/{asin}

**Purpose:** Retrieve comprehensive book metadata

**URL Format:**
```
https://api.audnex.us/books/{asin}?region=us&seedAuthors=0&update=0
```

**Query Parameters:**
| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `region` | string | `us` | Geographic region code (us, uk, au, ca, de, es, fr, in, it, jp) |
| `seedAuthors` | 0 or 1 | 0 | Whether to populate detailed author information |
| `update` | 0 or 1 | 0 | Force upstream check for fresh data (bypasses cache) |

**Response Schema (200 OK):**
```json
{
  "asin": "B002V5D1CG",
  "isbn": "string or null",
  "title": "Book Title",
  "subtitle": "string or null",
  "copyright": 2024,
  "releaseDate": "2024-01-15",
  "description": "Full HTML description",
  "summary": "Plain text summary",
  "formatType": "unabridged",
  "literatureType": "fiction",
  "language": "english",
  "publisherName": "Publisher Name",
  "authors": [
    {
      "asin": "B001ABC123",
      "name": "Author Name"
    }
  ],
  "narrators": [
    "Narrator Name"
  ],
  "genres": [
    {
      "asin": "18580606011",
      "name": "Science Fiction",
      "type": "genre"
    }
  ],
  "seriesPrimary": {
    "asin": "B01ABC123",
    "name": "Series Name",
    "position": "1"
  },
  "image": "https://m.media-amazon.com/images/I/51ABC123._SL500_.jpg",
  "runtimeLengthMin": 720,
  "rating": "4.5",
  "isAdult": false,
  "region": "us"
}
```

**Key Fields for FR-META-01:**
- `title` - Book title (required)
- `authors[].name` - Author name(s) (array, required)
- `narrators[]` - Narrator name(s) (array, required)
- `seriesPrimary.name` - Series name (optional)
- `seriesPrimary.position` - Series position (optional)
- `copyright` - Year (fallback to `releaseDate` year)
- `description` - Full description (HTML, strip tags for `desc.txt`)
- `genres[].name` - Genre tags (array)
- `image` - Cover art URL (JPEG, typically 500x500, can be up to 3200x3200)
- `runtimeLengthMin` - Total runtime in minutes (for duration matching)

**Error Responses:**
- `400 Bad Request` - Invalid ASIN format (not 10 alphanumeric characters)
- `404 Not Found` - Book not in Audnexus database (ASIN unknown)
- `429 Rate Limit Exceeded` - Too many requests (includes `retryAfterSeconds` field)
- `500 Internal Server Error` - Upstream aggregation failure

### GET /books/{asin}/chapters

**Purpose:** Retrieve millisecond-precision chapter timestamps

**URL Format:**
```
https://api.audnex.us/books/{asin}/chapters?region=us&update=0
```

**Query Parameters:**
| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `region` | string | `us` | Geographic region code |
| `update` | 0 or 1 | 0 | Force upstream check for fresh data |

**Response Schema (200 OK):**
```json
{
  "asin": "B002V5D1CG",
  "brandIntroDurationMs": 2043,
  "brandOutroDurationMs": 5045,
  "chapters": [
    {
      "lengthMs": 2710976,
      "startOffsetMs": 0,
      "startOffsetSec": 0,
      "title": "Opening Credits"
    },
    {
      "lengthMs": 2654321,
      "startOffsetMs": 2710976,
      "startOffsetSec": 2710.976,
      "title": "Chapter 1"
    }
  ],
  "isAccurate": true,
  "runtimeLengthMs": 43200000,
  "runtimeLengthSec": 43200
}
```

**Key Fields for FR-CHAP-02:**
- `chapters[]` - Array of chapter objects (ordered)
- `chapters[].lengthMs` - Chapter duration in milliseconds
- `chapters[].startOffsetMs` - Chapter start time in milliseconds (matches FFMETADATA1 `START` field)
- `chapters[].title` - Chapter title (sanitize via `sanitize_chapter_title()`)
- `runtimeLengthMs` - Total runtime in milliseconds (for duration matching)
- `isAccurate` - Boolean flag indicating chapter accuracy (honor when `false`)

**Error Responses:**
- `400 Bad Request` - Invalid ASIN format
- `404 Not Found` - Chapters unavailable (book exists but no chapter data)
- `429 Rate Limit Exceeded` - Rate limit hit

**Important:** Chapter endpoint requires Audible API credentials (`ADP_TOKEN`, `PRIVATE_KEY`) on the server side. Public Audnexus instance at api.audnex.us has this configured.

## Duration Matching Strategy

**Problem:** Audnexus chapter data comes from Audible's original AAX release. Our M4B is transcoded from MP3s. Duration mismatches can occur due to:
- Encoding differences (AAC vs MP3 vs AAX)
- Silence trimming during conversion
- Rounding errors in chapter boundary detection
- Different source materials (MP3s from different releases)

**Recommended Tolerance:** 5% duration difference

**Matching Algorithm:**
1. Get M4B duration via `get_duration()` (in seconds, float)
2. Get Audnexus `runtimeLengthMs` and convert to seconds (`runtimeLengthMs / 1000`)
3. Calculate percentage difference: `abs(m4b_duration - audnexus_duration) / audnexus_duration * 100`
4. If difference <= 5%: SAFE to import Audnexus chapters
5. If difference > 5%: WARN and fall back to file-boundary chapters

**Why 5%?**
- 720-minute audiobook: 5% = 36 minutes tolerance (reasonable for different encodings)
- 360-minute audiobook: 5% = 18 minutes tolerance
- 60-minute audiobook: 5% = 3 minutes tolerance
- Allows for intro/outro differences, encoding variance, and silence trimming
- More conservative than 10% (too loose), stricter than 2% (too tight for transcoded MP3s)

**Edge Cases:**
- **No chapters in response:** Fall back to file-boundary chapters (not an error)
- **Chapter count mismatch:** Compare count but don't fail - Audnexus may combine/split differently than file boundaries
- **`isAccurate: false`:** Log warning but still use chapters if duration matches (flag indicates potential inaccuracy, not guaranteed failure)
- **Negative `startOffsetMs`:** Reject entire chapter set (data corruption)
- **Overlapping chapters:** Reject entire chapter set (validation failure)

## Rate Limiting

**Default Limit:** 100 requests per minute per source (configurable via `MAX_REQUESTS` env var on Audnexus server)

**Error Response (429):**
```json
{
  "error": "RATE_LIMIT_EXCEEDED",
  "message": "Too many requests",
  "statusCode": 429,
  "retryAfterSeconds": 60
}
```

**Client-Side Strategy:**
- **No pre-emptive rate limiting needed** - 100 req/min is generous for our use case (one book at a time)
- **On 429 error:** Log warning, skip metadata enrichment for current book, continue pipeline
- **Do NOT retry automatically** - graceful degradation is acceptable
- **Future enhancement:** If batch processing is added (Phase 4 concurrency), implement exponential backoff with `retryAfterSeconds` hint

## Caching Strategy

### Backend Caching (Audnexus Server)

Audnexus uses **Redis** for hot caching with MongoDB for persistent storage:
- First request for an ASIN fetches from upstream (Audible), stores in cache
- Subsequent requests served from Redis (millisecond response times)
- `update=1` query parameter forces cache bypass and upstream refresh
- Default cache TTL: 30 days (configurable via `UPDATE_INTERVAL`)

**Do NOT use `update=1` by default** - it increases load on Audible and defeats caching benefits.

### Client-Side Caching (Our Implementation)

**Recommendation:** Local file-based JSON cache in work directory

**Cache Structure:**
```
$WORK_DIR/
├── audnexus_book_{asin}.json      # Book metadata cache
└── audnexus_chapters_{asin}.json  # Chapter data cache
```

**Cache Strategy:**
1. Before API call, check if `audnexus_book_{asin}.json` exists in `$WORK_DIR`
2. If exists and modified within 30 days: use cached JSON (no API call)
3. If missing or stale (>30 days): fetch from API, write to cache file
4. **Never cache error responses** - only cache successful 200 responses

**Cache Invalidation:**
- Manual: Delete `audnexus_*.json` files from work directory
- Automatic: 30-day TTL based on file modification time
- Force refresh: Add `--force-metadata` flag to pipeline (skips cache, re-fetches)

**Benefits:**
- Eliminates redundant API calls when re-processing same book
- Enables offline re-runs (as long as cache exists)
- Faster pipeline execution for subsequent runs
- Respects Audnexus backend caching (30-day TTL alignment)

## Cover Art Handling

**Image URL Format:**
```
https://m.media-amazon.com/images/I/{image_id}._SL500_.jpg
```

**Resolution:**
- Default from API: `_SL500_` (500x500 pixels)
- Available resolutions: Replace `500` with desired dimension (500, 1000, 2000, 3200)
- **Recommendation:** Use `_SL2000_.jpg` for high-quality embed (balances quality vs file size)

**Download Strategy:**
1. Extract `image` URL from `/books/{asin}` response
2. Modify URL: `s/_SL500_/_SL2000_/` for higher resolution
3. Download with `curl -fsSL -o "$WORK_DIR/cover.jpg" "$image_url"`
4. Verify download: Check file size > 0 and magic bytes (JPEG: `FF D8 FF`)
5. Embed in M4B via tone CLI (FR-META-02)
6. Copy to output directory as `cover.jpg` (FR-META-03)

**Error Handling:**
- 404 on image URL: Log warning, continue without cover (not fatal)
- Network timeout: Retry once, then skip cover (not fatal)
- Invalid image format: Skip embed, log warning

## Graceful Degradation

**Principle:** Metadata enrichment is enhancement, not requirement. Pipeline must succeed even when Audnexus is unavailable.

**Failure Scenarios & Responses:**

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
| HTTP requests | curl wrappers with retry logic | `curl -fsSL` with simple error checking | One-shot requests don't need complex retry - graceful degradation is acceptable |
| JSON parsing | awk/sed field extraction | `jq` (already in stack) | Handles nested objects, arrays, null values, escaping |
| Duration parsing | bc-based math with rounding | `bc` with explicit precision control | Already used in phase 1, consistent with `get_duration()` |
| ASIN validation | Custom regex | Simple length check + alphanumeric test | ASIN format is stable (10 chars, alphanumeric) |
| Caching layer | SQLite or custom cache DB | File-based JSON cache with `stat` mtime | Simple, inspectable, no dependencies, matches 30-day TTL |

**Key insight:** Metadata enrichment is low-frequency (one book at a time, not batch). Avoid premature optimization. File-based caching and simple curl requests are sufficient. Complex retry logic, connection pooling, or database caching would add complexity without meaningful benefit at this scale.

## Code Examples

### Fetch Book Metadata with Cache

```bash
#!/usr/bin/env bash
# Fetch book metadata from Audnexus with local caching

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

  # Cache successful response
  echo "$response" > "$cache_file"
  echo "$response"
  return 0
}
```

### Duration Matching Validation

```bash
#!/usr/bin/env bash
# Validate Audnexus chapters match M4B duration within 5% tolerance

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

### Extract and Download Cover Art

```bash
#!/usr/bin/env bash
# Extract cover URL from Audnexus response and download high-res version

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

### Parse Audnexus Chapters to FFMETADATA1

```bash
#!/usr/bin/env bash
# Convert Audnexus chapters JSON to FFMETADATA1 format

audnexus_chapters_to_ffmetadata() {
  local chapters_json="$1"
  local output_file="$2"

  # Extract chapters array and convert to FFMETADATA1
  echo "$chapters_json" | jq -r '.chapters[] |
    "\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=\(.startOffsetMs)\nEND=\(.startOffsetMs + .lengthMs)\ntitle=\(.title)"
  ' >> "$output_file"

  local chapter_count
  chapter_count=$(echo "$chapters_json" | jq '.chapters | length')

  log_info "Wrote $chapter_count Audnexus chapters to $output_file"
  return 0
}
```

## Common Pitfalls

### Pitfall 1: Assuming 404 Means API Failure
**What goes wrong:** Pipeline exits with error when ASIN is not found (404), treating it as fatal failure.
**Why it happens:** Confusion between "API unreachable" (network error) vs "data not found" (404 response).
**How to avoid:** Check HTTP status code - 404 is successful response, just means "not in database". Only fail on network errors (curl exit code != 0 with no HTTP response).
**Warning signs:** Pipeline fails on valid ASINs that aren't in Audnexus database yet.

### Pitfall 2: Trusting Chapter Count Over Duration
**What goes wrong:** Accepting Audnexus chapters because count matches file count, but duration is wildly different (e.g., 10 hours vs 8 hours).
**Why it happens:** Chapter count can coincidentally match even when source material differs (different editions, intro/outro differences).
**How to avoid:** Always validate total duration first (5% tolerance), then check chapter count as secondary validation.
**Warning signs:** Chapters are present but timestamps don't align with actual audio content.

### Pitfall 3: Caching Error Responses
**What goes wrong:** 404 or 429 responses get cached, preventing future successful lookups.
**Why it happens:** Cache logic writes response regardless of HTTP status.
**How to avoid:** Only cache successful 200 responses. Check HTTP status before writing cache file.
**Warning signs:** Book metadata never loads even after ASIN is added to Audnexus.

### Pitfall 4: Not Sanitizing Chapter Titles
**What goes wrong:** Audnexus chapter titles contain characters that break FFMETADATA1 format (newlines, special chars, control characters).
**Why it happens:** Upstream Audible data may include formatting or special characters.
**How to avoid:** Run all chapter titles through `sanitize_chapter_title()` function (already exists in lib/sanitize.sh from Phase 1).
**Warning signs:** FFmpeg errors when reading metadata file, garbled chapter titles in output.

### Pitfall 5: Hardcoding Image Resolution
**What goes wrong:** Always downloading `_SL500_` covers, resulting in low-resolution embeds.
**Why it happens:** Using image URL from API response without modification.
**How to avoid:** Modify URL to use `_SL2000_` for high-res (or `_SL3200_` for maximum quality). Fall back to original resolution only if high-res 404s.
**Warning signs:** Cover art in M4B appears pixelated or blurry.

### Pitfall 6: Blocking on Rate Limits
**What goes wrong:** Pipeline waits/retries when hitting 429, blocking for minutes.
**Why it happens:** Treating rate limits like temporary network errors requiring retry.
**How to avoid:** Graceful degradation - log warning and continue without metadata. Rate limits indicate systemic issue (too many concurrent pipelines or misconfigured deployment).
**Warning signs:** Pipeline hangs or has multi-minute delays during metadata stage.

### Pitfall 7: Ignoring `isAccurate: false`
**What goes wrong:** Blindly trusting chapter data even when Audnexus flags it as potentially inaccurate.
**Why it happens:** Not checking the `isAccurate` field in chapters response.
**How to avoid:** Log warning when `isAccurate: false`, increase scrutiny on duration validation (reduce tolerance to 3% instead of 5%).
**Warning signs:** User reports chapters in wrong positions or cut off mid-sentence.

## Open Questions

1. **Multi-region support**
   - What we know: API supports `region` parameter (us, uk, au, ca, de, es, fr, in, it, jp)
   - What's unclear: How to determine correct region for a given ASIN? Try `us` first and fall back to other regions on 404?
   - Recommendation: Default to `region=us` for Phase 2. Add multi-region retry in future enhancement if users report missing ASINs.

2. **Chapter validation edge cases**
   - What we know: `isAccurate` flag exists, duration matching catches major mismatches
   - What's unclear: Should we validate chapter ordering (monotonic `startOffsetMs`)? Validate no overlaps?
   - Recommendation: Add basic validation - reject if `startOffsetMs` is not strictly increasing. Log error and fall back to file-boundary chapters.

3. **Companion file encoding**
   - What we know: `desc.txt` and `reader.txt` should be created (FR-META-04)
   - What's unclear: Should description be plain text (strip HTML) or preserve HTML formatting? UTF-8 encoding assumed?
   - Recommendation: Strip HTML tags from description using `sed 's/<[^>]*>//g'`, write UTF-8 plain text. Plex agents prefer plain text.

4. **Series position format**
   - What we know: `seriesPrimary.position` can be string like "1", "1.5", "Book 1", etc.
   - What's unclear: How to handle non-numeric positions in tone CLI tags?
   - Recommendation: Extract leading numeric portion (`sed 's/[^0-9.].*//'`), fall back to raw string if no digits found. tone CLI expects numeric position for sorting.

## Sources

### Primary (HIGH confidence)
- [Audnexus API Official Documentation](https://audnex.us) - Endpoint schemas, query parameters, response structures
- [Audnexus GitHub Repository](https://github.com/laxamentumtech/audnexus) - Configuration, error codes, rate limiting, caching strategy
- [Live API Testing](https://api.audnex.us/books/B002V5D1CG) - Actual response schemas validated 2026-02-20

### Secondary (MEDIUM confidence)
- [m4b-merge GitHub](https://github.com/djdembeck/m4b-merge) - Real-world Audnexus integration example, duration handling
- [Audnexus.bundle GitHub](https://github.com/djdembeck/Audnexus.bundle) - Plex plugin implementation patterns

### Tertiary (LOW confidence)
- Web searches on chapter duration tolerance - no industry standard found, 5% recommendation based on encoding variance research
- Community discussions on Audnexus caching - verified against official docs

## Metadata

**Confidence breakdown:**
- API schemas: HIGH - Validated against live API, official docs, and GitHub source code
- Rate limiting: HIGH - Documented in official README, error response verified
- Caching strategy: HIGH - Redis/MongoDB architecture confirmed in official docs
- Duration tolerance: MEDIUM - No official guidance, recommendation based on audiobook encoding research and similar tools
- Chapter validation: MEDIUM - `isAccurate` field documented, but validation rules inferred from common practices

**Research date:** 2026-02-20
**Valid until:** 2026-03-22 (30 days for stable API, aligns with Audnexus cache TTL)
