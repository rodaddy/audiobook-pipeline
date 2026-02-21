# Phase 2: Metadata Enrichment - ASIN Discovery Research

**Researched:** 2026-02-20
**Domain:** ASIN extraction and validation for audiobook metadata enrichment
**Confidence:** MEDIUM-HIGH

## Summary

ASIN (Amazon Standard Identification Number) discovery is the critical first step for Phase 2 metadata enrichment. ASINs are 10-character alphanumeric identifiers that uniquely identify audiobooks in the Audible/Amazon ecosystem. Since the Audnexus API requires an ASIN for metadata lookup and has no text search capability, reliable ASIN discovery is essential for automation.

The research identifies four ASIN discovery methods with a clear priority order: (1) manual `.asin` file override (highest priority, P0 requirement), (2) folder/filename regex extraction (P1), (3) Readarr/Bookshelf API query (P1, but complicated by Readarr's retirement), and (4) user prompt as fallback. Each method has different reliability characteristics and implementation complexity.

**Primary recommendation:** Implement manual `.asin` file first (simplest, most reliable), then add folder regex parsing. Defer Readarr API integration until user validates the need -- Readarr is officially retired as of 2026, and the user may not be using it or its forks.

## ASIN Format Specification

### Standard Format

ASINs follow a consistent pattern for audiobooks and digital products:

| Attribute | Value |
|-----------|-------|
| **Length** | Exactly 10 characters |
| **Character set** | Alphanumeric (A-Z, 0-9) base-36 |
| **Common prefix** | `B0` for most audiobooks and digital products |
| **Starting character** | Books with ISBNs use the ISBN-10 as ASIN (starts with digit); non-ISBN products start with `B` |
| **Case** | Uppercase by convention |

**Examples of valid audiobook ASINs:**
- `B017V4U2VQ` - Standard B0 pattern
- `B002V0QK4C` - "Wizard's First Rule" by Terry Goodkind
- `B00B5HZGUG` - "The Martian" audiobook
- `B0CKM5YQX8` - Recent release (2024+)
- `B00JCDK5ME` - Used in Readarr API examples

### Validation Regex

**Recommended pattern for audiobooks:**
```bash
^B0[A-Z0-9]{8}$
```

This matches the most common audiobook ASIN format (B0 prefix + 8 alphanumeric chars).

**Broader pattern (includes all ASIN types):**
```bash
^[A-Z0-9]{10}$
```

This matches ISBN-10 ASINs (for print books) and all digital product ASINs.

**Implementation note:** Use the B0 pattern first for higher confidence. If that fails, fall back to the broader pattern and validate against Audnexus API.

### Edge Cases

- **Multiple ASINs per title:** Audiobooks may have different ASINs for different regions (US vs UK), formats (single-narrator vs full-cast), or editions (abridged vs unabridged). The pipeline should use the first valid ASIN found.
- **ISBN-10 as ASIN:** Physical books use their ISBN-10 as the ASIN (10 digits starting with 0-9). These are unlikely for audiobooks but may appear if metadata is mixed.
- **Invalid/malformed ASINs:** Folder names may contain patterns that look like ASINs but are not. Always validate against Audnexus API before using.

## ASIN Discovery Methods

### Priority 1: Manual `.asin` File (FR-ASIN-03)

**What it is:** A plain text file placed alongside the audiobook containing only the ASIN.

**Format:**
```
B00JCDK5ME
```

**Location:** Same directory as the audiobook source files (e.g., `/path/to/Book Name/.asin`)

**Implementation:**
```bash
if [[ -f "$SOURCE_DIR/.asin" ]]; then
  ASIN=$(cat "$SOURCE_DIR/.asin" | tr -d '[:space:]')
  # Validate format
  if [[ "$ASIN" =~ ^B0[A-Z0-9]{8}$ ]]; then
    log_info "ASIN from manual .asin file: $ASIN"
  else
    log_warn "Invalid ASIN format in .asin file: $ASIN"
    ASIN=""
  fi
fi
```

**Confidence:** HIGH - This is the user's explicit override. No ambiguity.

**Priority justification:** FR-ASIN-03 is P0 (highest priority). This is the reliable fallback when auto-detection fails. Users can manually add `.asin` files for problematic books.

**Edge cases:**
- File contains whitespace, newlines, or multiple lines -- trim and take first line
- File contains invalid ASIN -- log warning and fall through to next method
- File is empty -- treat as missing

### Priority 2: Folder/Filename Regex Extraction (FR-ASIN-02)

**What it is:** Parse the ASIN from the audiobook's folder name using pattern matching.

**Common naming conventions from download sources:**

| Pattern | Example | Regex |
|---------|---------|-------|
| Brackets at end | `Book Title [B00JCDK5ME]` | `\[([A-Z0-9]{10})\]` |
| Brackets anywhere | `Author - Title [B00G3L6JMS] (2023)` | `\[([A-Z0-9]{10})\]` |
| ASIN prefix | `B00G3L6JMS - Book Title` | `^([A-Z0-9]{10}) - ` |
| Parentheses | `Book Title (B00JCDK5ME)` | `\(([A-Z0-9]{10})\)` |

**Recommended approach:** Use multiple regex patterns in priority order. Match against folder name first, then filename if folder fails.

**Implementation strategy:**
```bash
# Try multiple patterns in order
patterns=(
  '\[([A-Z0-9]{10})\]'        # [ASIN] brackets
  '\(([A-Z0-9]{10})\)'        # (ASIN) parentheses
  '^([A-Z0-9]{10})\s*-'       # ASIN - Title
  '\s([A-Z0-9]{10})\s'        # space ASIN space
)

for pattern in "${patterns[@]}"; do
  if [[ "$FOLDER_NAME" =~ $pattern ]]; then
    ASIN="${BASH_REMATCH[1]}"
    # Further validate it starts with B0
    if [[ "$ASIN" =~ ^B0 ]]; then
      log_info "ASIN extracted from folder name: $ASIN (pattern: $pattern)"
      break
    fi
  fi
done
```

**Confidence:** MEDIUM - Depends on download source adhering to naming conventions. High false-positive risk (random 10-char strings).

**Validation required:** Always validate extracted ASIN against Audnexus API before trusting it. If API returns 404 or VALIDATION_ERROR, discard and try next method.

**Edge cases:**
- Folder contains multiple 10-char alphanumeric strings -- take first match, validate
- ASIN-like string is actually ISBN, date, or other metadata -- API validation will catch
- Folder name has been manually renamed by user -- may not follow conventions

### Priority 3: Readarr/Bookshelf API Query (FR-ASIN-01)

**What it is:** Query the Readarr or Bookshelf (Readarr fork) API to retrieve the book's metadata including ASIN.

**API Endpoint Structure:**

**Readarr/Bookshelf API:**
- `GET /api/v1/book` - List all books
- `GET /api/v1/book/{id}` - Get specific book by ID
- `GET /api/v1/search?term={query}` - Search by title, author, ISBN, ASIN
- `GET /api/v1/edition/{id}` - Get edition metadata (includes ASIN field)

**Edition JSON Response:**
```json
{
  "id": 12345,
  "bookId": 6789,
  "foreignEditionId": "goodreads-edition-id",
  "asin": "B00JCDK5ME",
  "isbn13": "9780123456789",
  "title": "Book Title",
  "format": "Audiobook",
  "monitored": true
}
```

**Implementation approach:**

1. **Book identification:** Derive book title from folder name using sanitize functions
2. **API query:** `GET /api/v1/search?term={sanitized_title}`
3. **Edition extraction:** Iterate through returned books, find edition with `format == "Audiobook"`
4. **ASIN extraction:** Pull `asin` field from edition metadata

**Authentication:** Readarr API requires `X-Api-Key` header (configured in Readarr settings).

**Example using curl:**
```bash
READARR_API_URL="http://localhost:8787/api/v1"
READARR_API_KEY="your-api-key-here"

# Search for book
curl -s -H "X-Api-Key: $READARR_API_KEY" \
  "$READARR_API_URL/search?term=The+Martian" \
  | jq -r '.[0].editions[] | select(.format == "Audiobook") | .asin'
```

**Confidence:** LOW-MEDIUM - High complexity, dependent on external service availability and user's Readarr setup.

**Critical caveat -- Readarr retirement:** As of 2026, the official Readarr project is retired. The development team stopped maintenance due to metadata issues. Community forks exist (Bookshelf by pennydreadful, others), but they may use different metadata sources (Goodreads, Hardcover, OpenLibrary) that may not include ASIN fields.

**Recommendation:** Defer this implementation until user confirms they need it. Ask: "Are you using Readarr or Bookshelf? Do you need automatic ASIN lookup from your library manager?" If yes, implement. If no, skip to save development time.

**Edge cases:**
- Readarr/Bookshelf not installed or not running -- detect via API health check, skip method
- API key missing or invalid -- fail gracefully, log warning, skip method
- Book exists in Readarr but edition has no ASIN -- fall through to next method
- Multiple editions returned -- prefer audiobook format, then most recent release date
- Readarr using non-ASIN metadata source (OpenLibrary, Hardcover) -- ASIN field may be null

### Priority 4: User Prompt Fallback

**What it is:** If all automated methods fail, prompt the user to manually enter the ASIN or skip metadata enrichment.

**When to use:** Only when `.asin` file missing, folder regex failed, and Readarr API unavailable or returned no ASIN.

**Implementation:**
```bash
if [[ -z "$ASIN" ]] && [[ "${INTERACTIVE:-false}" == "true" ]]; then
  echo "Unable to auto-detect ASIN for: $(basename "$SOURCE_DIR")" >&2
  echo "Enter ASIN (or press Enter to skip metadata): " >&2
  read -r ASIN
  ASIN=$(echo "$ASIN" | tr -d '[:space:]')
fi
```

**Confidence:** HIGH - User provides known-good ASIN.

**Non-interactive mode:** In automated pipeline (cron, webhook), skip this step. Log warning and continue without metadata enrichment.

## Audnexus API Validation

### Endpoint

`GET https://api.audnex.us/books/{ASIN}`

### Validation Workflow

```bash
validate_asin() {
  local asin="$1"
  local response
  local http_code

  response=$(curl -s -w "\n%{http_code}" "https://api.audnex.us/books/$asin")
  http_code=$(echo "$response" | tail -n1)

  case "$http_code" in
    200)
      log_info "ASIN validated: $asin"
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

### Error Responses

| HTTP Code | Error Code | Meaning | Action |
|-----------|------------|---------|--------|
| 422 | `VALIDATION_ERROR` | ASIN format invalid | Discard ASIN, try next method |
| 404 | `NOT_FOUND` | ASIN doesn't exist in database | Discard ASIN, try next method |
| 404 | `REGION_UNAVAILABLE` | Item exists but not in requested region | Try different region or discard |
| 400 | `CONTENT_TYPE_MISMATCH` | ASIN is for podcast, not book | Discard ASIN, try next method |

**Rate limiting:** Audnexus has no documented rate limits, but use caching to avoid redundant calls. Cache successful ASIN validations for 30 days.

**Graceful degradation:** If Audnexus API is completely unavailable (connection timeout, 500 error), log warning and continue pipeline without metadata enrichment. Don't block conversion for metadata failures.

## Architecture Patterns

### Priority Chain Pattern

Implement ASIN discovery as a chain of responsibility:

```bash
discover_asin() {
  local source_dir="$1"
  local asin=""

  # Priority 1: Manual .asin file
  asin=$(check_manual_asin_file "$source_dir")
  if [[ -n "$asin" ]] && validate_asin "$asin"; then
    echo "$asin"
    return 0
  fi

  # Priority 2: Folder name regex
  asin=$(extract_asin_from_folder "$source_dir")
  if [[ -n "$asin" ]] && validate_asin "$asin"; then
    echo "$asin"
    return 0
  fi

  # Priority 3: Readarr API (if configured)
  if [[ -n "${READARR_API_URL:-}" ]]; then
    asin=$(query_readarr_for_asin "$source_dir")
    if [[ -n "$asin" ]] && validate_asin "$asin"; then
      echo "$asin"
      return 0
    fi
  fi

  # Priority 4: User prompt (interactive only)
  if [[ "${INTERACTIVE:-false}" == "true" ]]; then
    asin=$(prompt_user_for_asin "$source_dir")
    if [[ -n "$asin" ]] && validate_asin "$asin"; then
      echo "$asin"
      return 0
    fi
  fi

  # All methods failed
  log_warn "No valid ASIN found for: $(basename "$source_dir")"
  return 1
}
```

### Caching Pattern

Cache validated ASINs by book hash to avoid redundant API calls:

```bash
ASIN_CACHE_DIR="/var/lib/audiobook-pipeline/asin-cache"

cache_asin() {
  local book_hash="$1"
  local asin="$2"
  echo "$asin" > "$ASIN_CACHE_DIR/$book_hash.asin"
}

get_cached_asin() {
  local book_hash="$1"
  local cache_file="$ASIN_CACHE_DIR/$book_hash.asin"

  if [[ -f "$cache_file" ]]; then
    cat "$cache_file"
    return 0
  fi

  return 1
}
```

Cache invalidation: 30 days or manual purge. ASIN-to-metadata mapping is stable (Audible doesn't reassign ASINs).

## Common Pitfalls

### Pitfall 1: Trusting Unvalidated Regex Matches

**What goes wrong:** Folder names may contain 10-character alphanumeric strings that aren't ASINs (dates, random IDs, file hashes). Using these without validation results in failed API calls and no metadata.

**Why it happens:** Over-reliance on regex without validation. Assumption that folder names follow conventions.

**How to avoid:** Always validate regex-extracted ASINs against Audnexus API before trusting them. If API returns 404 or validation error, discard and try next method.

**Warning signs:** High rate of Audnexus 404 errors, books processed without metadata despite folder containing ASIN-like strings.

### Pitfall 2: Blocking on Readarr API Failures

**What goes wrong:** Pipeline times out or fails completely when Readarr API is unavailable, slow, or returns errors.

**Why it happens:** Synchronous API calls without timeouts, no graceful degradation.

**How to avoid:**
- Set short timeouts on Readarr API calls (5 seconds max)
- Catch all HTTP errors and log warnings instead of dying
- Fall through to next method on any Readarr failure
- Make Readarr integration optional (check if API URL is configured)

**Warning signs:** Pipeline hangs, "connection timeout" errors, pipeline fails when Readarr is restarting.

### Pitfall 3: Case-Sensitivity Mismatch

**What goes wrong:** ASIN extracted as lowercase from folder name (`b00jcdk5me`) fails Audnexus validation or lookup.

**Why it happens:** Some download tools or file managers convert folder names to lowercase. Audnexus may be case-sensitive.

**How to avoid:** Always uppercase ASIN strings before validation: `ASIN=$(echo "$ASIN" | tr '[:lower:]' '[:upper:]')`

**Warning signs:** Regex extracts valid-looking ASIN but Audnexus returns VALIDATION_ERROR.

### Pitfall 4: Race Condition with Manual .asin File

**What goes wrong:** User adds `.asin` file while pipeline is running. Pipeline uses stale ASIN from previous run or skips the file.

**Why it happens:** ASIN discovery happens at pipeline start, before user has chance to intervene.

**How to avoid:**
- Check for `.asin` file immediately before metadata stage, not at pipeline start
- Support `--force-metadata` flag to reprocess metadata even if already enriched
- Document workflow: "Add `.asin` file, then rerun with --force"

**Warning signs:** User reports "I added .asin file but pipeline didn't use it."

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| ASIN validation | Custom checksum or format checker | Audnexus API validation | Audnexus is source of truth; custom validation can't detect non-existent ASINs |
| Metadata caching | Custom cache implementation | Filesystem cache with book hash as key | Simple, debuggable, no database overhead |
| Folder name parsing | Single monolithic regex | Priority chain of patterns | Different sources use different conventions; single regex too brittle |

**Key insight:** ASIN discovery is inherently heuristic. Don't try to build a perfect detector -- build a priority chain with validation and user fallback.

## Open Questions

1. **Readarr usage:**
   - What we know: User's PROJECT.md mentions "Readarr is configured with root folder `/mnt/media/AudioBooks/_incoming`" but Readarr is officially retired
   - What's unclear: Is user actively using Readarr? A fork like Bookshelf? Neither?
   - Recommendation: Ask user before implementing Readarr API integration. If not using it, skip this method entirely.

2. **ASIN cache expiry:**
   - What we know: ASIN-to-metadata mapping is stable (Audible doesn't reassign ASINs)
   - What's unclear: How often to refresh cache? Never? On force flag?
   - Recommendation: Start with 30-day expiry. Add `--clear-asin-cache` flag for manual purge.

3. **Multiple ASIN handling:**
   - What we know: Books can have different ASINs for regions, formats, editions
   - What's unclear: What if folder contains multiple bracketed ASINs? Which to use?
   - Recommendation: Use first valid ASIN found (left-to-right). User can override with `.asin` file if needed.

4. **Interactive mode in automation:**
   - What we know: Phase 4 adds cron and webhook triggers (non-interactive)
   - What's unclear: Should pipeline block on missing ASIN in manual mode vs. skip in automated mode?
   - Recommendation: Add `INTERACTIVE` config flag. Default false for automation, true for manual CLI runs. Only prompt when true.

## Sources

### Primary (HIGH confidence)

- [Audnexus GitHub Repository](https://github.com/laxamentumtech/audnexus) - API structure, error codes, validation requirements
- [tone CLI GitHub Repository](https://github.com/sandreas/tone) - Custom ASIN field format, metadata handling
- [Readarr Go Package Documentation](https://pkg.go.dev/golift.io/starr/readarr) - API methods, Edition struct with ASIN field
- [The Story behind ASINs](https://inventlikeanowner.com/blog/the-story-behind-asins-amazon-standard-identification-numbers/) - ASIN format history, B0 prefix, base-36 numbering

### Secondary (MEDIUM confidence)

- [pyarr Readarr Documentation](https://docs.totaldebug.uk/pyarr/_modules/pyarr/readarr.html) - API lookup methods, ASIN search syntax
- [Audiobookshelf Scanner Guide](https://www.audiobookshelf.org/guides/book-scanner/) - ASIN in brackets convention `[B002UZJGYY]`
- [m4b-merge GitHub Repository](https://github.com/djdembeck/m4b-merge) - ASIN input workflow, Audnexus API usage
- [Readarr Guide 2026](https://www.rapidseedbox.com/blog/guide-to-readarr) - Readarr retirement confirmation

### Tertiary (LOW confidence)

- [Audiobookshelf Bug Report #2550](https://github.com/advplyr/audiobookshelf/issues/2550) - Real-world ASIN examples (B0CKM5YQX8, B00B5HZGUG)
- [Goodreads Audible ISBN Discussion](https://www.goodreads.com/topic/show/410686-audible-com-isbn) - ASIN vs ISBN for audiobooks

## Metadata

**Confidence breakdown:**
- ASIN format: HIGH - Multiple authoritative sources confirm 10-char alphanumeric, B0 prefix for audiobooks
- Manual .asin file: HIGH - Straightforward implementation, no dependencies
- Folder regex: MEDIUM - Depends on naming conventions, requires validation
- Readarr API: LOW-MEDIUM - Complex, depends on retired project and user's setup
- Audnexus validation: HIGH - Official API, documented error codes

**Research date:** 2026-02-20
**Valid until:** 2026-03-22 (30 days - ASIN format is stable, but ecosystem tools evolve)
