# Technology Stack

**Project:** Audiobook Pipeline
**Researched:** 2026-02-20

## Recommended Stack

### Core Tools

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| ffmpeg | 6.1.1 (on LXC) | MP3 concat, AAC encoding, M4B muxing | Already installed. Industry standard. Handles all audio conversion. |
| tone | v0.2.5 | M4B metadata tagging (title, author, narrator, series, cover, chapters) | Single binary, no dependencies, built for audiobooks, successor to m4b-tool by same author |
| Audnexus API | hosted at api.audnex.us | Audible metadata lookup (title, narrator, series, cover art, description, chapters) | Free JSON API, no auth required for book data, millisecond chapter timestamps |
| mp4v2-utils | 2.0.0 (apt) | Chapter embedding via mp4chaps | Reliable chapter injection into M4B containers, used by m4b-tool internally |
| Bash | 5.x | Pipeline orchestration | Matches existing scripts, no runtime dependencies, runs natively on LXC 210 |

### Supporting Tools

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| ffprobe | (bundled with ffmpeg) | Detect source bitrate, sample rate, duration, existing metadata | Every file -- determines encoding parameters |
| jq | apt | Parse Audnexus JSON responses in bash scripts | All metadata lookups |
| curl | apt | HTTP calls to Audnexus API | All metadata lookups |
| mediainfo | (on LXC) | Secondary metadata inspection, chapter verification | Validation/debugging |

## Tool Deep Dives

### tone CLI (v0.2.5) -- PRIMARY METADATA TAGGER

**Confidence:** HIGH (verified via GitHub, releases page, README)

tone is a cross-platform audio tagger written in C# by sandreas (same author as m4b-tool). Deployed as a single static binary -- no .NET runtime, no dependencies.

**Supported metadata fields for M4B:**
- `--meta-title`, `--meta-artist` (author), `--meta-album`
- `--meta-album-artist`, `--meta-composer` (narrator)
- `--meta-genre`, `--meta-description`, `--meta-long-description`
- `--meta-recording-date` (year)
- `--meta-comment`, `--meta-copyright`
- Movement/part fields for series: `--meta-movement`, `--meta-movement-name`
- Custom fields via reserved namespace: `----:com.pilabor.tone:AUDIBLE_ASIN`
- Cover art: `--auto-import=covers` (reads cover.jpg/cover.png from same directory)
- Chapters: `--auto-import=chapters` (reads chapters.txt in ChptFmtNative format)

**Key commands:**

```bash
# Dump all metadata from a file
tone dump audiobook.m4b

# Tag with basic metadata
tone tag audiobook.m4b \
  --meta-title="Harry Potter and the Sorcerer's Stone" \
  --meta-artist="J.K. Rowling" \
  --meta-album-artist="J.K. Rowling" \
  --meta-composer="Jim Dale" \
  --meta-genre="Fantasy" \
  --meta-recording-date="1999" \
  --meta-description="Harry Potter has never even heard of Hogwarts..."

# Tag with cover art and chapters from files
tone tag audiobook.m4b \
  --auto-import=covers \
  --auto-import=chapters

# Batch tag using path patterns
tone tag --auto-import=covers --auto-import=chapters \
  --path-pattern="audiobooks/%g/%a/%s/%p - %n.m4b" audiobooks/

# Custom JavaScript tagger (advanced)
tone tag audiobook.m4b --script=my-tagger.js --script-tagger-parameter="key=value"
```

**Chapter file format (chapters.txt):**
```
00:00:00.000 Opening Credits
00:00:30.924 Chapter 1: The Boy Who Lived
00:29:23.578 Chapter 2: The Vanishing Glass
```

**Installation on Ubuntu 24.04 (LXC 210):**
```bash
wget https://github.com/sandreas/tone/releases/download/v0.2.5/tone-0.2.5-linux-x64.tar.gz
tar xzf tone-0.2.5-linux-x64.tar.gz
sudo mv tone /usr/local/bin/
tone --help  # verify
```

**Important note:** tone should operate on local block storage. NFS mounts can cause issues. Copy files locally for tagging, then move to NFS destination.

**Does tone scrape Audible directly?** No. tone is purely a tagger -- it reads/writes metadata to audio files. It does NOT fetch metadata from the internet. You need to get metadata from Audnexus (or another source) and pipe it into tone commands. The JavaScript tagger extension system could theoretically call an API, but the JS runtime is sandboxed and limited.

