# Phase 3 Research: Folder Organization & Output

**Research Focus:** Plex folder structure, filename sanitization, NFS output handling, and archival patterns.

**Date:** 2026-02-20

---

## 1. Plex Audiobook Folder Structure

### Recommended Structure (Plex-Compatible)

Based on the [seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide), the canonical structure is:

```
/Audiobooks/
  Author Name/
    Series Name/
      Year - Book Title/
        Book Title (Year).m4b
        cover.jpg
        desc.txt
        reader.txt
```

**Format Details:**
- **With Series:** `Author/Series Name/Year - Book Title/Title (Year).m4b`
- **Without Series:** `Author/Book Title (Year)/Title (Year).m4b`
- **Companion Files:** `cover.jpg`, `desc.txt` (summary), `reader.txt` (narrator)

**Key Points:**
- Plex doesn't strictly enforce folder structure (metadata tags matter more)
- Album Artist tag = Author, Album tag = Book Title (critical for Plex matching)
- Companion files improve Booksonic compatibility and provide fallback metadata
- Year in folder name helps with multi-edition handling and chronological sorting

**Source:** [GitHub seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide)

---

## 2. Prologue iOS App Requirements

Prologue is a self-hosted audiobook player for Plex that works with iPhone, iPad, Apple Watch, and CarPlay.

**Folder/Naming Requirements:**
- Follows standard Plex conventions (no special requirements)
- Uses Plex's metadata agent, so proper tags are critical
- Works with MP3, M4A, M4B formats (M4B preferred for chapter support)
- Does NOT work with DRM-protected files (Apple Books/Audible)

**Best Practices:**
- Chapterized M4B files preferred (Plex handles M4B metadata better than MP3)
- Prologue can handle M4B chapter splits and names natively
- File system folder name should match Album tag for consistency

