# Domain Pitfalls

**Domain:** Automated audiobook processing pipeline (MP3 to M4B, metadata enrichment, Plex organization)
**Researched:** 2026-02-20

---

## Critical Pitfalls

Mistakes that cause rewrites, data loss, or fundamentally broken output.

### Pitfall 1: Chapter Markers Lost During Concat/Transcode

**What goes wrong:** When concatenating multiple MP3 files into a single M4B, ffmpeg silently drops chapter metadata unless you explicitly provide a chapters metadata file via `-map_metadata`. The default behavior produces a single monolithic track with no chapter navigation. Even when chapters are provided, they often come out as "Chapter 1, Chapter 2" with no real titles.

**Why it happens:** ffmpeg's concat demuxer treats input files as raw streams. Chapter information must be reconstructed from file boundaries (using ffprobe durations) and injected as a separate metadata input. There is no automatic "one file = one chapter" behavior.

**Consequences:** Users get a 20+ hour audiobook with zero chapter navigation. Prologue/BookCamp show one giant track. Unusable for anything but sequential listening. Reconversion required.

**Prevention:**
1. Generate a FFMETADATA1 chapters file from individual MP3 durations using ffprobe before concat
2. Use the two-input approach: `ffmpeg -i concat_list.txt -i chapters_metadata.txt -map_metadata 1 output.m4b`
3. Validate output with `ffprobe -show_chapters output.m4b` -- assert chapter count matches input file count
4. Consider using m4b-tool instead of raw ffmpeg -- it handles chapter generation from input file tags automatically

**Detection:** Post-conversion check: `ffprobe -show_chapters output.m4b | grep -c "Chapter"` should match expected chapter count. Zero = total failure.

**Confidence:** HIGH -- well-documented across multiple sources

### Pitfall 2: NFS Root Squash Breaking Atomic Moves and Permissions

**What goes wrong:** On LXC 210 running as the readarr user (UID 2018), file operations over NFS to TrueNAS can fail silently or produce files with wrong ownership. Atomic `mv` (rename) across NFS mount boundaries falls back to copy+delete, which is slow and non-atomic. If any process runs as root (even briefly, e.g., a systemd service before dropping privileges), root squash maps it to `nobody:nogroup`, creating files the readarr user cannot modify.

**Why it happens:** NFS root_squash maps UID 0 to `nobody` (65534). The `rename()` syscall only works within the same filesystem -- moving from a local temp dir to an NFS mount is a cross-device move that silently becomes copy+delete. Additionally, NFS is not fully POSIX compliant -- `O_EXCL` and other atomic operations may not behave as expected.

**Consequences:**
- Orphaned temp files when copy+delete fails mid-transfer
- Permission denied errors during metadata tagging or file organization
- Corrupted output if process is interrupted during the non-atomic copy phase
- Files owned by nobody:nogroup that readarr/Plex cannot read

**Prevention:**
1. **All temp work on the NFS mount itself** -- write temp files to a `.tmp` directory on the same NFS share so `mv` is a true atomic rename within the same filesystem
2. **Never run pipeline scripts as root** -- always run as UID 2018 (readarr). Ensure systemd unit has `User=readarr` from the start
3. **Verify NFS export options** -- ensure TrueNAS export allows the readarr UID/GID to write. Check `mapall` and `maproot` settings
4. **Use `install -o readarr -g media` instead of `mv` + `chown`** for final file placement

**Detection:** `ls -la` on output files -- if owner is `nobody` or `65534`, root squash is biting you. Test with `touch /nfs/mount/testfile && ls -la /nfs/mount/testfile`.

**Confidence:** HIGH -- root squash issues are extremely well-documented, and the cross-device rename trap is a classic Unix pitfall

### Pitfall 3: Audible Metadata Matching Returns Wrong Book

**What goes wrong:** Fuzzy title matching against Audible returns the wrong book entirely. "The Clouds of Saturn" matching to "Saturn's Story" (completely different book). Reissued audiobooks with new ASINs cause metadata to flip to the reissue. Omnibus editions, companion novels (Book 0.5), and multi-book collections create ambiguous matches.

**Why it happens:** Audible's search API returns results by relevance, not exact match. Short titles, common words, and series with many entries produce ambiguous results. Series numbering is stored as a plain number in most tools, so fractional entries (0.5, 1.5) or ranges ("Books 1-3") break sorting. Audible itself doesn't always accept structured series metadata -- it may be shoved into the subtitle field.

