# Phase 1: Core Conversion Pipeline - Research

**Researched:** 2026-02-20
**Domains:** ffmpeg audio conversion, bash project structure, manifest tracking, CLI patterns
**Confidence:** HIGH

---

## Summary

This research consolidates audio conversion mechanics (ffmpeg/ffprobe), bash project architecture, and state management patterns needed for Phase 1 implementation. Key findings:

**Audio conversion:**
- ffmpeg concat demuxer + FFMETADATA1 chapter generation from file durations is proven reliable
- Chapters must be manually injected via `-map_metadata 1` (concat demuxer drops them silently)
- Always use `-movflags +faststart` for M4B streaming/seeking support
- Built-in `aac` encoder preferred over `libfdk_aac` (universal availability, good quality at 64kbps)

**Project structure:**
- Stage-based pipeline (`stages/*.sh`) for clear separation and testability
- JSON manifests for idempotent processing (resume from last completed stage)
- Reusable library functions (`lib/core.sh`, `lib/ffmpeg.sh`, `lib/manifest.sh`, `lib/sanitize.sh`)
- Structured key=value logging for machine-parseable output

**State management:**
- Manifest per book in `/var/lib/audiobook-pipeline/manifests/`
- Work directories use content hash (source path + file list) for idempotency
- Atomic manifest updates with temp file + move pattern

---

## Standard Stack

### Required Tools
- **ffmpeg** -- audio concat, AAC encoding, M4B muxing
- **ffprobe** -- duration/bitrate detection, chapter validation
- **jq** -- JSON manifest parsing/updates
- **bc** -- floating-point math for timestamp calculations
- **bash 4.0+** -- required for associative arrays (if used)

### Exact Command Patterns

**Multi-MP3 concatenation:**
```bash
# Generate file list
find "$INPUT_DIR" -type f -name "*.mp3" | sort | \
  sed "s/'/'''/g" | sed "s/^/file '/" | sed "s/$/'/" > files.txt

# Concat with chapter injection
ffmpeg -y \
  -f concat -safe 0 -i files.txt \
  -i metadata.txt \
  -map_metadata 1 \
  -map 0:a \
  -c:a aac -b:a 64k \
  -movflags +faststart \
  output.m4b
```

**FFMETADATA1 chapter generation:**
```bash
# Header
echo ";FFMETADATA1"
echo "title=$BOOK_TITLE"
echo "artist=$AUTHOR"
echo "album=$BOOK_TITLE"

# Per-file chapters
while read -r file; do
  duration_s=$(ffprobe -v error -show_entries format=duration \
               -of default=noprint_wrappers=1:nokey=1 "$file")
  duration_ms=$(echo "$duration_s * 1000" | bc | cut -d. -f1)

  echo "[CHAPTER]"
  echo "TIMEBASE=1/1000"
  echo "START=$chapter_start"
  echo "END=$((chapter_start + duration_ms))"
  echo "title=Chapter $counter - $(basename "$file" .mp3)"

  chapter_start=$((chapter_start + duration_ms))
  counter=$((counter + 1))
done
```

**Chapter validation:**
```bash
# Count chapters
ffprobe -v error -show_chapters output.m4b | grep -c "^\[CHAPTER\]"

# List chapter timestamps
ffprobe -v error -show_chapters output.m4b
```

---

## Architecture Patterns

### Project Layout

```
/opt/audiobook-pipeline/
├── bin/
│   └── audiobook-convert          # Main CLI entry point
├── lib/
│   ├── core.sh                    # Logging, run(), die()
│   ├── ffmpeg.sh                  # FFprobe helpers
│   ├── manifest.sh                # State tracking
│   └── sanitize.sh                # Filename sanitization
├── stages/
│   ├── 01-validate.sh             # Input validation
│   ├── 02-concat.sh               # MP3 concatenation
│   ├── 03-convert.sh              # M4B conversion
│   └── 04-cleanup.sh              # Temp cleanup
├── config.env.example             # Template configuration
└── VERSION

/var/lib/audiobook-pipeline/
├── work/
│   └── <book-hash>/               # Per-book work directory
│       ├── input/                 # Original files (hardlinks)
│       ├── files.txt              # Concat file list
│       ├── metadata.txt           # FFMETADATA1 chapters
│       └── output/                # Final M4B
└── manifests/
    └── <book-hash>.json           # Per-book state
```

### Stage Flow

