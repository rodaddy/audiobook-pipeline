# audiobook-pipeline

Convert audio files to chaptered M4B audiobooks with rich metadata from the Audible catalog.

## Features

- **Multi-format input** -- MP3, FLAC, OGG, M4A, WMA
- **Chaptered M4B output** -- one file per book with chapter markers from source files
- **Dual metadata sources** -- Audible catalog API (primary) with Audnexus fallback
- **Rich metadata** -- cover art (up to 2400px), author/narrator, series info, subtitle, copyright, publisher, rating, genre taxonomy, ISBN
- **Accurate chapters** -- Audible API provides official chapter markers with exact timestamps
- **Plex-ready organization** -- `Author/Book (Year)/Book.m4b` folder structure
- **M4B enrichment** -- fix metadata and organize existing M4B files (skip conversion)
- **Idempotent processing** -- manifest-based state tracking with resume support
- **Automation ready** -- Readarr webhook, cron scanner, batch processing with `--no-lock`
- **Error recovery** -- categorized failures, automatic retries, failed/ directory quarantine
- **Hardware-accelerated encoding** -- AudioToolbox (macOS) when available, software AAC fallback

## Quick Start

```bash
# Clone and configure
git clone https://github.com/rodaddy/audiobook-pipeline.git
cd audiobook-pipeline
cp config.env.example config.env
# Edit config.env -- set your paths

# Install dependencies (see Installation section)

# Convert a directory of MP3s
bin/audiobook-convert /path/to/audiobook-mp3s/
```

## Installation

### Dependencies

| Tool | Purpose | macOS Install | Linux Install |
|------|---------|---------------|---------------|
| `ffmpeg` | Audio concat + AAC encoding | `brew install ffmpeg` | `apt install ffmpeg` |
| `jq` | JSON parsing and manipulation | `brew install jq` | `apt install jq` |
| `curl` | API calls (Audible, Audnexus) | Pre-installed | Pre-installed |
| `bc` | Duration arithmetic | Pre-installed | `apt install bc` |
| `xxd` | JPEG validation (cover art) | Pre-installed | `apt install xxd` |
| `tone` | M4B chapter + metadata tagging | See below | See below |

### Installing tone