**Consequences:**
- Wrong cover art, description, narrator attribution
- Series order completely wrong (0.5 entries sorted incorrectly or missing)
- Omnibus editions get matched to individual books or vice versa
- Wrong book in Plex library with no indication it's wrong until someone listens

**Prevention:**
1. **Require ASIN when available** -- direct ASIN lookup is unambiguous. Store ASINs in a mapping file or derive from source filenames
2. **Multi-field matching** -- match on title AND author AND narrator, not title alone
3. **Confidence scoring** -- if the best match scores below a threshold, flag for manual review rather than auto-accepting
4. **Human-in-the-loop for edge cases** -- series 0.5, omnibus, anthologies should always require manual confirmation
5. **Store series position as string, not number** -- "0.5", "1-3", "Prequel" all need to work
6. **Log every match decision** -- include the query, top 3 results, and selected match so bad matches can be audited

**Detection:** Periodic spot-check of recently processed books. Compare embedded metadata against filename/folder expectations.

**Confidence:** HIGH -- multiple tools (audiobookshelf, beets-audible, Listenarr) document these exact issues

### Pitfall 4: Bash Word Splitting Destroys Filenames with Spaces

**What goes wrong:** Audiobook filenames routinely contain spaces, apostrophes, parentheses, and other special characters: `The Hitchhiker's Guide to the Galaxy (Unabridged) - Part 01.mp3`. Unquoted variable expansions in bash split these into multiple arguments, causing `file not found` errors, partial processing, or -- worst case -- operating on wrong files.

**Why it happens:** Bash performs word splitting on unquoted `$variable` expansions using IFS (default: space, tab, newline). `for f in $(ls *.mp3)` breaks on every space. Glob expansion in unquoted variables can also match unintended files.

**Consequences:**
- Silent file skipping (script processes "The" then fails on "Hitchhiker's")
- Concatenation with missing files produces truncated audiobooks
- `rm` on split filenames could delete wrong files
- Entire pipeline breaks on the first audiobook with spaces in its name (which is nearly all of them)

**Prevention:**
1. **Quote everything:** `"$file"`, `"${array[@]}"`, never bare `$file`
2. **Use arrays for file lists:** `files=( *.mp3 )` then `for f in "${files[@]}"`
3. **Use `find -print0 | while IFS= read -r -d '' file`** for recursive operations
4. **Run ShellCheck on all scripts** -- it catches unquoted variables automatically
5. **Set `IFS=$'\n\t'` at script top** as a safety net (removes space from default IFS)
6. **Never parse `ls` output** -- use globs or `find` instead

**Detection:** ShellCheck (shellcheck.net) catches 90%+ of these issues statically. Test with filenames containing spaces, quotes, and parentheses.

**Confidence:** HIGH -- fundamental bash behavior, extensively documented at wooledge.org/BashPitfalls

---

## Moderate Pitfalls

Issues that cause wasted time, degraded quality, or require manual intervention.

### Pitfall 5: AAC Encoder Quality and Compatibility Issues

**What goes wrong:** ffmpeg's built-in `aac` encoder produces lower quality than `libfdk_aac` (the non-free encoder). At low bitrates (32kbps or below), the difference becomes noticeable. Worse, ffmpeg's HE-AAC implementation produces files incompatible with many players including iTunes. Some Plex clients may also struggle with certain AAC profiles.

**Why it happens:** `libfdk_aac` cannot be distributed in pre-built ffmpeg binaries due to licensing (non-free codec). The built-in `aac` encoder is decent but not best-in-class. HE-AAC v2 support varies wildly across players.

**Prevention:**
1. **Use AAC-LC at 64kbps** -- this is what Audible uses and has universal compatibility. Avoid HE-AAC unless you specifically need tiny files
2. **If quality matters, compile ffmpeg with libfdk_aac** or use the m4b-tool Docker image which includes it
3. **Use `-c:a copy` when source is already AAC** -- avoids quality loss and dramatically reduces CPU usage (85% to 18% reduction reported)
4. **Standardize on sample rate 44100 Hz** for maximum player compatibility
5. **Test output in Prologue specifically** -- that's the target player

**Detection:** Listen-test a sample conversion. Check with `ffprobe` that codec is `aac` (not `he-aac` or `aac_he`).

**Confidence:** HIGH

### Pitfall 6: Silence Detection False Positives

**What goes wrong:** ffmpeg's `silencedetect` filter is a simple volume threshold tool. It cannot distinguish between intentional chapter-break silence and quiet moments in background music, dramatic pauses, or sound effects. Audiobooks with music interludes between chapters often produce dozens of false positive chapter markers. Conversely, audiobooks with no clear silence between chapters produce zero splits.