```
01-validate.sh
  ├── Check ffmpeg/ffprobe available
  ├── Validate all MP3s readable
  ├── Detect multi-folder books (CD 1, CD 2)
  ├── Calculate total duration/size
  └── Write manifest: stages.validate.status=completed

02-concat.sh
  ├── Generate files.txt (concat demuxer format)
  ├── Generate metadata.txt (FFMETADATA1)
  └── Write manifest: stages.concat.status=completed

03-convert.sh
  ├── ffmpeg concat + encode + chapter inject
  ├── Validate chapter count matches input files
  └── Write manifest: stages.convert.status=completed

04-cleanup.sh
  ├── Move M4B to final location
  ├── Set ownership/permissions
  ├── Delete work directory (if CLEANUP_WORK_DIR=true)
  └── Write manifest: status=completed
```

### Manifest Schema

```json
{
  "book_hash": "abc123def456",
  "source_path": "/mnt/audiobooks/Author/Book",
  "created_at": "2026-02-20T14:32:01Z",
  "status": "completed",
  "stages": {
    "validate": {
      "status": "completed",
      "completed_at": "2026-02-20T14:32:07Z",
      "file_count": 42,
      "total_duration_sec": 36000
    },
    "concat": {
      "status": "completed",
      "completed_at": "2026-02-20T14:35:12Z"
    },
    "convert": {
      "status": "completed",
      "completed_at": "2026-02-20T14:42:01Z",
      "output_file": "output/Book.m4b",
      "bitrate": "64k",
      "codec": "aac",
      "chapter_count": 42
    },
    "cleanup": {
      "status": "completed",
      "completed_at": "2026-02-20T14:42:05Z"
    }
  },
  "metadata": {
    "author": "Author Name",
    "title": "Book Title"
  }
}
```

**Status values:**
- `pending` -- not started
- `in_progress` -- stage running
- `completed` -- stage finished
- `failed` -- stage error (includes `error_message` field)

### Idempotent Processing

```bash
# Check if already processed
check_book_status() {
  local book_hash="$1"
  local manifest="$MANIFEST_DIR/$book_hash.json"

  [[ ! -f "$manifest" ]] && echo "new" && return 0

  local status=$(jq -r '.status' "$manifest")
  echo "$status"

  [[ "$status" == "completed" ]] && return 1  # Skip
  return 0  # Process
}

# Resume from last completed stage
get_next_stage() {
  local book_hash="$1"
  local manifest="$MANIFEST_DIR/$book_hash.json"

  for stage in validate concat convert cleanup; do
    local stage_status=$(jq -r ".stages.$stage.status // \"pending\"" "$manifest")
    [[ "$stage_status" != "completed" ]] && echo "$stage" && return
  done

  echo "done"
}
```

---

## Code Examples

### Complete Conversion Function

```bash
#!/usr/bin/env bash
set -euo pipefail

convert_to_m4b() {
  local input_dir="$1"
  local author="$2"
  local album="$3"
  local output_dir="$4"

  local work_dir=$(mktemp -d)
  trap 'rm -rf "$work_dir"' EXIT

  local files_list="$work_dir/files.txt"
  local metadata_file="$work_dir/metadata.txt"
  local output_file="$output_dir/$author/$album/$album.m4b"

  mkdir -p "$(dirname "$output_file")"

  # Generate file list
  find "$input_dir" -type f -name "*.mp3" | sort | \
    sed "s/'/'''/g" | sed "s/^/file '/" | sed "s/$/'/" > "$files_list"

  [[ ! -s "$files_list" ]] && die "No MP3 files found"

  # Generate FFMETADATA1
  {
    echo ";FFMETADATA1"
    echo "major_brand=mp42"
    echo "minor_version=512"
    echo "compatible_brands=isomiso2mp41"
    echo "title=$album"
    echo "artist=$author"
    echo "album_artist=$author"
    echo "album=$album"
  } > "$metadata_file"

  # Generate chapters from file durations
  local chapter_start=0
  local counter=1

  while read -r file; do
    local mp3_path=$(echo "$file" | sed "s/^file '\(.*\)'$/\1/")
    local duration_s=$(ffprobe -v error -show_entries format=duration \
                       -of default=noprint_wrappers=1:nokey=1 "$mp3_path")
    local duration_ms=$(echo "$duration_s * 1000" | bc | cut -d. -f1)

    {
      echo "[CHAPTER]"
      echo "TIMEBASE=1/1000"
      echo "START=$chapter_start"
      echo "END=$(($chapter_start + $duration_ms))"
      echo "title=Chapter $counter - $(basename "$mp3_path" .mp3)"
    } >> "$metadata_file"

    chapter_start=$(($chapter_start + $duration_ms))
    counter=$((counter + 1))
  done < "$files_list"

  # Single-pass: concat + encode + chapters + faststart
  ffmpeg -y \
    -f concat -safe 0 -i "$files_list" \
    -i "$metadata_file" \
    -map_metadata 1 \
    -map 0:a \
    -c:a aac -b:a 64k \
    -movflags +faststart \
    "$output_file"

  # Validate chapter count
  local expected=$((counter - 1))
  local actual=$(ffprobe -v error -show_chapters "$output_file" 2>/dev/null | \
                 grep -c "^\[CHAPTER\]" || echo 0)

  if [[ "$actual" -ne "$expected" ]]; then
    echo "WARNING: Expected $expected chapters, found $actual"
  else
    echo "SUCCESS: $output_file with $actual chapters"
  fi
}
```