`tone` is a specialized M4B tagging tool not available in package managers. Download from the [official release page](https://github.com/sandreas/tone).

```bash
# macOS (Intel)
wget https://github.com/sandreas/tone/releases/latest/download/tone-darwin-x64.tar.gz
tar -xzf tone-darwin-x64.tar.gz
sudo mv tone /usr/local/bin/
sudo chmod +x /usr/local/bin/tone

# macOS (Apple Silicon)
wget https://github.com/sandreas/tone/releases/latest/download/tone-darwin-arm64.tar.gz
tar -xzf tone-darwin-arm64.tar.gz
sudo mv tone /usr/local/bin/
sudo chmod +x /usr/local/bin/tone

# Linux (x86_64)
wget https://github.com/sandreas/tone/releases/latest/download/tone-linux-x64.tar.gz
tar -xzf tone-linux-x64.tar.gz
sudo mv tone /usr/local/bin/
sudo chmod +x /usr/local/bin/tone

# Verify installation
tone --version
```

### Setup

```bash
git clone https://github.com/rodaddy/audiobook-pipeline.git
cd audiobook-pipeline
cp config.env.example config.env
```

Edit `config.env` to configure paths for your system. At minimum:
- `WORK_DIR` -- temporary processing space
- `MANIFEST_DIR` -- manifest storage for idempotency
- `NFS_OUTPUT_DIR` -- your Plex/Audiobookshelf library root

## Configuration

Copy `config.env.example` to `config.env` and customize for your environment.

### Configuration Variables

**Directories**

| Variable | Default | Description |
|----------|---------|-------------|
| `WORK_DIR` | `/var/lib/audiobook-pipeline/work` | Temporary processing workspace |
| `MANIFEST_DIR` | `/var/lib/audiobook-pipeline/manifests` | Manifest storage for idempotency tracking |
| `OUTPUT_DIR` | `/var/lib/audiobook-pipeline/output` | Local output before NFS move |
| `LOG_DIR` | `/var/log/audiobook-pipeline` | Pipeline logs |
| `NFS_OUTPUT_DIR` | `/mnt/media/AudioBooks` | Library root for organized output (Plex/Audiobookshelf) |
| `ARCHIVE_DIR` | `/var/lib/audiobook-pipeline/archive` | Archive original source files after processing |

**Encoding**

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_BITRATE` | `128` | Cap output bitrate (kbps). Source bitrate matched up to this limit. |
| `CHANNELS` | `1` | Audio channels: 1=mono (recommended for speech), 2=stereo |

**Metadata**

| Variable | Default | Description |
|----------|---------|-------------|
| `METADATA_SOURCE` | `audible` | Primary metadata source: `audible` or `audnexus` (see below) |
| `AUDIBLE_REGION` | `com` | Audible API region (see Region Configuration below) |
| `AUDNEXUS_REGION` | `us` | Audnexus fallback region: `us`, `uk`, `au`, `ca`, `de`, `fr`, `jp`, `in`, `it`, `es` |
| `AUDNEXUS_CACHE_DIR` | `$WORK_DIR` | Metadata cache directory (defaults to work dir) |
| `AUDNEXUS_CACHE_DAYS` | `30` | Cache metadata responses for N days |
| `CHAPTER_DURATION_TOLERANCE` | `5` | Percent tolerance for chapter duration matching |
| `METADATA_SKIP` | `false` | Set `true` to skip metadata enrichment entirely |
| `FORCE_METADATA` | `false` | Set `true` to re-fetch metadata even if cached |

**Organization**

| Variable | Default | Description |
|----------|---------|-------------|
| `CREATE_COMPANION_FILES` | `true` | Deploy `cover.jpg`, `desc.txt`, `reader.txt` alongside M4B |

**Automation**

| Variable | Default | Description |
|----------|---------|-------------|
| `INCOMING_DIR` | `/mnt/media/AudioBooks/_incoming` | Cron scanner watches this directory for new books |
| `QUEUE_DIR` | `/var/lib/audiobook-pipeline/queue` | Queue directory for automation webhooks |
| `PROCESSING_DIR` | `/var/lib/audiobook-pipeline/processing` | Active processing marker directory |
| `COMPLETED_DIR` | `/var/lib/audiobook-pipeline/completed` | Completed book tracking |
| `FAILED_DIR` | `/var/lib/audiobook-pipeline/failed` | Quarantine directory for permanent failures |
| `PIPELINE_BIN` | `/opt/audiobook-pipeline/bin/audiobook-convert` | Path to conversion script for automation |
| `STABILITY_THRESHOLD` | `120` | Seconds -- cron scanner skips recently modified books |

**Error Recovery**

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_RETRIES` | `3` | Retry attempts before quarantine to `failed/` |
| `FAILURE_WEBHOOK_URL` | _(empty)_ | Slack/Discord webhook for failure notifications |

**Permissions**

| Variable | Default | Description |
|----------|---------|-------------|
| `FILE_OWNER` | _(empty)_ | chown target for output files (e.g., `1000:1000`). Leave empty to skip. |
| `FILE_MODE` | `644` | File permissions for output M4B files |
| `DIR_MODE` | `755` | Directory permissions for organized folders |

**Behavior**

| Variable | Default | Description |
|----------|---------|-------------|
| `DRY_RUN` | `false` | Preview mode -- show what would happen without making changes |
| `FORCE` | `false` | Re-process even if already completed |
| `VERBOSE` | `false` | Enable debug-level logging |
| `CLEANUP_WORK_DIR` | `true` | Delete work directory after successful completion |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARN`, `ERROR` |

### Metadata Source

The pipeline supports two metadata sources with automatic fallback:

#### `METADATA_SOURCE=audible` (default)

Fetches metadata from the **Audible catalog API** and normalizes to Audnexus-compatible format. Provides richer metadata:

- **Subtitle** -- book subtitle (not available in Audnexus)
- **Copyright** -- copyright statement with year
- **Publisher** -- publishing house name
- **ISBN** -- 10-digit ISBN (when available)
- **Rating** -- Audible customer rating (0.0-5.0)
- **Genre path** -- full category taxonomy (e.g., "Fiction / Fantasy / Epic")
- **Cover art** -- up to 2400x2400px (vs 500px from Audnexus)
- **Official chapters** -- chapter markers with exact timestamps from Audible's production data

Falls back to Audnexus if Audible API fails or returns no results.

#### `METADATA_SOURCE=audnexus`

Uses the **Audnexus API** directly (community-maintained Audible metadata mirror). Good for:

- **Plex users** with the Audnexus metadata agent installed
- **Rate limit avoidance** if processing large batches
- **Older books** that may have been removed from active Audible catalog

Falls back to Audible API if Audnexus returns no results.

#### Inline Override

Override metadata source for a single run without editing `config.env`:

```bash
METADATA_SOURCE=audnexus bin/audiobook-convert /path/to/book/
```

### Region Configuration

The `AUDIBLE_REGION` variable controls which Audible marketplace to query. Use the domain suffix for your region:

| Region | AUDIBLE_REGION | Audible URL |
|--------|----------------|-------------|
| United States | `com` | audible.com |
| United Kingdom | `co.uk` | audible.co.uk |
| Australia | `com.au` | audible.com.au |
| Canada | `ca` | audible.ca |
| Germany | `de` | audible.de |
| France | `fr` | audible.fr |
| Japan | `co.jp` | audible.co.jp |
| India | `in` | audible.in |
| Italy | `it` | audible.it |
| Spain | `es` | audible.es |

**Important:** The ASIN must exist in the target region's catalog. A book sold on audible.com may not be available on audible.co.uk with the same ASIN.

### Metadata Fields

The pipeline writes the following metadata tags to M4B files using `tone`:

| M4B Tag | tone Flag | Audible Source | Audnexus Source |
|---------|-----------|----------------|-----------------|
| Title | `--meta-title` | `.product.title` | `.title` |
| Artist | `--meta-artist` | `.product.authors[].name` (joined) | `.authors[].name` (joined) |
| Narrator | `--meta-narrator` | `.product.narrators[].name` (joined) | `.narrators[].name` (joined) |
| Album | `--meta-album` | `.product.series[0].title` | `.seriesPrimary.name` |
| Part | `--meta-part` | `.product.series[0].sequence` | `.seriesPrimary.position` |
| Movement Name | `--meta-movement-name` | `.product.series[0].title` | `.seriesPrimary.name` |
| Movement | `--meta-movement` | `.product.series[0].sequence` | `.seriesPrimary.position` |
| Content Group | `--meta-group` | "Series, Book #N" | "Series, Book #N" |
| Sort Album | `--meta-sort-album` | "Series 01 - Title" (zero-padded) | "Series 01 - Title" (zero-padded) |
| Genre | `--meta-genre` | `.category_ladders[].ladder[].name` (full path) | `.genres[0].name` |
| Description | `--meta-description` | `.product.publisher_summary` | `.description` or `.summary` |
| Long Description | `--meta-long-description` | `.product.publisher_summary` | _(not set)_ |
| Recording Date | `--meta-recording-date` | `.product.release_date` (ISO 8601) | `.releaseDate` (ISO 8601) |
| **Subtitle** | `--meta-subtitle` | `.product.subtitle` | _(not available)_ |
| **Copyright** | `--meta-copyright` | `.product.copyright` | _(not available)_ |
| **Publisher** | `--meta-publisher` | `.product.publisher_name` | _(not available)_ |
| **Album Artist** | `--meta-album-artist` | `.product.authors[0].name` (first with ASIN) | `.authors[0].name` |
| **Composer** | `--meta-composer` | `.product.narrators[].name` (joined) | `.narrators[].name` (joined) |
| **Popularity** | `--meta-popularity` | `.product.rating.overall_distribution.display_average_rating` | _(not available)_ |
| Cover Art | `--meta-cover-file` | `.product_images["2400"]` (2400x2400px) | `.image` (500x500px) |
| Chapters | `--meta-chapters-file` | `.product.content_metadata.chapter_info.chapters[]` | `.chapters[]` |
| iTunes Media Type | `--meta-itunes-media-type` | `"Audiobook"` | `"Audiobook"` |

**Custom fields** (stored but not displayed by most players):

| Field Name | Source |
|------------|--------|
| `AUDIBLE_ASIN` | ASIN for future re-runs |
| `AUDIBLE_URL` | Direct link to Audible product page |

## Usage

### Convert a directory of audio files

```bash
# Auto-detects directory input -> convert mode
bin/audiobook-convert /mnt/downloads/MyBook/

# With options
bin/audiobook-convert --verbose --force /mnt/downloads/MyBook/
```

Pipeline stages: `validate -> concat -> convert -> asin -> metadata -> organize -> archive -> cleanup`

### Enrich an existing M4B

```bash
# Auto-detects .m4b input -> enrich mode
bin/audiobook-convert /mnt/media/untagged-book.m4b
```

Skips conversion stages. Fetches metadata from configured source and organizes into your library.

### Metadata-only mode

```bash
bin/audiobook-convert --mode metadata /path/to/book.m4b
```

Fetches ASIN and applies metadata (cover art, author, narrator, series) without moving the file.

### Organize-only mode

```bash
bin/audiobook-convert --mode organize /path/to/book.m4b
```

Moves the file into the `Author/Book (Year)/Book.m4b` folder structure without touching metadata.

### Region-specific processing

```bash
# German audiobook
AUDIBLE_REGION=de bin/audiobook-convert /path/to/german-book/

# UK audiobook
AUDIBLE_REGION=co.uk bin/audiobook-convert /path/to/uk-book/
```

### Override metadata source

```bash
# Use Audnexus instead of Audible for this run
METADATA_SOURCE=audnexus bin/audiobook-convert /path/to/book.m4b

# Use Audible API for UK marketplace
AUDIBLE_REGION=co.uk METADATA_SOURCE=audible bin/audiobook-convert /path/to/book/
```

### Batch processing

```bash
# Process multiple books in parallel (each skips the global lock)
for dir in /mnt/downloads/*/; do
  bin/audiobook-convert --no-lock "$dir" &
done
wait
```

### Dry-run mode

```bash
# Preview what would happen without making changes
bin/audiobook-convert --dry-run --verbose /mnt/downloads/MyBook/
```

## Architecture

```
SOURCE_PATH (directory or .m4b file)
    |
    v
┌─────────────────────────────────────────────────────────┐
│ 01-validate  Find audio files, detect bitrate, check    │
│              disk space, write sorted file list          │
│                          |                               │
│ 02-concat    Generate ffmpeg concat list + FFMETADATA1  │
│              chapter file from per-file durations        │
│                          |                               │
│ 03-convert   Single-pass ffmpeg: concat + AAC encode +  │
│              chapter inject + faststart                  │
│                          |                               │
│ 05-asin      Discover ASIN via folder name, Readarr     │  Stages 01-03
│              API, or Audnexus/Audible search             │  skipped for
│                          |                               │  M4B input
│ 06-metadata  Fetch from Audible API (or Audnexus):      │  (enrich mode)
│              cover art (2400px), author, narrator,       │
│              series, subtitle, copyright, publisher,     │
│              rating, genre path, official chapters       │
│                          |                               │
│ 07-organize  Create Author/Book (Year)/ structure,      │
│              move M4B + companion files to library       │
│                          |                               │
│ 08-archive   Archive original source to archive/        │
│                          |                               │
│ 09-cleanup   Remove work directory, release locks        │
└─────────────────────────────────────────────────────────┘
    |
    v
NFS_OUTPUT_DIR/Author/Book (Year)/Book.m4b
```

### ASIN Discovery

The pipeline tries multiple sources to find an Audible ASIN (in priority order):

1. **Folder name pattern match** -- `{ASIN}` or `[ASIN]` in directory name
2. **Readarr API lookup** -- if configured, queries Readarr for the book's ASIN
3. **Audible/Audnexus search** -- searches by title/author extracted from folder name
4. **Manual entry prompt** -- interactive mode asks user to provide ASIN

**ASIN format:** Must be the **Audible ASIN** from the audible.com (or regional) URL, NOT the Amazon product ASIN. Example:

- Audible URL: `https://www.audible.com/pd/B084QHXYFP` -> ASIN: `B084QHXYFP` ✅
- Amazon URL: `https://www.amazon.com/dp/198009036X` -> Product ASIN: `198009036X` ❌

## Automation

### Readarr Webhook

Triggered when Readarr imports a new audiobook. Queues the book for processing.

```bash
# Readarr custom script (Settings -> Connect -> Custom Script)
/opt/audiobook-pipeline/bin/readarr-hook.sh
```

### Cron Scanner

Watches `INCOMING_DIR` for new audiobook directories. Skips recently modified books (based on `STABILITY_THRESHOLD`).

```bash
# Crontab example: run every 5 minutes
*/5 * * * * /opt/audiobook-pipeline/bin/cron-scanner.sh
```

### Queue Processor

Processes queued books sequentially (one at a time). Run as a systemd service or cron job.

```bash
# Systemd service (recommended)
# /etc/systemd/system/audiobook-queue.service
[Unit]
Description=Audiobook Pipeline Queue Processor
After=network.target

[Service]
Type=simple
ExecStart=/opt/audiobook-pipeline/bin/queue-processor.sh
Restart=always
User=audiobook
Environment="PATH=/usr/local/bin:/usr/bin:/bin"

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

### No metadata found

**Symptoms:** Pipeline logs `"No metadata found for ASIN XXX"` or `"Audible API returned invalid or empty response"`

**Causes:**
1. Wrong ASIN format -- used Amazon product ASIN instead of Audible ASIN
2. Book not available in the configured region
3. ASIN is invalid or has been removed from Audible catalog

**Solutions:**
- Verify ASIN is from the Audible URL (not Amazon). Check the URL: `https://www.audible.com/pd/[ASIN]`
- Check if the book exists in your configured `AUDIBLE_REGION` marketplace
- Try switching metadata source: `METADATA_SOURCE=audnexus bin/audiobook-convert ...`
- Try a different region if the book was purchased from a different marketplace: `AUDIBLE_REGION=co.uk bin/audiobook-convert ...`

### Cover art download failed

**Symptoms:** `"Failed to download cover art from Audible"` or `"Downloaded cover art is not a valid JPEG"`

**Causes:**
1. Audible API rate limiting
2. Network timeout or connection issue
3. Invalid or missing image URL in API response

**Solutions:**
- Pipeline automatically falls back to Audnexus for cover art if Audible fails
- Check network connectivity: `curl -I https://api.audible.com`
- Wait a few minutes and retry -- rate limits are usually temporary
- Verify the ASIN is correct and the book has cover art on audible.com

### Chapter duration mismatch

**Symptoms:** `"Chapter duration mismatch: expected XXXms, got YYYms"`

**Causes:**
1. Audible's official chapter markers don't match actual file duration (intro/outro credits, regional differences)
2. Source files were trimmed or edited

**Solutions:**
- Adjust `CHAPTER_DURATION_TOLERANCE` in `config.env` (default: 5%). Try `10` or `15` for books with significant intro/outro content
- Check source file integrity -- re-download if files were corrupted
- Use `--verbose` to see detailed chapter timestamp comparison
- If Audible chapters are consistently wrong for a book, fallback to file-based chapters by skipping the metadata stage: `bin/audiobook-convert --mode organize /path/to/book.m4b`

### Region mismatch

**Symptoms:** Metadata is incorrect, wrong narrator, or cover art doesn't match

**Causes:**
1. Book was purchased from a different regional Audible marketplace
2. Different editions exist across regions (US vs UK narrators, abridged vs unabridged)

**Solutions:**
- Check which Audible marketplace the book was purchased from
- Set `AUDIBLE_REGION` to match the purchase region: `AUDIBLE_REGION=co.uk bin/audiobook-convert ...`
- Search the book on multiple regional Audible sites to find the matching ASIN
- For UK books, use: `AUDIBLE_REGION=co.uk`
- For German books, use: `AUDIBLE_REGION=de`

### Pipeline stalls or hangs

**Symptoms:** Pipeline stops responding during processing, no log output

**Causes:**
1. ffmpeg encoding stalled (rare, usually hardware codec issue)
2. NFS mount is unresponsive
3. Disk full

**Solutions:**
- Check disk space: `df -h $WORK_DIR $NFS_OUTPUT_DIR`
- Verify NFS mount is accessible: `ls -la $NFS_OUTPUT_DIR`
- Kill hung ffmpeg processes: `pkill -9 ffmpeg`
- Check work directory for partial files: `ls -lah $WORK_DIR`
- Enable verbose logging and retry: `bin/audiobook-convert --verbose --force /path/to/book/`

### Permission denied errors

**Symptoms:** `"Permission denied"` when writing to output directory or setting file ownership

**Causes:**
1. Pipeline user doesn't have write access to `NFS_OUTPUT_DIR`
2. `FILE_OWNER` is set but pipeline user can't chown files

**Solutions:**
- Verify write permissions: `touch $NFS_OUTPUT_DIR/test.txt && rm $NFS_OUTPUT_DIR/test.txt`
- If using NFS, check export options (no_root_squash, user mapping)
- If `FILE_OWNER` is set, ensure pipeline runs as root or the target user
- For non-root setups, set `FILE_OWNER=""` in `config.env` to skip chown

## License

MIT -- see [LICENSE](LICENSE).