**Why it happens:** `silencedetect` only measures volume level against a threshold. A -30dB threshold tuned for speech will trigger on quiet music. The optimal threshold varies per audiobook -- narrator style, recording quality, and post-production all affect where silence falls.

**Prevention:**
1. **Use generous minimum duration** -- `d=2.0` or higher (2+ seconds of silence) to skip dramatic pauses and only catch chapter breaks
2. **Two-pass approach** -- detect silence first, output timestamps, review/filter, then split. Never auto-split blindly
3. **Prefer embedded chapter data** when available (Audible M4B files already have chapters)
4. **Prefer filename-based chapters** -- if input is already split into numbered MP3s, each file IS a chapter. No silence detection needed
5. **Calibrate per-book** -- expose threshold as a config parameter, not a hardcoded value. Default to -30dB, d=2.0
6. **Fallback: even-split chapters** -- if silence detection fails, split into N equal-duration chapters as a last resort rather than one giant chapter

**Detection:** Compare detected chapter count against expected count (from Audible metadata or file count). If they diverge by more than 20%, flag for manual review.

**Confidence:** MEDIUM -- well-understood technically, but real-world failure rates depend heavily on source material

### Pitfall 7: Unicode Normalization Across macOS/NFS/ext4

**What goes wrong:** Author names with accented characters (Bronte with an umlaut, Garcia Marquez with accents) can exist in two Unicode forms: NFC (composed, one codepoint) and NFD (decomposed, base + combining mark). macOS historically used NFD (HFS+), Linux uses NFC by convention (ext4 doesn't enforce), and NFS doesn't mandate either. Files created from macOS over NFS may use NFD while files created on Linux use NFC -- they look identical but are different byte sequences. `ls` shows two files, comparisons fail, deduplication breaks.

**Why it happens:** HFS+ forced NFD normalization. APFS (modern macOS) preserves both forms but adds a normalization layer. ext4 treats filenames as opaque bytes. NFS RFC3530 explicitly does NOT specify a normalization form. When files cross filesystem boundaries, normalization mismatches silently accumulate.

**Prevention:**
1. **Normalize all filenames to NFC before writing** -- NFC is the web standard, Linux convention, and most tools expect it. Use Python's `unicodedata.normalize('NFC', name)` or bash: pipe through `uconv -x NFC` (from icu-devtools)
2. **Strip or transliterate problem characters** -- for folder names, consider ASCII-safe versions (e.g., "Bronte" instead of "Bronte-with-umlaut") if downstream tools struggle
3. **Test with accented author names early** -- don't discover this in production with 500 books
4. **Avoid mixing AFP and NFS access** to the same share -- this creates mixed NFC/NFD filenames on the same disk

**Detection:** `ls | xxd` to inspect actual bytes. If the same visual filename appears twice, you have a normalization mismatch. Tool: `convmv --notest -f utf8 -t utf8 --nfc -r .` to normalize existing files.

**Confidence:** MEDIUM -- the LXC-to-TrueNAS-via-NFS path is Linux-to-Linux (both NFC-preferring), so this is less likely than macOS-involved paths. But if any files originate from macOS (e.g., user uploads), it will bite.

### Pitfall 8: Large Audiobook Resource Exhaustion

**What goes wrong:** A 40-hour audiobook at 128kbps is roughly 2.2 GB as MP3. Re-encoding to AAC requires ffmpeg to hold significant data in memory. The M4B container format (MP4) needs the `moov` atom finalized at the end -- using `-movflags +faststart` causes ffmpeg to write a temp file and rewrite the entire output, roughly doubling disk space needs. On LXC 210 with 8GB RAM and shared storage, this can exhaust either resource.

**Why it happens:** AAC encoding buffers frames in memory. The MP4 muxer accumulates index data proportional to file duration. `+faststart` must rewrite the entire file to move the moov atom to the beginning (required for streaming/seeking).

**Prevention:**
1. **Check disk space before conversion** -- require at least 3x the input file size in temp space (input + output + faststart rewrite)
2. **Use `-c:a copy` when possible** -- if source is already AAC, skip re-encoding entirely
3. **Process one book at a time** -- don't parallelize conversions on an 8GB RAM system
4. **Monitor with `ulimit` and process accounting** -- set memory limits so a runaway ffmpeg doesn't OOM the entire LXC
5. **Use local temp dir, not NFS** -- NFS adds latency to every write, making the faststart rewrite much slower. But see Pitfall 2 -- if you use local temp, the final move to NFS is a cross-device copy
6. **For extreme cases (80+ hours):** consider splitting into multiple M4B files (e.g., by disc/part) rather than one massive file

**Detection:** Pre-flight check: `df -h` on temp dir and output dir. If available space < 3x input size, abort with clear error message.

**Confidence:** MEDIUM -- 8GB RAM is plenty for audiobook encoding (audio is small compared to video), but the disk space issue is real, especially if temp and output are on the same NFS share

---

## Minor Pitfalls

Issues that cause friction but have straightforward fixes.

### Pitfall 9: Plex Scanner Doesn't Detect New Audiobooks

**What goes wrong:** New M4B files appear on disk but don't show up in Plex. Or they appear but with wrong metadata, no cover art, or filed under the wrong author. Prologue shows empty library or missing chapters.

**Why it happens:** Multiple causes:
- Wrong library type (must be Music, not Movies or Other)
- Wrong scanner/agent (need "Personal Media Artists" scanner or Audnexus agent)
- File tags don't match folder structure -- Audnexus uses Album Artist tag for author and Album tag for title
- M4B files with no embedded tags fall back to filename parsing, which is fragile
- Plex caches aggressively -- new files may not appear until a manual scan or scheduled scan runs

**Prevention:**
1. **Follow seanap's Plex-Audiobook-Guide structure exactly:** `Audiobooks/Author Name/Book Title/Book Title.m4b`
2. **Tag files with tone before moving to Plex library** -- set Album Artist (author), Album (title), and cover art at minimum
3. **Install Audnexus agent** -- it pulls metadata from Audible API using the Album Artist + Album tags
4. **Trigger Plex scan after each batch** -- use `curl` to hit Plex's scan endpoint or use the `plexapi` Python library
5. **Separate audiobook library from music** -- mixing them causes genre/metadata conflicts

**Detection:** After processing, check Plex API for the new book. If missing after scan, check Plex logs at `/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Logs/`.

**Confidence:** HIGH -- extensively documented in seanap's guide and Plex forums

### Pitfall 10: Signal Handling During Long Conversions

**What goes wrong:** A 40-hour audiobook conversion takes 10-30 minutes. If the script receives SIGINT (Ctrl-C), SIGTERM (system shutdown), or SIGHUP (terminal disconnect), ffmpeg may be killed mid-write, leaving a corrupted partial M4B file. The script's cleanup logic never runs because bash doesn't handle signals while a foreground process is running.

**Why it happens:** When bash executes an external command (like ffmpeg) in the foreground, it doesn't process signal handlers until the foreground process exits. If you `trap cleanup EXIT` and ffmpeg is running, the trap doesn't fire until ffmpeg finishes or is killed. The kill goes to ffmpeg, not the script.

**Prevention:**
1. **Run ffmpeg in background with `wait`:**
   ```bash
   ffmpeg [args] &
   pid=$!
   trap 'kill "$pid" 2>/dev/null; cleanup; exit 1' INT TERM
   wait "$pid"
   ```
2. **Write to temp file, rename on success** -- if conversion is interrupted, the temp file is clearly incomplete. Never write directly to the final output path
3. **Implement a lockfile** -- prevent multiple instances from processing the same book simultaneously
4. **Cleanup function should remove temp files** -- `trap cleanup EXIT` handles all exit paths (normal, error, signal)
5. **Use `set -euo pipefail`** -- catch errors early rather than continuing with partial data

**Detection:** Look for orphaned `.tmp` or `.partial` files in the processing directory. Their presence indicates interrupted conversions.

**Confidence:** HIGH -- standard bash signal handling behavior

### Pitfall 11: Special Characters in Folder/File Names Beyond Unicode

**What goes wrong:** Beyond Unicode normalization, certain characters are illegal or problematic in filenames across platforms. Colons (`:`) are illegal on Windows/SMB. Forward slashes (`/`) are directory separators. Ampersands, quotes, and semicolons break unquoted shell commands. Author names like "H.P. Lovecraft" or book titles like "Who's Afraid of Virginia Woolf?" contain characters that can cause issues.

**Why it happens:** Different filesystems have different illegal character sets. NFS to TrueNAS (ZFS) is permissive, but if files are ever accessed via SMB (Windows/macOS clients), colons and other characters will cause failures.

**Prevention:**
1. **Sanitize filenames** -- replace `:` with ` -`, strip `/ \ ? * " < > |`, collapse multiple spaces
2. **Keep originals in metadata, sanitize only paths** -- the M4B file's internal tags can have any characters; only the filesystem path needs sanitizing
3. **Build a `sanitize_filename()` function** and use it consistently for all path construction
4. **Test with pathological names** -- include test cases like: `Author: Name`, `Book (Vol. 1/2)`, `Title "Quoted"`, `Name's Book`

**Detection:** `find /audiobooks -name '*[:?*"<>|]*'` to locate problematic filenames before they cause issues downstream.

**Confidence:** HIGH

### Pitfall 12: Race Conditions with Watch Directories

**What goes wrong:** If the pipeline watches an input directory for new files (e.g., via inotifywait or polling), it may trigger processing before the file is fully written. A large MP3 being copied over NFS can take minutes -- the pipeline sees the file appear and starts processing an incomplete file.

**Why it happens:** File creation and file completion are separate events. `inotifywait CREATE` fires when the file is created (0 bytes), not when writing finishes. Polling with `find -newer` also catches partially-written files.

**Prevention:**
1. **Write to a staging directory, then move** -- the source process writes to `/incoming/.tmp/` and moves to `/incoming/` when done. The pipeline only watches `/incoming/`
2. **Check file stability** -- compare file size at two time points (e.g., 5 seconds apart). If size changed, it's still being written
3. **Use a sentinel file** -- source process creates a `.done` marker file after the audiobook files are fully written. Pipeline watches for `.done` files
4. **Lock files** -- use `flock` to coordinate access between writer and processor

**Detection:** Corrupted/truncated output from otherwise valid source material. ffmpeg errors about "invalid data" or "unexpected EOF".

**Confidence:** HIGH

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| MP3 to M4B conversion | Chapter markers lost (Pitfall 1), AAC quality (Pitfall 5) | Generate chapters from file durations, use AAC-LC 64kbps |
| Metadata enrichment | Wrong Audible match (Pitfall 3) | Multi-field matching, confidence scoring, human review fallback |
| File organization | Word splitting (Pitfall 4), special chars (Pitfall 11) | Quote all variables, sanitize filenames, run ShellCheck |
| NFS operations | Root squash (Pitfall 2), race conditions (Pitfall 12) | Run as readarr user, temp files on same mount, stability checks |
| Plex integration | Scanner issues (Pitfall 9) | Follow seanap guide structure, tag with tone, trigger scans |
| Silence detection | False positives (Pitfall 6) | Prefer file-based chapters, two-pass detection, generous duration |
| Large files | Resource exhaustion (Pitfall 8) | Pre-flight disk check, sequential processing, memory limits |
| Cross-platform paths | Unicode normalization (Pitfall 7) | Normalize to NFC, avoid mixed client access |

---

## Sources

- [m4b-tool (sandreas)](https://github.com/sandreas/m4b-tool) -- chapter handling, concat behavior
- [tone CLI (sandreas)](https://github.com/sandreas/tone) -- metadata tagging capabilities
- [Creating M4B with FFmpeg (myByways)](https://www.mybyways.com/blog/creating-an-audiobook-m4b-with-ffmpeg) -- chapter metadata format
- [seanap Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide) -- folder structure, Audnexus setup
- [Audnexus / Audiobooks.bundle](https://github.com/seanap/Audiobooks.bundle) -- Plex metadata agent
- [mergerfs NFS permission issue #1218](https://github.com/trapexit/mergerfs/issues/1218) -- root squash + rename failures
- [audiobookshelf matching bug #4277](https://github.com/advplyr/audiobookshelf/issues/4277) -- wrong book matching
- [beets-audible](https://github.com/Neurrone/beets-audible) -- series numbering, path templates
- [BashPitfalls (wooledge.org)](https://mywiki.wooledge.org/BashPitfalls) -- comprehensive bash pitfall list
- [SignalTrap (wooledge.org)](https://mywiki.wooledge.org/SignalTrap) -- bash signal handling
- [FFmpeg silencedetect docs](https://ayosec.github.io/ffmpeg-filters-docs/7.1/Filters/Audio/silencedetect.html) -- filter parameters
- [Unicode normalization and APFS (Eclectic Light)](https://eclecticlight.co/2021/05/08/explainer-unicode-normalization-and-apfs/) -- NFC/NFD filesystem behavior
- [convmv](https://linux.extremeoverclocking.com/man/1/convmv) -- filename encoding conversion