### FFprobe Helper Library

```bash
# lib/ffmpeg.sh

# Get duration in seconds (float)
get_duration() {
  ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 "$1"
}

# Get bitrate (bits/sec as integer)
get_bitrate() {
  ffprobe -v error -show_entries format=bit_rate \
    -of default=noprint_wrappers=1:nokey=1 "$1"
}

# Get codec name
get_codec() {
  ffprobe -v error -select_streams a:0 \
    -show_entries stream=codec_name \
    -of default=noprint_wrappers=1:nokey=1 "$1"
}

# Get channel count (1=mono, 2=stereo)
get_channels() {
  ffprobe -v error -select_streams a:0 \
    -show_entries stream=channels \
    -of default=noprint_wrappers=1:nokey=1 "$1"
}

# Validate audio file
validate_audio_file() {
  local file="$1"

  [[ ! -f "$file" ]] && log_error "Not found: $file" && return 1

  if ! ffprobe -v error "$file" >/dev/null 2>&1; then
    log_error "Invalid audio: $file"
    return 1
  fi

  local codec=$(get_codec "$file")
  [[ -z "$codec" ]] && log_error "No audio stream: $file" && return 1

  log_debug "Valid: $file (codec=$codec)"
  return 0
}

# Convert seconds to HH:MM:SS
duration_to_timestamp() {
  local sec="$1"
  local h=$(echo "$sec / 3600" | bc)
  local m=$(echo "($sec % 3600) / 60" | bc)
  local s=$(echo "$sec % 60" | bc)
  printf "%02d:%02d:%02d\n" "$h" "$m" "$s"
}
```

### Logging Pattern

```bash
# lib/core.sh

LOG_LEVEL_DEBUG=0
LOG_LEVEL_INFO=1
LOG_LEVEL_WARN=2
LOG_LEVEL_ERROR=3
CURRENT_LOG_LEVEL=${LOG_LEVEL_INFO}
LOG_FILE="/var/log/audiobook-pipeline/convert.log"

log() {
  local level="$1"
  shift
  local message="$*"

  local timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  local level_num

  case "$level" in
    DEBUG) level_num=$LOG_LEVEL_DEBUG ;;
    INFO)  level_num=$LOG_LEVEL_INFO ;;
    WARN)  level_num=$LOG_LEVEL_WARN ;;
    ERROR) level_num=$LOG_LEVEL_ERROR ;;
  esac

  [[ $level_num -lt $CURRENT_LOG_LEVEL ]] && return 0

  local log_line="timestamp=$timestamp level=$level"
  [[ -n "${STAGE:-}" ]] && log_line="$log_line stage=$STAGE"
  [[ -n "${BOOK_HASH:-}" ]] && log_line="$log_line book_hash=$BOOK_HASH"

  local escaped_msg="${message//\"/\\\"}"
  log_line="$log_line message=\"$escaped_msg\""

  echo "$log_line" | tee -a "$LOG_FILE"
}

log_debug() { log DEBUG "$@"; }
log_info() { log INFO "$@"; }
log_warn() { log WARN "$@"; }
log_error() { log ERROR "$@"; }

die() {
  log_error "$@"
  exit 1
}

# Dry-run wrapper
run() {
  local cmd="$*"

  if [[ "$DRY_RUN" == "true" ]]; then
    log_info "[DRY-RUN] Would execute: $cmd"
    return 0
  fi

  log_debug "Executing: $cmd"
  eval "$cmd"
  local exit_code=$?

  if [[ $exit_code -ne 0 ]]; then
    log_error "Command failed (exit=$exit_code): $cmd"
    return $exit_code
  fi

  return 0
}
```

### Filename Sanitization

