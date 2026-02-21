# Feature Landscape

**Domain:** Automated audiobook processing pipeline (MP3 to M4B with metadata enrichment)
**Researched:** 2026-02-20

## Table Stakes

Features users expect. Missing = pipeline feels incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| MP3-to-M4B conversion | Core purpose of the pipeline | Medium | Use m4b-tool (wraps ffmpeg + mp4v2) |
| Chapter markers | M4B without chapters is just a renamed M4A | Medium | Multiple strategies -- see Chapter Detection below |
| Metadata tagging (title, author, narrator) | Plex/Prologue need ALBUMARTIST + ALBUM minimum | Medium | Audible API for enrichment |
| Cover art embedding | Every audiobook player shows cover art | Low | Embed in M4B + save cover.jpg alongside |
| Plex-compatible folder structure | Entire point is Plex integration | Low | seanap convention -- see Folder Structure below |
| Series organization | Most audiobooks are series; no series = messy library | Low | Series name + part number in folder + tags |
| Automatic trigger on new downloads | Manual runs defeat the purpose of a pipeline | Medium | Readarr/Bookshelf post-import hook or folder watch |
| Idempotent processing | Re-running shouldn't duplicate or corrupt | Medium | Track processed files, skip already-done |

## Differentiators

Features that elevate beyond basic conversion scripts.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Audible metadata matching | Rich metadata (description, genres, narrator, series info) beyond filename parsing | High | Fuzzy title+author search via Audible catalog API |
| Bitrate-aware transcoding | Preserve quality for high-bitrate sources, don't upscale low-bitrate | Low | Simple conditional logic on source bitrate |
| Multi-file MP3 merge | Many audiobooks arrive as 50+ MP3 files | Medium | m4b-tool merge handles this natively |
| Backup original files | Safety net before destructive conversion | Low | Copy to /original before processing |
| Processing queue with status | Know what's processing, what failed, what's done | Medium | Simple SQLite or file-based state tracking |
| Notification on completion/failure | Know when books are ready or need attention | Low | Webhook to existing notification stack |
| Manual override for metadata | Fuzzy matching will get it wrong sometimes | Medium | CLI flag or config file to force ASIN |
| Dry-run mode | Preview what would happen without doing it | Low | Essential for debugging and trust-building |

## Anti-Features

Features to explicitly NOT build.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Web UI for management | Scope creep; Audiobookshelf already exists for this | CLI + config files; use Audiobookshelf if you want a UI |
| DRM removal | Legal minefield, not needed for this use case | Assume input is already DRM-free MP3 |
| Audiobook downloading/acquisition | Readarr/Bookshelf handles this | Trigger on post-import, don't replicate download logic |
| Multi-format output (FLAC, OGG, etc.) | M4B is the standard; supporting others adds complexity for no gain | M4B only, with M4A as trivial alias |
| Real-time streaming | That's Plex/Prologue's job | Just organize files correctly for Plex |
| Database-backed metadata cache | Overkill for a processing pipeline | File-based state (e.g., `.processed` marker files or simple JSON) |

---

## Feature Deep Dives

### 1. Plex Audiobook Folder Structure

**Source:** [seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide) (1.7k stars, actively maintained)
**Confidence:** HIGH

The canonical folder structure for Plex audiobooks:

```
/Audiobooks/
  Author Name/
    Series Name/
      YYYY - Book Title/
        Book Title (YYYY).m4b
        cover.jpg
        desc.txt
        reader.txt
```

For standalone books (no series):

```
/Audiobooks/
  Author Name/
    YYYY - Book Title/
      Book Title (YYYY).m4b
      cover.jpg
      desc.txt
      reader.txt
```

**Critical tags for Plex recognition:**

| Tag | Field | Maps To |
|-----|-------|---------|
| ALBUMARTIST (TPE2) | Author name | Plex "Artist" / book author |
| ALBUM (TALB) | Book title | Plex "Album" / book title |
| COMPOSER (TCOM) | Narrator | Used by Prologue |
| GENRE (TCON) | Genre(s) | Up to 6 genres |
| ALBUMSORT | `Series Part - Title` | Sort order within series |
| SERIES (custom) | Series name | Used by Plex Audiobooks agent |
| SERIES-PART (custom) | Book number | Used by Plex Audiobooks agent |

**Companion files generated per book:**
- `cover.jpg` -- album artwork (pulled from Audible)
- `desc.txt` -- publisher summary/description
- `reader.txt` -- narrator name

**Prologue specifics:**
- Reads M4B chapter markers natively -- this is why M4B matters
- Uses ALBUMARTIST for author, ALBUM for title, COMPOSER for narrator
- Free app with $5 IAP for offline downloads
- Supports iPhone, iPad, Apple Watch, CarPlay