---

### Audnexus API -- METADATA SOURCE

**Confidence:** HIGH (verified via live API calls, confirmed response structure)

Public JSON API at `https://api.audnex.us`. No authentication required for book/author data. Chapters require Audible device tokens (ADP_TOKEN, PRIVATE_KEY) when self-hosting, but the public API serves cached chapters.

**Endpoints:**

```bash
# Book metadata by ASIN
curl "https://api.audnex.us/books/{ASIN}"

# Chapter data by ASIN (with millisecond offsets)
curl "https://api.audnex.us/books/{ASIN}/chapters"

# Author by ASIN
curl "https://api.audnex.us/authors/{ASIN}"

# Author search by name
curl "https://api.audnex.us/authors?name=Stephen+King&region=us"
```

**Book response fields** (verified via live call):
```json
{
  "asin": "B017V4IM1G",
  "title": "Harry Potter and the Sorcerer's Stone",
  "subtitle": "...",
  "authors": [{"asin": "B000AP9A6K", "name": "J.K. Rowling"}],
  "narrators": [{"name": "Jim Dale"}],
  "description": "...",
  "summary": "<p>HTML formatted long description</p>",
  "image": "https://m.media-amazon.com/images/I/xxxxx.jpg",
  "genres": [{"asin": "...", "name": "Children's Audiobooks", "type": "genre"}],
  "publisherName": "...",
  "releaseDate": "2015-11-20T00:00:00.000Z",
  "copyright": 1997,
  "rating": "4.9",
  "runtimeLengthMin": 497,
  "formatType": "unabridged",
  "language": "english",
  "region": "us"
}
```

**Chapter response** (verified via live call):
```json
{
  "asin": "B017V4IM1G",
  "brandIntroDurationMs": 3924,
  "brandOutroDurationMs": 4945,
  "chapters": [
    {"lengthMs": 30924, "startOffsetMs": 0, "startOffsetSec": 0, "title": "Opening Credits"},
    {"lengthMs": 1732654, "startOffsetMs": 30924, "startOffsetSec": 30, "title": "Chapter 1: The Boy Who Lived"},
    {"lengthMs": 1306377, "startOffsetMs": 1763578, "startOffsetSec": 1763, "title": "Chapter 2: The Vanishing Glass"}
  ]
}
```

**Rate limits:** 100 requests per minute from a single source. More than sufficient for batch processing.

**Critical limitation:** You need the ASIN. The API does NOT support free-text book search. You must either:
1. Know the ASIN upfront (from Readarr, which stores it)
2. Parse it from an Audible URL
3. Use a different search mechanism to find the ASIN first

**ASIN sourcing strategy:** Readarr stores the Audible ASIN in its database. The post-import webhook can include it. For manual/cron runs, the pipeline will need a way to look up ASINs -- possibly by searching Audible's website or using the `audible-cli` Python tool.

---

### ffmpeg -- AUDIO CONVERSION

**Confidence:** HIGH (well-documented, already on LXC)

**MP3 to M4B conversion pipeline:**

```bash
# Step 1: Detect source bitrate
SOURCE_BITRATE=$(ffprobe -v error -select_streams a:0 \
  -show_entries stream=bit_rate -of csv=p=0 input.mp3)
# Apply 128k floor, 256k ceiling for AAC
TARGET_BITRATE=$(echo "$SOURCE_BITRATE" | awk '{
  br = int($1/1000);
  if (br < 128) br = 128;
  if (br > 256) br = 256;
  print br "k"
}')

# Step 2a: Single MP3 to M4B
ffmpeg -i input.mp3 -c:a aac -b:a "$TARGET_BITRATE" \
  -movflags +faststart -f mp4 output.m4b

# Step 2b: Multiple MP3s to M4B (concat)
# Create file list
for f in *.mp3; do echo "file '$f'"; done > filelist.txt
# Concat and convert
ffmpeg -f concat -safe 0 -i filelist.txt \
  -c:a aac -b:a "$TARGET_BITRATE" \
  -movflags +faststart -f mp4 output.m4b

# Step 3: Embed cover art
ffmpeg -i output.m4b -i cover.jpg \
  -map 0:a -map 1:v -c:a copy -c:v mjpeg \
  -disposition:v:0 attached_pic \
  -movflags +faststart output_with_cover.m4b

# Step 4: Add chapter metadata file
ffmpeg -i output.m4b -i chapters.ffmeta \
  -map_metadata 1 -c copy \
  -movflags +faststart output_with_chapters.m4b
```