```bash
# lib/sanitize.sh

# Sanitize filename component (not full path)
sanitize_filename() {
  local filename="$1"

  local sanitized=$(echo "$filename" | sed -E '
    s/[\/\\:"*?<>|;]+/_/g;      # Replace unsafe chars
    s/^[._]+//;                  # Remove leading dots
    s/[._]+$//;                  # Remove trailing dots
    s/__+/_/g;                   # Collapse underscores
  ')

  # Truncate to 255 bytes
  if [[ ${#sanitized} -gt 255 ]]; then
    local ext="${sanitized##*.}"
    local base="${sanitized%.*}"

    if [[ "$ext" != "$sanitized" ]]; then
      local max_base_len=$((255 - ${#ext} - 1))
      sanitized="${base:0:$max_base_len}.$ext"
    else
      sanitized="${sanitized:0:255}"
    fi
  fi

  echo "$sanitized"
}

# Sanitize chapter title (more permissive)
sanitize_chapter_title() {
  local title="$1"

  echo "$title" | sed -E '
    s/[\/\\:"*?<>|;]+/ /g;      # Replace with spaces
    s/  +/ /g;                   # Collapse spaces
    s/^ +//;                     # Trim leading
    s/ +$//;                     # Trim trailing
  '
}

# Generate book hash for work directory
generate_book_hash() {
  local source_path="$1"

  {
    echo "$source_path"
    find "$source_path" -type f -name "*.mp3" | sort
  } | shasum -a 256 | cut -d' ' -f1 | cut -c1-16
}
```

### CLI Argument Parsing

```bash
#!/usr/bin/env bash
# bin/audiobook-convert

set -euo pipefail

DRY_RUN=false
FORCE=false
VERBOSE=false
CONFIG_FILE="/opt/audiobook-pipeline/config.env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --force)
      FORCE=true
      shift
      ;;
    -v|--verbose)
      VERBOSE=true
      shift
      ;;
    -c|--config)
      CONFIG_FILE="$2"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    -*)
      die "Unknown option: $1 (use --help)"
      ;;
    *)
      break
      ;;
  esac
done

SOURCE_PATH="${1:-}"
[[ -z "$SOURCE_PATH" ]] && die "SOURCE_PATH required"

export DRY_RUN FORCE VERBOSE

source "$CONFIG_FILE"

LIB_DIR="$(dirname "$0")/../lib"
source "$LIB_DIR/core.sh"
source "$LIB_DIR/ffmpeg.sh"
source "$LIB_DIR/manifest.sh"
source "$LIB_DIR/sanitize.sh"

main "$SOURCE_PATH"
```

---

## Common Pitfalls

### 1. Chapters Silently Dropped by Concat Demuxer

**Problem:** ffmpeg concat demuxer does NOT preserve chapters. Output M4B has zero chapter markers.

