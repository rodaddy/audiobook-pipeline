# audiobook-pipeline

Convert, tag, and organize audiobook collections into chaptered M4B files with rich metadata.

MP3/FLAC/OGG/M4A directories in, Plex/Audiobookshelf-ready M4B files out -- with cover art, chapter markers, and proper folder structure.

## Features

- **Multi-format input** -- MP3, FLAC, OGG, M4A, WMA
- **Chaptered M4B output** -- one file per book with chapter markers from source files
- **Audnexus metadata** -- automatic ASIN discovery, cover art, author/narrator, series info
- **Plex-ready organization** -- `Author/Book (Year)/Book.m4b` folder structure
- **M4B enrichment** -- fix metadata and organize existing M4B files (skip conversion)
- **Idempotent processing** -- manifest-based state tracking with resume support
- **Automation ready** -- Readarr webhook, cron scanner, batch processing with `--no-lock`
- **Error recovery** -- categorized failures, automatic retries, failed/ directory quarantine
- **Hardware-accelerated encoding** -- AudioToolbox (macOS) when available, software AAC fallback

## Quick Start

```bash
# Install dependencies
# macOS: brew install ffmpeg jq curl bc
# Linux: apt install ffmpeg jq curl bc

# Install tone (chapter/metadata tool)
# See: https://github.com/sandreas/tone

# Clone and configure
git clone https://github.com/rodaddy/audiobook-pipeline.git
cd audiobook-pipeline
cp config.env.example config.env
# Edit config.env -- set your paths

# Convert a directory of MP3s
bin/audiobook-convert /path/to/audiobook-mp3s/
```

## Installation

### Dependencies

| Tool | Purpose | Install |
|------|---------|---------|
| `ffmpeg` | Audio concat + AAC encoding | `brew install ffmpeg` / `apt install ffmpeg` |
| `jq` | JSON manifest management | `brew install jq` / `apt install jq` |
| `tone` | M4B chapter + metadata tagging | [github.com/sandreas/tone](https://github.com/sandreas/tone) |
| `curl` | Audnexus API calls | Pre-installed on most systems |
| `bc` | Duration arithmetic | Pre-installed on most systems |

### Setup

```bash
git clone https://github.com/rodaddy/audiobook-pipeline.git
cd audiobook-pipeline
cp config.env.example config.env
```

Edit `config.env` to set your directory paths. At minimum, configure:
- `WORK_DIR` -- temporary processing space
- `MANIFEST_DIR` -- manifest storage for idempotency
- `NFS_OUTPUT_DIR` -- your Plex/Audiobookshelf library root

## Usage

### Convert a directory of audio files

```bash
# Auto-detects directory input -> convert mode
bin/audiobook-convert /mnt/downloads/MyBook/

# With options
bin/audiobook-convert --verbose --force /mnt/downloads/MyBook/
```

Pipeline stages: validate -> concat -> convert -> ASIN lookup -> metadata -> organize -> archive -> cleanup

### Enrich an existing M4B

```bash
# Auto-detects .m4b input -> enrich mode
bin/audiobook-convert /mnt/media/untagged-book.m4b
```

Skips conversion stages. Fetches metadata from Audnexus and organizes into your library.

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

### Batch processing

```bash
# Process multiple books in parallel (each skips the global lock)
for dir in /mnt/downloads/*/; do
  bin/audiobook-convert --no-lock "$dir" &
done
wait
```

### Automation

- **Readarr webhook**: `bin/readarr-hook.sh` -- triggered on import, queues for processing
- **Cron scanner**: `bin/cron-scanner.sh` -- watches incoming directory for stable books
- **Queue processor**: `bin/queue-processor.sh` -- processes queued books sequentially

## Configuration

See `config.env.example` for all options. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_BITRATE` | `128` | Cap output bitrate (kbps). Source bitrate matched up to this. |
| `CHANNELS` | `1` | Audio channels. 1=mono (speech), 2=stereo. |
| `NFS_OUTPUT_DIR` | `/mnt/media/AudioBooks` | Library root for organized output. |
| `CREATE_COMPANION_FILES` | `true` | Deploy cover.jpg, desc.txt, reader.txt alongside M4B. |
| `MAX_RETRIES` | `3` | Retry attempts before quarantine to failed/. |
| `AUDNEXUS_REGION` | `us` | Audible region for metadata lookups. |

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
│              API, or Audnexus search                     │  skipped for
│                          |                               │  M4B input
│ 06-metadata  Fetch from Audnexus: cover art, author,    │  (enrich mode)
│              narrator, series, chapters. Apply via tone  │
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

1. Folder name pattern match (e.g., `{ASIN}` or `[ASIN]` in directory name)
2. Readarr API lookup (if configured)
3. Audnexus search by title/author
4. Manual entry prompt (interactive mode)

## License

MIT -- see [LICENSE](LICENSE).