**ffmpeg chapter metadata format (FFMETADATA1):**
```ini
;FFMETADATA1
[CHAPTER]
TIMEBASE=1/1000
START=0
END=30924
title=Opening Credits

[CHAPTER]
TIMEBASE=1/1000
START=30924
END=1763578
title=Chapter 1: The Boy Who Lived
```

**Key flags:**
- `-movflags +faststart` -- moves moov atom to file start for faster seeking. Always use this.
- `-vn` -- strip problematic embedded album art from MP3s before processing
- `-f mp4` -- M4B is just MP4 with a different extension
- AAC encoder: default `aac` is fine. `libfdk_aac` is marginally better but requires special ffmpeg build. Not worth the complexity.

---

### mp4v2-utils -- CHAPTER TOOLS

**Confidence:** HIGH (standard Ubuntu package)

```bash
sudo apt install mp4v2-utils
```

Provides `mp4chaps`, `mp4art`, `mp4info`, `mp4tags`.

```bash
# Import chapters from chapters.txt (Nero format)
mp4chaps -i audiobook.m4b

# List chapters
mp4chaps -l audiobook.m4b

# Add cover art
mp4art --add cover.jpg audiobook.m4b

# View file info
mp4info audiobook.m4b
```

**Chapter format for mp4chaps (Nero):**
```
00:00:00.000 Opening Credits
00:00:30.924 Chapter 1: The Boy Who Lived
00:29:23.578 Chapter 2: The Vanishing Glass
```

**When to use vs ffmpeg chapters:** mp4chaps is simpler for adding chapters to an existing M4B without re-muxing. ffmpeg chapter metadata requires a re-mux step. Use mp4chaps for post-processing chapter insertion.

---

### Chapter Detection -- FOR SINGLE-FILE AUDIOBOOKS

**Confidence:** MEDIUM (multiple approaches, each with tradeoffs)

When Audnexus chapters are available (book has an ASIN), use those -- they're authoritative and precise. Chapter detection is only needed for books without Audible metadata.

**Option 1: Audnexus chapters (preferred)**
- Millisecond precision from Audible's own chapter data
- Convert `startOffsetMs` to `HH:MM:SS.mmm` format for chapters.txt
- No processing time, no accuracy concerns

**Option 2: m4b-tool silence detection (fallback)**
- Built-in `--max-chapter-length` with configurable silence thresholds
- `--silence-min-length 1000` (1 second minimum silence)
- Docker: `docker run sandreas/m4b-tool:latest`
- Mature, battle-tested in audiobook community

**Option 3: Chapterize-Audiobooks (ML-based, complex)**
- Uses Vosk speech-to-text to find "Chapter X" announcements
- Python 3.10+, large ML model download (~1GB)
- Better accuracy than silence detection when narrator announces chapters
- Slower (must transcribe entire audiobook)
- Last release: no recent activity, appears unmaintained

**Recommendation:** Use Audnexus chapters when ASIN is known (90%+ of cases). Fall back to m4b-tool silence detection for unknowns. Skip Chapterize-Audiobooks -- the ML approach is overkill given Audnexus provides chapter data directly.

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Metadata tagger | tone v0.2.5 | m4b-tool v0.4.2 (PHP) | tone is the successor by same author. Single binary vs PHP + composer + mp4v2 dependency chain. m4b-tool is great for merge/split but tone is cleaner for pure tagging. |
| Metadata tagger | tone v0.2.5 | Mp3tag | GUI app, not scriptable in a headless pipeline |
| Metadata source | Audnexus API | Direct Audible scraping | Audnexus is a JSON API vs parsing HTML. Cached, faster, more reliable. |
| Metadata source | Audnexus API | audible-cli (Python) | audible-cli requires an Audible account login. Audnexus is unauthenticated for book data. |
| MP3-to-M4B | ffmpeg direct | m4b-tool merge | m4b-tool wraps ffmpeg anyway. Direct ffmpeg gives us full control over encoding params. Fewer dependencies. |
| MP3-to-M4B | ffmpeg direct | m4b-merge (Python) | m4b-merge is excellent but pulls in m4b-tool + Python + pip. Overengineered for our use case since we're building the pipeline ourselves. |
| Chapter detection | Audnexus + m4b-tool fallback | Chapterize-Audiobooks | ML model is 1GB, slow, unmaintained. Audnexus chapters are authoritative. |
| Chapter embedding | mp4chaps (mp4v2-utils) | ffmpeg -i chapters.ffmeta | mp4chaps is simpler for post-hoc chapter insertion without re-muxing |
| Orchestration | Bash | Python | Bash matches existing scripts, no runtime to install, simpler for a pipeline that's mostly invoking CLI tools |