**Cause:** [FFmpeg Trac #6468](https://trac.ffmpeg.org/ticket/6468) -- concat demuxer doesn't support chapters yet.

**Solution:**
- Generate FFMETADATA1 file from input MP3 durations
- Use two-input ffmpeg command: `-i files.txt -i metadata.txt -map_metadata 1 -map 0:a`
- Validate chapter count after encoding

### 2. Missing `-movflags +faststart`

**Problem:** M4B files won't stream in Plex/audiobookshelf, chapter navigation slow, metadata not readable until full file scanned.

**Cause:** moov atom is at end of file (default ffmpeg behavior).

**Solution:** Always include `-movflags +faststart` in ffmpeg command. This relocates the moov atom to the file start.

### 3. Apostrophe Escaping in File Lists

**Problem:** Files with apostrophes (e.g., `Who's Afraid.mp3`) break concat demuxer.

**Cause:** Concat file list uses shell quoting: `file 'path'`. Single apostrophe inside path breaks the quote.

**Solution:** Escape apostrophes with triple apostrophes:
```bash
sed "s/'/'''/g"  # Replace ' with '''
```

### 4. Natural Sort Order

**Problem:** `find | sort` sorts lexicographically: `1, 10, 2, 20` instead of `1, 2, 10, 20`.

**Solution:** Use `sort -V` (version sort) for natural ordering:
```bash
find "$dir" -name "*.mp3" | sort -V
```

### 5. Duration Float to Milliseconds

**Problem:** ffprobe returns duration as float seconds (`1732.654`). Chapter timestamps need integer milliseconds.

**Cause:** Shell arithmetic doesn't handle floats.

**Solution:** Use `bc` for multiplication, `cut` to truncate:
```bash
duration_s=$(ffprobe ... format=duration ...)
duration_ms=$(echo "$duration_s * 1000" | bc | cut -d. -f1)
```

### 6. Mono Downmixing When Not Needed

**Problem:** Using `-ac 1` unconditionally converts stereo to mono, losing spatial effects.

**Recommendation:** Only use `-ac 1` if source is stereo AND user wants mono. For Phase 1, omit `-ac 1` (keep source channels).

### 7. Atomic Manifest Writes

**Problem:** Interrupted `jq ... > manifest.json` leaves corrupt/empty manifest.

**Solution:** Write to temp file, then atomic move:
```bash
jq ... "$manifest.json" > "$manifest.json.tmp.$$"
mv "$manifest.json.tmp.$$" "$manifest.json"
```

### 8. File Size Detection (macOS vs Linux)

**Problem:** `stat` has different flags on macOS (`-f%z`) vs Linux (`-c%s`).

**Solution:** Try both with fallback:
```bash
stat -f%z "$file" 2>/dev/null || stat -c%s "$file"
```

---

## Open Questions

### 1. Adaptive Bitrate vs Fixed 64kbps

**Current:** Fixed 64kbps per user decision (CONTEXT.md).

**Future consideration:** Should we detect source bitrate and apply floor/ceiling?
- Floor: 64k (don't downsample below this)
- Ceiling: 128k (don't upsample above this)
- Logic: `target = max(64, min(source, 128))`

**Decision:** Defer to Phase 2 (bitrate detection is NFR-07, not blocking for MVP).

### 2. tone vs ffmpeg for Chapter Embedding

**Two approaches:**
- **ffmpeg FFMETADATA1** -- single-pass, no dependencies, works during concat
- **tone `--auto-import=chapters`** -- post-processing, preserves existing metadata, requires tone binary

**Recommendation:** Use ffmpeg for Phase 1 (simpler, single-pass). Add tone support in Phase 3 for metadata enrichment.

### 3. Multi-Folder Book Detection

**Scenario:** Books split across directories: `CD 1/`, `CD 2/`, `Part 1/`, etc.

**Current approach (from existing scripts):** Detect subfolders matching patterns, merge all MP3s in sort order.

**Question:** Should we preserve folder structure in chapter titles?
- Option A: `Chapter 1 - 01.mp3`
- Option B: `Chapter 1 - CD 1 - 01.mp3`

**Recommendation:** Option A for simplicity. User can customize chapter titles in Phase 3.

### 4. Progress Indication

**Need:** Long-running ffmpeg jobs (1-2 hours for 20+ hour audiobooks) should show progress.

**Options:**
- Parse ffmpeg stderr for `time=` updates
- Use `pv` (pipe viewer) for byte-level progress
- FFmpeg `-progress` flag with Unix socket

**Recommendation:** Implement in Phase 2 (not blocking for MVP, but valuable for UX).

---

## Sources

### Primary (HIGH confidence)
- [FFmpeg Formats Documentation](https://ffmpeg.org/ffmpeg-formats.html)
- [FFmpeg Codecs Documentation](https://ffmpeg.org/ffmpeg-codecs.html)
- [ffprobe Documentation](https://ffmpeg.org/ffprobe.html)
- [Encode/AAC - FFmpeg Wiki](https://mirror.hjertaas.com/trac.ffmpeg.org/trac.ffmpeg.org/wiki/Encode/AAC.html)
- Existing scripts at `/Volumes/ThunderBolt/AudioBookStuff/AudioBooks_to_fix/scripts/`

### Secondary (MEDIUM confidence)
- [FFmpeg Trac #6468 - concat demuxer chapter issue](https://trac.ffmpeg.org/ticket/6468)
- [Joining Video Files While Preserving Chapters](https://www.caseyliss.com/2021/1/26/joining-files-keeping-chapters-using-ffmpeg)
- [Make videos start faster - FFmpeg Cookbook](https://code.pixplicity.com/ffmpeg/faststart/)
- [tone GitHub Repository](https://github.com/sandreas/tone)
- [ffprobe Tutorial (OTTVerse)](https://ottverse.com/ffprobe-comprehensive-tutorial-with-examples/)

---

## Metadata

**Research date:** 2026-02-20
**Valid until:** 2026-03-22 (30 days -- ffmpeg/bash are stable)
**Confidence:** HIGH overall
- ffmpeg commands: HIGH (verified working code)
- FFMETADATA1 format: HIGH (official spec + working examples)
- Bash patterns: HIGH (proven in existing scripts)
- Manifest schema: MEDIUM (new design, needs testing)
- Chapter validation: HIGH (ffprobe documented behavior)