**Key insight:** Plex itself doesn't display M4B chapters. Prologue (and BookCamp) read them directly from the file. So chapters are essential even though Plex ignores them.

### 2. Readarr/Bookshelf Integration

**Source:** [Servarr Wiki](https://wiki.servarr.com/readarr/custom-scripts), community reports
**Confidence:** MEDIUM (Readarr retired June 2025; Bookshelf fork inherits same API)

**IMPORTANT: Readarr was retired June 27, 2025.** The [Bookshelf fork](https://github.com/pennydreadful/bookshelf) is the active replacement. It inherits Readarr's custom script system with identical environment variables.

**Trigger mechanism: Settings -> Connect -> Custom Script**

Configure to fire on:
- **On Release Import** -- when a book is downloaded and imported
- **On Upgrade** -- when a better quality version replaces existing

**Environment variables passed to custom scripts:**

| Variable | Description |
|----------|-------------|
| `readarr_eventtype` | Event type: `Download`, `Rename`, `Test` |
| `readarr_addedbookpaths` | Full path(s) to imported files, `\|` separated (UNDOCUMENTED but critical) |
| `readarr_book_id` | Internal Readarr/Bookshelf book ID |
| `readarr_author_name` | Author name |
| `readarr_book_title` | Book title |

**Gotchas:**
- `readarr_addedbookpaths` is undocumented but is the most useful variable
- It sometimes doesn't populate on manual imports -- works reliably for automated downloads
- The `Test` event fires when you click "Test" in the UI; script must handle this gracefully
- `readarr_addedbookpaths` may use `|` as separator for multi-file imports

**Alternative trigger: Folder watching (cron or inotify)**

If Readarr/Bookshelf integration is unreliable, use polling:

```bash
# Cron approach: check every 5 minutes for new files
*/5 * * * * /path/to/process-new-audiobooks.sh /path/to/incoming/
```

Or use the [auto-m4b Docker container](https://github.com/seanap/auto-m4b) approach which polls every N minutes via `SLEEPTIME` env var. It does NOT use inotify -- it's a simple sleep loop.

**Recommended approach:** Use Readarr/Bookshelf post-import hook as primary trigger, with cron-based folder scan as fallback. The cron scan catches anything the hook misses and handles manual additions.

### 3. Chapter Detection Strategies

**Source:** [sandreas/m4b-tool](https://github.com/sandreas/m4b-tool), community testing
**Confidence:** HIGH

Three strategies, ranked by reliability:

#### Strategy A: One-file-per-chapter (BEST for multi-MP3 books)

When an audiobook arrives as multiple MP3 files (the common case), each file typically IS a chapter. m4b-tool's merge command uses each input file as a chapter by default.

```bash
m4b-tool merge "input_folder/" --output-file="output.m4b"
```

Each MP3 becomes a chapter named after its filename. This is the most reliable approach and requires zero configuration.

**When it works:** Multi-file MP3 audiobooks (most common case)
**When it fails:** Single-file audiobooks, files that don't map 1:1 to chapters

#### Strategy B: Silence detection (BEST for single-file audiobooks)

Uses ffmpeg's `silencedetect` filter to find pauses between chapters.

```bash
m4b-tool chapters --adjust-by-silence \
  --silence-min-length=300 \
  --silence-max-length=900 \
  -o "output.m4b" "input.m4b"
```

Parameters:
- `--silence-min-length=300` -- minimum 5 minutes between chapter marks
- `--silence-max-length=900` -- maximum 15 minutes between chapter marks
- If no silence detected in range, hard-cuts every 5 minutes as fallback

**When it works:** Single-file audiobooks with clear pauses between chapters
**When it fails:** Audiobooks with music transitions, short pauses, or no clear chapter boundaries

#### Strategy C: MusicBrainz lookup (NICHE -- only for well-known titles)

```bash
m4b-tool chapters --merge "input/" \
  --use-musicbrainz
```

Looks up chapter data from MusicBrainz database. Only works for popular titles (Harry Potter, etc.). Unreliable for most audiobooks.

**When it works:** Popular, well-cataloged audiobooks
**When it fails:** Anything obscure, self-published, or not in MusicBrainz

#### Recommended pipeline logic:

```
IF input is multiple files:
  Use file-per-chapter (Strategy A)
ELSE IF single file:
  Try silence detection (Strategy B)
  Fall back to fixed-interval chapters (every 5 min)
```

### 4. Metadata Matching

**Source:** [Audible API docs](https://audible.readthedocs.io/en/latest/misc/external_api.html), [Audiobookshelf matching](https://deepwiki.com/advplyr/audiobookshelf/8.3-metadata-extraction-and-matching), [beets-audible](https://github.com/Neurrone/beets-audible)
**Confidence:** MEDIUM (API is undocumented, can break)

**Audible's catalog search API** is the primary metadata source:

```
GET /1.0/catalog/products?
  title={title}&
  author={author}&
  num_results=5&
  products_sort_by=Relevance&
  response_groups=product_desc,product_attrs,media,contributors,series
```

**Matching strategy (ordered by reliability):**

1. **ASIN direct lookup** (if available) -- 100% reliable
   - Parse ASIN from folder name: `Book Title [B00XXXXXX]/`
   - Endpoint: `/1.0/catalog/products/{ASIN}`

2. **Title + Author search** -- ~85% reliable
   - Search `/1.0/catalog/products?title=X&author=Y`
   - Take first result sorted by Relevance
   - Verify by comparing runtime (within 10% tolerance)

3. **Keywords search** (fallback) -- ~60% reliable
   - Search `/1.0/catalog/products?keywords=title+author`
   - More forgiving of misspellings but more false positives

**Metadata fields available from Audible:**

| Field | Response Group | Use |
|-------|---------------|-----|
| title, subtitle | product_attrs | ALBUM tag |
| authors[] | contributors | ALBUMARTIST tag |
| narrators[] | contributors | COMPOSER tag, reader.txt |
| publisher_name | product_attrs | PUBLISHER tag |
| release_date | product_attrs | YEAR tag, folder name |
| runtime_length_min | product_attrs | Verification against actual duration |
| product_images | media | cover.jpg |
| publisher_summary | product_desc | desc.txt |
| series[] (name, position) | series | SERIES, SERIES-PART tags |
| thesaurus_subject_keywords | product_attrs | GENRE tag |
| language | product_attrs | Filtering non-English results |

**Known issues:**
- ASIN is case-sensitive -- always use uppercase (bug in Audnexus converts to lowercase)
- API is undocumented and can change without notice
- Some titles return wrong matches (especially reissued/discontinued titles)
- Multiple Audible regions (.com, .co.uk, .de) have different catalogs

**Audnexus API** (`https://api.audnex.us`) -- third-party enrichment layer used by Audiobookshelf and Plex agents. Provides chapter data, author photos, and normalized metadata. Free, no auth required.

### 5. Bitrate Handling

**Source:** Community best practices, [Hydrogenaudio](https://hydrogenaudio.org/index.php/topic,32153.0.html)
**Confidence:** HIGH

**Decision matrix for MP3-to-AAC transcoding:**

| Source MP3 Bitrate | Action | AAC Output Bitrate | Rationale |
|-------------------|--------|-------------------|-----------|
| <= 64 kbps | DO NOT transcode | Keep as MP3 | Lossy-to-lossy at low bitrate = audible degradation |
| 96 kbps | Transcode | 64 kbps mono AAC | AAC is ~2x more efficient than MP3 for speech |
| 128 kbps | Transcode | 64 kbps mono AAC | Sweet spot: comparable quality, half the size |
| 192+ kbps | Transcode | 96 kbps mono AAC | Diminishing returns above 96k for speech |
| VBR (variable) | Transcode | 64 kbps mono AAC | Use average bitrate to decide |

**Key principles:**
- **64 kbps mono AAC** is the audiobook sweet spot -- sounds as good as 128 kbps MP3 for speech
- **Never transcode low-bitrate sources** (<= 64 kbps) -- generation loss is audible
- **Mono, not stereo** -- audiobooks are spoken word; stereo doubles file size for no benefit
- **Use libfdk_aac** encoder if available (slightly better quality than ffmpeg's default `aac`)
- **Sample rate: 44100 Hz** -- standard; no need to resample unless source is exotic

**m4b-tool merge with quality settings:**

```bash
m4b-tool merge "input/" \
  --output-file="output.m4b" \
  --audio-bitrate=64k \
  --audio-channels=1 \
  --audio-samplerate=44100 \
  --jobs=4
```

**File size estimates at 64 kbps mono:**
- 10-hour audiobook: ~280 MB
- 20-hour audiobook: ~560 MB
- 40-hour audiobook (epic fantasy): ~1.1 GB

### 6. M4B Format Details

**Source:** [m4b-tool](https://github.com/sandreas/m4b-tool), [myByways FFmpeg guide](https://www.mybyways.com/blog/creating-an-audiobook-m4b-with-ffmpeg)
**Confidence:** HIGH

**What M4B actually is:**
- MPEG-4 container (same as .mp4, .m4a) with `.m4b` extension
- AAC audio codec inside (or ALAC for lossless, but nobody does this for audiobooks)
- The `.m4b` extension is a hint to players that this is an audiobook (enables bookmarking in Apple ecosystem)
- Supports chapters, cover art, and rich metadata natively

**Chapter marker format:**
M4B uses the MP4 chapter atom format. Two standards exist:

| Format | Tool Support | Notes |
|--------|-------------|-------|
| Nero chapters (mp4v2) | m4b-tool, mp4chaps | Most common for audiobooks |
| QuickTime chapters | ffmpeg | Apple-native, works everywhere |

m4b-tool uses mp4v2's `mp4chaps` to write Nero-style chapters. ffmpeg can write QuickTime-style chapters via metadata file:

```
;FFMETADATA1
[CHAPTER]
TIMEBASE=1/1000
START=0
END=259153
title=Chapter 1

[CHAPTER]
TIMEBASE=1/1000
START=259153
END=519000
title=Chapter 2
```

**Cover art embedding:**
- m4b-tool automatically embeds `cover.jpg`/`cover.png` found in the input directory
- Also write `cover.jpg` alongside the M4B for Plex/Booksonic/Audiobookshelf
- Maximum recommended size: 500x500 to 1000x1000 px (larger works but wastes space)
- Format: JPEG preferred (smaller than PNG for photos)

**Tag standards (MP4/M4B atoms):**

| Atom | Tag | Maps To |
|------|-----|---------|
| aART | Album Artist | Author |
| \xa9alb | Album | Book Title |
| \xa9wrt | Composer | Narrator |
| \xa9gen | Genre | Genre |
| \xa9day | Year | Release year |
| desc | Description | Publisher summary |
| covr | Cover Art | Embedded image |
| ---- (freeform) | Series | Custom atom via mp4v2 |
| ---- (freeform) | Series-Part | Custom atom via mp4v2 |

**m4b-tool's custom mp4v2 fork** adds `--series` and `--series-part` flags to `mp4tags` for writing series metadata that Plex Audiobooks agent reads.

**Tools required:**
- `ffmpeg` -- audio conversion engine
- `mp4v2` -- chapter and tag manipulation (m4b-tool uses custom fork)
- `fdkaac` (optional) -- higher quality AAC encoder
- `m4b-tool` -- PHP wrapper orchestrating all of the above

**Installation on Debian/Ubuntu (LXC 210):**

```bash
# Via Docker (recommended)
docker pull sandreas/m4b-tool:latest

# Or native install
apt install ffmpeg
# mp4v2 from source or sandreas tap
```

---

## Feature Dependencies

```
Readarr/Bookshelf post-import hook
  --> File detection (what was downloaded, where)
    --> Metadata matching (Audible API lookup by title+author)
      --> MP3-to-M4B conversion (m4b-tool merge)
        --> Chapter detection (file-per-chapter or silence)
        --> Bitrate selection (based on source quality)
        --> Cover art embedding (from Audible)
        --> Tag writing (all metadata fields)
      --> Folder organization (Plex structure)
        --> Companion file generation (cover.jpg, desc.txt, reader.txt)
```

Key dependency: Metadata matching should happen BEFORE conversion so we can embed tags and cover art during the merge step rather than as a separate pass.

## MVP Recommendation

**Phase 1 -- Core conversion (get it working):**
1. MP3-to-M4B conversion with m4b-tool merge
2. File-per-chapter detection (multi-MP3 input)
3. Basic folder organization (Author/Title/)
4. Manual trigger (CLI invocation)

**Phase 2 -- Metadata enrichment:**
1. Audible API matching by title+author
2. Cover art, description, narrator metadata
3. Series detection and folder structure
4. Companion file generation (cover.jpg, desc.txt, reader.txt)

**Phase 3 -- Automation:**
1. Readarr/Bookshelf post-import hook
2. Cron-based folder scan fallback
3. Idempotent processing (skip already-done)
4. Notification on completion/failure

**Defer:**
- MusicBrainz chapter lookup -- too niche, not worth the complexity
- Web UI -- use CLI; Audiobookshelf exists if you want a UI
- Multi-region Audible search -- start with .com only, add regions if needed

## Sources

- [seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide) -- folder structure, naming conventions, Prologue compatibility
- [sandreas/m4b-tool](https://github.com/sandreas/m4b-tool) -- M4B conversion, chapter detection, tag writing
- [seanap/auto-m4b](https://github.com/seanap/auto-m4b) -- Docker-based auto-conversion pipeline pattern
- [Audible API docs (community)](https://audible.readthedocs.io/en/latest/misc/external_api.html) -- undocumented API endpoints
- [Audnexus API](https://github.com/laxamentumtech/audnexus) -- third-party Audible metadata enrichment
- [Servarr Wiki - Readarr Custom Scripts](https://wiki.servarr.com/readarr/custom-scripts) -- post-import hook environment variables
- [pennydreadful/bookshelf](https://github.com/pennydreadful/bookshelf) -- active Readarr fork
- [Neurrone/beets-audible](https://github.com/Neurrone/beets-audible) -- beets plugin for Audible matching patterns
- [Hydrogenaudio forums](https://hydrogenaudio.org/index.php/topic,32153.0.html) -- AAC bitrate recommendations
- [Prologue](https://prologue.audio/) -- Plex audiobook player with M4B chapter support