## Installation

```bash
# On LXC 210 (Ubuntu 24.04)

# Core tools (ffmpeg likely already installed)
sudo apt update
sudo apt install -y ffmpeg mp4v2-utils jq curl mediainfo

# tone CLI
wget https://github.com/sandreas/tone/releases/download/v0.2.5/tone-0.2.5-linux-x64.tar.gz
tar xzf tone-0.2.5-linux-x64.tar.gz
sudo mv tone /usr/local/bin/tone
sudo chmod +x /usr/local/bin/tone
rm -rf tone-0.2.5-linux-x64.tar.gz

# Verify
ffmpeg -version | head -1
tone --help | head -3
mp4chaps --version
jq --version
```

**Optional (only if silence-based chapter detection needed):**
```bash
# m4b-tool via Docker (for silence detection fallback)
# Requires Docker on LXC 210
docker pull sandreas/m4b-tool:latest
alias m4b-tool='docker run -it --rm -u $(id -u):$(id -g) -v "$(pwd)":/mnt sandreas/m4b-tool:latest'

# OR install directly (requires PHP)
sudo apt install -y php-cli php-mbstring php-xml php-intl fdkaac
sudo wget https://github.com/sandreas/m4b-tool/releases/download/v.0.4.2/m4b-tool.phar \
  -O /usr/local/bin/m4b-tool
sudo chmod +x /usr/local/bin/m4b-tool
```

## ASIN Discovery -- The Missing Piece

The biggest gap in the stack is ASIN lookup. Audnexus requires an ASIN but doesn't offer text search. Strategies:

1. **Readarr integration** (best): Readarr likely stores ASINs from its Audible/GoodReads metadata sources. The post-import webhook payload may include it, or we can query Readarr's API.

2. **Manual ASIN entry**: For manual/cron runs, accept ASIN as a parameter or read from a `.asin` file dropped alongside the audiobook.

3. **audible-cli search** (fallback): Python tool that can search Audible by title/author and return ASINs. Requires Audible account auth. Install: `pip install audible-cli`.

4. **Audible website scraping**: Search `audible.com/search?keywords=...` and parse ASIN from results. Fragile, not recommended.

**Recommendation:** Start with Readarr API integration for ASIN. Add manual `.asin` file support. Defer audible-cli as a Phase 2 enhancement.

## Sources

- [tone GitHub - sandreas/tone](https://github.com/sandreas/tone) -- README, releases (HIGH confidence)
- [tone releases v0.2.5](https://github.com/sandreas/tone/releases) -- installation binaries (HIGH confidence)
- [Audnexus GitHub - laxamentumtech/audnexus](https://github.com/laxamentumtech/audnexus) -- API documentation (HIGH confidence)
- [Audnexus API](https://api.audnex.us) -- live API verified with curl (HIGH confidence)
- [m4b-tool GitHub - sandreas/m4b-tool](https://github.com/sandreas/m4b-tool) -- merge/split/chapter features (HIGH confidence)
- [m4b-merge PyPI](https://pypi.org/project/m4b-merge/) -- Python wrapper (MEDIUM confidence)
- [Chapterize-Audiobooks GitHub](https://github.com/patrickenfuego/Chapterize-Audiobooks) -- ML chapter detection (MEDIUM confidence)
- [ffmpeg MP3 to M4B gist](https://gist.github.com/butuzov/fa7d456ebc3ec0493c0a10b73800bf42) -- conversion examples (MEDIUM confidence)
- [Felipe Martin blog - M4B creation](https://fmartingr.com/blog/2024/03/12/create-an-audiobook-file-from-several-mp3-files-using-ffmpeg/) -- practical walkthrough (MEDIUM confidence)
