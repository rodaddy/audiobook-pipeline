# Phase 3 Research: Folder Organization & Output

**Date:** 2026-02-20
**Sources:** 03-RESEARCH-FOLDER.md (folder structure, sanitization), 03-RESEARCH-ARCHIVE.md (archive, NFS, validation)

## Key Findings

### 1. Plex Folder Structure

Canonical format from [seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide):

```
/AudioBooks/
  Author Name/
    Series Name/                    # Only if series metadata exists
      NN - Book Title (Year)/       # Zero-padded series position
        Book Title (Year).m4b
        cover.jpg
        desc.txt
        reader.txt
    Book Title (Year)/              # Standalone (no series)
      Book Title (Year).m4b
      cover.jpg
      desc.txt
      reader.txt
```

- Album Artist tag = Author, Album tag = Title (Plex relies on tags more than folders)
- Prologue iOS app has no special requirements beyond standard Plex structure
- Series position: 2-digit padding (01-99), 3-digit for 100+ book series
- Decimal format for half-books: 01.5, 02.5

### 2. NFS Operations -- install vs cp

**Critical finding:** `install -o UID -g GID` fails on NFS with root squash because client cannot chown.

```bash
# FAILS on NFS with root squash:
install -m 644 -o 2018 -g 2000 "$source" "$nfs_dest"

# WORKS -- NFS maps UIDs automatically:
cp "$source" "$nfs_dest"
chmod 644 "$nfs_dest"
```

Current `stages/04-cleanup.sh` uses install -- must be replaced for NFS.
Add `check_nfs_available()` with 5s timeout before organize/archive stages.

### 3. Filename Sanitization

Current `lib/sanitize.sh` gaps:
1. Uses `${#var}` (character count) not byte count -- fails for UTF-8 multi-byte
2. No space normalization (double spaces)
3. Underscore-only replacement -- less readable for folders
4. Truncation can split multi-byte UTF-8 characters

Need:
- `sanitize_folder_component()` -- space-based replacement, UTF-8 byte-aware truncation to 255 bytes
- `build_plex_path()` -- construct Author/Series/Title path with fallbacks
- Use `printf '%s' "$var" | wc -c` for byte counting

### 4. Missing Metadata Fallbacks

| Field | Fallback Hierarchy |
|-------|-------------------|
| Author | albumartist tag -> artist tag -> folder name -> "Unknown Author" |
| Title | album tag -> title tag -> filename -> basename -> "Unknown Title - HASH" |
| Series | Omit series folder entirely |
| Year | date/year tag -> omit from folder name |

### 5. Stage Architecture

Two new stages, cleanup renumbered:

```
STAGE_ORDER=(validate concat convert asin metadata organize archive cleanup)
STAGE_MAP: [organize]="07"  [archive]="08"  [cleanup]="09" (was "04")
```

- **Stage 07 (organize):** Build Plex path, mkdir -p, cp+chmod to NFS, companion files
- **Stage 08 (archive):** Validate M4B integrity, move originals to ARCHIVE_DIR
- **Stage 09 (cleanup):** Renumbered from 04, clean work directory

### 6. M4B Validation Before Archive

Must validate before destroying originals:
1. File exists and non-empty
2. ffprobe can parse container
3. Duration > 0
4. Codec is AAC
5. Container is mov/mp4 family
6. File size within 10% of expected (bitrate x duration / 8)

### 7. Idempotency Patterns

Both new stages must:
- Check manifest stage status at start (skip if completed)
- Check if files already exist at destination
- Update manifest AFTER each major operation
- Handle partial completion (files moved but manifest not updated)

### 8. Config Variables Needed

```bash
NFS_OUTPUT_DIR="/mnt/media/AudioBooks"     # Plex library root on NFS
ARCHIVE_DIR="/var/lib/audiobook-pipeline/archive"  # Local disk preferred
ARCHIVE_RETENTION_DAYS=90
CREATE_COMPANION_FILES=true
```

## Anti-Patterns

- **install on NFS** -- fails with root squash, use cp+chmod
- **Archive before validation** -- risk of losing originals if M4B corrupt
- **mv across filesystems** -- not atomic, use cp+verify+rm
- **Character count for truncation** -- must use byte count for filesystem limits

## Sources

- [seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide)
- [ffprobe Documentation](https://ffmpeg.org/ffprobe.html)
- [NFS Root Squash - Microsoft Azure](https://learn.microsoft.com/en-us/azure/storage/files/nfs-root-squash)
- [Red Hat - NFS Permissions](https://access.redhat.com/solutions/100013)
- [SS64 - macOS Valid Filenames](https://ss64.com/mac/syntax-filenames.html)
- [Arch Linux - Ext4 Invalid Characters](https://bbs.archlinux.org/viewtopic.php?id=81009)
- [Idempotent Bash Scripts](https://arslan.io/2019/07/03/how-to-write-idempotent-bash-scripts/)