**Source:**
- [Prologue Audio](https://prologue.audio/)
- [Vikram Pant - Configuring Plex for Audiobooks](https://vikrampant.com/blog/configuring-plex-media-server-for-audiobooks-and-improved-ios-playback-experience/)

---

## 3. Filesystem Character Restrictions

### ext4 (NFS Server)

**Invalid Characters:**
- `/` (forward slash) - directory separator
- `\0` (NULL byte)

**Filename Length Limits:**
- 255 bytes per filename component
- 4096 bytes total path length
- Encrypted partitions reduce limit to ~140 characters

**Key Point:** ext4 is very permissive -- characters like `:`, `?`, `*` are VALID on ext4 but cause cross-platform issues.

**Source:** [Arch Linux Forums - Ext4 Invalid Characters](https://bbs.archlinux.org/viewtopic.php?id=81009)

### NFS-Specific Considerations

**User/Group Permissions:**
- NFS relies on numeric UIDs/GIDs, NOT usernames
- UIDs must match between client and server for permission checks
- Maximum 16 groups passed from client to server

**Character Translation:**
- When NFS serves NTFS partitions, characters like `: " \ * ? < > |` are translated
- Best practice: sanitize to most restrictive character set for cross-platform compatibility

**Source:**
- [Micro Focus - Understanding UNIX and NFS Permissions](https://support.microfocus.com/kb/doc.php?id=7021756)
- [AWS EFS - NFS Permissions](https://docs.aws.amazon.com/efs/latest/ug/accessing-fs-nfs-permissions.html)

### macOS APFS/HFS+

**Invalid Characters:**
- `/` (forward slash) - filesystem level
- `\0` (NULL byte)
- `:` (colon) - historically disallowed at volume level (flipped with slash at BSD level)

**Filename Length Limits:**
- 255 Unicode UTF-16 characters (both APFS and HFS+)

**Unicode Normalization:**
- HFS+ performs automatic Unicode normalization (prevents non-normal names)
- APFS does NOT normalize (respects exact Unicode, can cause compatibility issues with non-English characters)

**Source:** [SS64 - macOS Valid Filenames](https://ss64.com/mac/syntax-filenames.html)

### Cross-Platform Safe Character Set

To ensure compatibility across ext4, NFS, macOS, and Windows clients:

**Disallowed Characters:**
```
/ \ : * ? " < > | ; NUL
```

**Additional Sanitization:**
- Strip leading/trailing dots and underscores (Windows hidden file convention)
- Collapse repeated spaces and underscores
- Remove leading/trailing whitespace
- Avoid control characters (0x00-0x1F, 0x7F)

**Current Implementation:**
The existing `lib/sanitize.sh` function already handles most of this:
```bash
s/[\/\\:"*?<>|;]+/_/g;  # Replace invalid chars with underscore
s/^[._]+//;              # Strip leading dots/underscores
s/[._]+$//;              # Strip trailing dots/underscores
s/__+/_/g;               # Collapse repeated underscores
```

**Gap:** Current sanitizer uses underscores for replacements. For Plex folder names, spaces or hyphens may be more readable.

---

## 4. UTF-8 Safe Truncation at 255 Bytes

### Problem

Filesystems limit filenames to 255 **bytes**, not characters. UTF-8 uses 1-4 bytes per character:
- ASCII: 1 byte
- Latin extended, Greek, Cyrillic: 2 bytes
- Most other scripts (CJK, emoji): 3-4 bytes

Truncating mid-character creates invalid UTF-8.

### Safe Truncation Algorithm

1. Check if string length > 255 bytes
2. If yes, find last valid UTF-8 character boundary before byte 255
3. UTF-8 byte detection: if byte value < 0xC0 (192), it's NOT a continuation byte
4. Walk backwards from byte 255 until you find a byte < 0xC0, then truncate there

**Current Implementation:**
```bash
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
```

**Issue:** This uses `${#var}` which counts characters, not bytes. For UTF-8 strings, this can truncate past 255 bytes.

**Fix Needed:** Use byte-aware truncation with extension preservation.

**Source:** [Hacker News - UTF-8 Truncation](https://news.ycombinator.com/item?id=40573213)

---

## 5. Series Position Formatting

### Zero-Padding Standards

Based on Plex forum discussions and the Plex Audiobook Guide:

**Two-Digit Padding (01-99):**
- Standard for series < 100 books
- Format: `01`, `02`, `03`, ..., `99`
- Prevents alphabetical sort issues ("10" before "2")

**Three-Digit Padding (001-999):**
- Use for series with 100+ books
- Format: `001`, `002`, `003`, ..., `999`
- Example: Harlequin romance series, manga series

**Half-Number Books (e.g., "Book 1.5"):**
- Use decimal format: `01.5`, `02.5`
- Alternative: suffix notation `01a`, `01b` for multi-part books
- Plex sorts decimals correctly: `01`, `01.5`, `02`

**Recommendation:** Default to 2-digit padding, detect series size from metadata if 100+ books exist.

**Source:**
- [Plex Forums - Audiobook Sort Order](https://forums.plex.tv/t/audiobooks-and-sort-album-sort-not-working-as-expected/802475/4)
- [GitHub seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide)

---

## 6. Missing Metadata Fallback Patterns

### Missing Author

**Fallback Hierarchy:**
1. Use `artist` tag if `albumartist` is missing
2. Use folder name heuristic (parent directory name)
3. Fall back to `Unknown Author`

**Folder Structure:**
```
Unknown Author/
  Book Title (Year)/
    Book Title (Year).m4b
```

### Missing Title

**Fallback Hierarchy:**
1. Use `title` tag from first audio track
2. Use filename (sanitized, without extension)
3. Use source directory basename
4. Fall back to `Unknown Title - <BOOK_HASH>`

**Rationale:** Never use generic "Unknown Title" alone -- always append unique identifier (book hash) to avoid collisions.

### Missing Series

**Behavior:**
- Omit series folder entirely
- Use Author/Title structure
- Set series metadata tags to empty

### Missing Year

**Fallback Hierarchy:**
1. Use `date` or `year` tag
2. Use file modification time (last resort, unreliable)
3. Omit year from folder name if unavailable
4. Format: `Author/Title/Title.m4b` (no year parenthetical)

**Source:** [GitHub seanap/Audiobooks.bundle](https://github.com/seanap/Audiobooks.bundle)

---

## 7. Current Sanitization Analysis (`lib/sanitize.sh`)

### `sanitize_filename()` Function

**Current Behavior:**
```bash
sanitize_filename() {
  local filename="$1"
  local sanitized
  sanitized=$(echo "$filename" | sed -E '
    s/[\/\\:"*?<>|;]+/_/g;   # Replace invalid chars with underscore
    s/^[._]+//;               # Strip leading dots/underscores
    s/[._]+$//;               # Strip trailing dots/underscores
    s/__+/_/g;                # Collapse repeated underscores
  ')

  # Truncate to 255 bytes while preserving extension
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
```

**Strengths:**
- Replaces cross-platform unsafe characters
- Strips leading/trailing dots (Windows compatibility)
- Collapses repeated separators
- Preserves file extension when truncating

**Gaps:**
1. **Character vs Byte Counting:** Uses `${#var}` (character count) instead of byte count -- fails for multi-byte UTF-8
2. **No Space Normalization:** Doesn't collapse double spaces or strip leading/trailing spaces
3. **Underscore-Only Replacement:** Uses `_` for all replacements -- less readable for folder names (consider space/hyphen)
4. **No UTF-8 Boundary Awareness:** Truncation can split multi-byte characters

### `sanitize_chapter_title()` Function

**Current Behavior:**
```bash
sanitize_chapter_title() {
  local title="$1"
  echo "$title" | sed -E '
    s/[\/\\:"*?<>|;]+/ /g;   # Replace invalid chars with SPACE
    s/  +/ /g;                # Collapse double spaces
    s/^ +//;                  # Strip leading spaces
    s/ +$//;                  # Strip trailing spaces
  '
}
```

**Strengths:**
- Uses spaces instead of underscores (more readable)
- Normalizes whitespace

**Gaps:**
- No length limit enforcement
- No UTF-8 safe truncation

### Recommendations for Phase 3

1. **Create `sanitize_folder_component()` function:**
   - Replace invalid chars with spaces (more readable than underscores)
   - Collapse double spaces
   - Strip leading/trailing whitespace and dots
   - Truncate to 255 bytes using UTF-8 safe algorithm

2. **Create `sanitize_path()` function:**
   - Apply `sanitize_folder_component()` to each path segment
   - Validate total path length < 4096 bytes
   - Return sanitized absolute path

3. **Add `get_byte_length()` helper:**
   - Use `wc -c` or `printf '%s' | wc -c` for accurate byte counting

4. **Add `truncate_utf8_safe()` helper:**
   - Truncate at byte boundary without splitting UTF-8 characters
   - Preserve file extensions

---

## 8. Using `install` Command for NFS Output

### Command Overview

From `man install`:
```
install [OPTION]... SOURCE DEST
install [OPTION]... SOURCE... DIRECTORY
install [OPTION]... -d DIRECTORY...

Options:
  -m, --mode=MODE     Set permission mode (as in chmod), instead of rwxr-xr-x
  -o, --owner=OWNER   Set ownership (super-user only)
  -g, --group=GROUP   Set group ownership, instead of process' current group
  -d, --directory     Create all components of the specified directories
  -D                  Create all leading components of DEST except the last
  -v, --verbose       Print the name of each created file or directory
```

### Current Usage in `stages/04-cleanup.sh`

```bash
local owner_user="${FILE_OWNER%%:*}"  # Extract UID from "2018:2000"
local owner_group="${FILE_OWNER##*:}" # Extract GID from "2018:2000"
run install -m "${FILE_MODE:-644}" -o "$owner_user" -g "$owner_group" \
  "$convert_output" "$final_path"
```

**Benefits of `install` vs `cp`:**
- Atomic operation: copy + chmod + chown in one command
- Creates parent directories if needed (with `-D`)
- Reduces race conditions
- Better for NFS (single write operation)

### Recommended Usage for Phase 3

**For Files:**
```bash
install -m 644 -o 2018 -g 2000 source.m4b /mnt/media/AudioBooks/Author/Series/Book/Title.m4b
```

**For Directories:**
```bash
install -d -m 755 -o 2018 -g 2000 /mnt/media/AudioBooks/Author/Series/Book
```

**For Companion Files:**
```bash
install -m 644 -o 2018 -g 2000 cover.jpg /mnt/media/AudioBooks/Author/Series/Book/cover.jpg
install -m 644 -o 2018 -g 2000 desc.txt /mnt/media/AudioBooks/Author/Series/Book/desc.txt
install -m 644 -o 2018 -g 2000 reader.txt /mnt/media/AudioBooks/Author/Series/Book/reader.txt
```

**Why Numeric UIDs?**
- NFS uses numeric UIDs/GIDs, not usernames
- `readarr` user is UID 2018, `media` group is GID 2000
- Must match across NFS client and server

**Source:** `man install`, [Micro Focus NFS Permissions](https://support.microfocus.com/kb/doc.php?id=7021756)

---

## 9. Companion Files in Book Folder

### Recommended Companion Files

Based on Plex Audiobook Guide and Booksonic compatibility:

1. **cover.jpg**
   - Album artwork extracted from M4B metadata
   - JPEG format, typically 600x600 or 1400x1400 pixels
   - Fallback if embedded cover is missing or corrupt

2. **desc.txt**
   - Plain text file with book description/summary
   - Source: Audnexus API summary field
   - Used by Booksonic and other players that don't read M4B tags

3. **reader.txt**
   - Plain text file with narrator name(s)
   - Source: Audnexus API narrators field
   - Comma-separated if multiple narrators

**Location:** All three files go in the same folder as the M4B file.

**Example:**
```
Author/
  Series Name/
    01 - Book Title (2023)/
      Book Title (2023).m4b
      cover.jpg
      desc.txt
      reader.txt
```

**Permissions:** Same as M4B file (644, owned by readarr:media)

**Source:** [GitHub seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide)

---

## 10. Archive Pattern for Original Files

### Requirement (FR-ARCH-01)

> Move originals to archive after output M4B verified via ffprobe

### Recommended Archive Structure

**Option 1: Mirror Structure with `.archive/` suffix**
```
/mnt/media/AudioBooks.archive/
  Author/
    Series/
      Book Title (Year)/
        *.mp3
        *.txt
        *.jpg
        metadata.json
```

**Option 2: Flat Hash-Based Archive**
```
/mnt/media/AudioBooks.archive/
  <book-hash>/
    *.mp3
    *.txt
    *.jpg
    metadata.json
```

**Option 3: Date-Based Archive**
```
/mnt/media/AudioBooks.archive/
  2026/
    02/
      20/
        <book-hash>/
          *.mp3
```

**Recommendation:** Use **Option 1** (mirror structure) for human readability and easier recovery.

### Archive Workflow

1. **Verify M4B with ffprobe:**
   ```bash
   ffprobe -v error "$final_m4b" >/dev/null 2>&1 || die "M4B validation failed"
   ```

2. **Create archive target directory:**
   ```bash
   local archive_dir="${OUTPUT_DIR}.archive/Author/Series/Book Title (Year)"
   install -d -m 755 -o 2018 -g 2000 "$archive_dir"
   ```

3. **Move original files:**
   ```bash
   mv "$SOURCE_PATH"/*.mp3 "$archive_dir/"
   mv "$SOURCE_PATH"/*.txt "$archive_dir/" 2>/dev/null || true
   mv "$SOURCE_PATH"/*.jpg "$archive_dir/" 2>/dev/null || true
   ```

4. **Update manifest:**
   ```bash
   manifest_update "$BOOK_HASH" \
     ".stages.archive.status = \"completed\"
      | .stages.archive.archive_path = \"$archive_dir\"
      | .stages.archive.archived_at = \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\""
   ```

### Retention Policy

- Keep archives for 90 days (configurable via `ARCHIVE_RETENTION_DAYS`)
- Implement cleanup cron job to purge old archives
- Never auto-delete archives with `stages.archive.manual_keep = true` flag

---

## 11. Implementation Recommendations

### New Functions Needed

1. **`sanitize_folder_component()`**
   - UTF-8 safe, byte-aware truncation
   - Space-based replacement (not underscore)
   - Whitespace normalization

2. **`build_plex_path()`**
   - Takes: author, series, title, year, series_position
   - Returns: sanitized folder path
   - Handles missing metadata fallbacks

3. **`create_companion_files()`**
   - Extract cover from M4B → cover.jpg
   - Write description → desc.txt
   - Write narrator(s) → reader.txt

4. **`archive_originals()`**
   - Move source files to archive structure
   - Update manifest with archive location
   - Verify M4B before archiving

### Configuration Variables

Add to `config.env.example`:
```bash
# Phase 3: Folder Organization & Output
NFS_OUTPUT_DIR="/mnt/media/AudioBooks"
ARCHIVE_DIR="/mnt/media/AudioBooks.archive"
ARCHIVE_RETENTION_DAYS=90
SERIES_PADDING_DIGITS=2  # Use 2 for < 100 books, 3 for 100+
CREATE_COMPANION_FILES=true
```

### New Stage: `stages/07-organize.sh`

Replace `stages/04-cleanup.sh` with:
- Build Plex folder path from metadata
- Create target directory structure with `install -d`
- Copy M4B to final location with `install`
- Create companion files (cover.jpg, desc.txt, reader.txt)
- Verify output with ffprobe
- Archive original source files
- Update manifest with final paths

---

## Sources

- [GitHub - seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide)
- [GitHub - seanap/Audiobooks.bundle](https://github.com/seanap/Audiobooks.bundle)
- [Prologue Audio](https://prologue.audio/)
- [Vikram Pant - Configuring Plex for Audiobooks](https://vikrampant.com/blog/configuring-plex-media-server-for-audiobooks-and-improved-ios-playback-experience/)
- [Plex Forums - Best Practices for Audiobooks](https://forums.plex.tv/t/best-practices-for-audiobooks-file-types-naming-and-metadata/814851)
- [SS64 - macOS Valid Filenames](https://ss64.com/mac/syntax-filenames.html)
- [Arch Linux Forums - Ext4 Invalid Characters](https://bbs.archlinux.org/viewtopic.php?id=81009)
- [Micro Focus - Understanding UNIX and NFS Permissions](https://support.microfocus.com/kb/doc.php?id=7021756)
- [AWS EFS - NFS Permissions](https://docs.aws.amazon.com/efs/latest/ug/accessing-fs-nfs-permissions.html)
- [Hacker News - Linux 255 Bytes Limitation](https://news.ycombinator.com/item?id=19242579)
- [Plex Forums - Audiobook Sort Order](https://forums.plex.tv/t/audiobooks-and-sort-album-sort-not-working-as-expected/802475/4)
