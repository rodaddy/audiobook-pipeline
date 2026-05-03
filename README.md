<p align="center">
  <strong>audiobook-pipeline</strong><br>
  Convert audio files to chaptered M4B audiobooks with rich Audible metadata.
</p>

<p align="center">
  <a href="https://github.com/rodaddy/audiobook-pipeline/actions/workflows/ci.yml"><img src="https://github.com/rodaddy/audiobook-pipeline/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/rodaddy/audiobook-pipeline" alt="License"></a>
  <img src="https://img.shields.io/badge/python-%3E%3D3.11-blue" alt="Python 3.11+">
  <a href="https://buymeacoffee.com/rodaddy"><img src="https://img.shields.io/badge/Buy%20Me%20A%20Coffee-ffdd00?logo=buy-me-a-coffee&logoColor=black" alt="Buy Me A Coffee"></a>
</p>

---

Drop a folder of MP3s in, get a single `.m4b` audiobook out -- with chapters, cover art, author/narrator credits, and Plex-ready folder structure. The pipeline handles everything: concatenation, AAC encoding, ASIN lookup, Audible metadata enrichment, and library organization.

## Quick Start

```bash
git clone https://github.com/rodaddy/audiobook-pipeline.git
cd audiobook-pipeline
cp .env.example .env   # edit paths for your system

# Convert a directory of MP3s to a chaptered M4B
uv run audiobook-convert /path/to/audiobook-mp3s/

# Batch convert multiple books (CPU-aware parallel processing)
uv run audiobook-convert --mode convert /path/to/incoming/
```

## Features

- **Multi-format input** -- MP3, FLAC, OGG, M4A, WMA
- **Chaptered M4B output** -- one file per book with chapter markers from source files
- **Audible metadata** -- cover art (up to 2400px), author, narrator, series, subtitle, copyright, publisher, rating, genre taxonomy, ISBN
- **Official chapters** -- Audible API provides chapter markers with exact timestamps
- **Plex-ready organization** -- `Author/Book (Year)/Book.m4b` folder structure
- **M4B enrichment** -- fix metadata and organize existing M4B files without re-encoding
- **Library audit** -- scan for missing tags, duplicates, structure issues, stale files
- **Library diff** -- compare two libraries to find missing books
- **Idempotent** -- SQLite state tracking with automatic resume on failure
- **Parallel batch conversion** -- CPU-aware concurrency for large imports
- **Automation** -- Readarr webhook, cron scanner, queue processor
- **Error recovery** -- categorized failures, automatic retries, quarantine directory
- **Hardware-accelerated encoding** -- AudioToolbox (macOS) when available, software AAC fallback
- **Four intelligence tiers** -- from simple tag-and-go to AI-assisted metadata resolution

## Usage

### Convert audio files to M4B

```bash
# Auto-detects directory -> convert mode
uv run audiobook-convert /mnt/downloads/MyBook/

# Explicit mode with options
uv run audiobook-convert --mode convert --verbose --force /mnt/downloads/MyBook/

# Preview without changes
uv run audiobook-convert --dry-run /mnt/downloads/MyBook/
```

Pipeline stages: `validate -> concat -> convert -> asin -> metadata -> organize -> archive -> cleanup`

### Enrich an existing M4B

```bash
# Auto-detects .m4b -> enrich mode (skips conversion)
uv run audiobook-convert /mnt/media/untagged-book.m4b
```

### Metadata-only or organize-only

```bash
# Tag without moving
uv run audiobook-convert --mode metadata /path/to/book.m4b

# Move into library structure without touching metadata
uv run audiobook-convert --mode organize /path/to/book.m4b
```

### Reorganize an existing library

```bash
# Dry-run first -- always
uv run audiobook-convert /path/to/library --reorganize --dry-run

# Then run for real
uv run audiobook-convert /path/to/library --reorganize
```

The `--reorganize` flag moves (not copies) misplaced books, detects correct placements, cleans up empty directories, and deduplicates across source directories.

### Region-specific processing

```bash
AUDIBLE_REGION=de uv run audiobook-convert /path/to/german-book/
AUDIBLE_REGION=co.uk uv run audiobook-convert /path/to/uk-book/
```

### Audit your library

```bash
# Full audit
uv run audiobook-audit /path/to/library/

# Specific checks only
uv run audiobook-audit /path/to/library/ --check tags --check duplicates

# Auto-fix safe issues
uv run audiobook-audit /path/to/library/ --fix

# Preview fixes first
uv run audiobook-audit /path/to/library/ --dry-run

# JSON output for scripting
uv run audiobook-audit /path/to/library/ --json-output

# Compare two libraries (find missing books)
uv run audiobook-audit /path/to/source --diff /path/to/target
```

Audit checks: `tags`, `duplicates`, `structure`, `sources`, `stale`.

### CLI reference

```
audiobook-convert [OPTIONS] SOURCE_PATH

Options:
  -m, --mode {convert,enrich,metadata,organize}  Pipeline mode (auto-detected if omitted)
  --level {simple,normal,ai,full}                 Override intelligence tier
  --dry-run                                       Preview without making changes
  --force                                         Re-process even if completed
  -v, --verbose                                   Enable DEBUG logging
  -c, --config PATH                               Path to .env file
  --ai-all                                        AI validation on all books
  --reorganize                                    Move misplaced books (implies --ai-all)
  --verify                                        Data quality checks after processing
  --no-lock                                       Skip file locking (manual batch mode)
  --asin TEXT                                     Override ASIN discovery
  --author-override TEXT                          Force author folder name

audiobook-audit [OPTIONS] [LIBRARY_PATH]

Options:
  --check {tags,duplicates,structure,sources,stale}  Run specific check(s)
  --fix                                              Auto-fix safe issues
  --dry-run                                          Preview fixes
  --json-output                                      JSON instead of human-readable
  --diff PATH                                        Compare against target library
  --plex-url URL                                     Plex server URL
  -v, --verbose                                      Show all files, not just issues
  -c, --config PATH                                  Path to .env file
```

## Pipeline Levels

Four intelligence tiers, configured via `PIPELINE_LEVEL` in `.env` or `--level`:

| Level | Convert | Metadata | Organize | AI | Use case |
|-------|---------|----------|----------|----|----------|
| `simple` | Yes | Audible/Audnexus API | No | None | Just give me a tagged M4B |
| `normal` | Yes | Audible/Audnexus API | Best-effort | None | Try to file it, don't overthink |
| `ai` | Yes | API + LLM disambiguation | Full library placement | LLM resolves conflicts | Production library management |
| `full` | Yes | API + LLM | Interactive agent-guided | Agent walks user through issues | Hands-on curation |

- `simple` and `normal` never call an LLM, even if `PIPELINE_LLM_BASE_URL` is configured
- `--reorganize` and `--ai-all` force level to `ai` minimum
- `full` adds the interactive agent guide (`.claude/agents/audiobook-guide.md`)

## Architecture

```
SOURCE_PATH (directory of audio files or .m4b)
    |
    v
+-----------------------------------------------------------+
| 01-validate   Find audio files, detect bitrate, check     |
|               disk space, write sorted file list           |
|                           |                               |
| 02-concat     Generate ffmpeg concat list + FFMETADATA1   |
|               chapter file from per-file durations         |
|                           |                               |
| 03-convert    Single-pass ffmpeg: concat + AAC encode +   |  Stages 01-03
|               chapter inject + faststart                   |  skipped for
|                           |                               |  .m4b input
| 05-asin       ASIN discovery: folder name, Readarr API,   |  (enrich mode)
|               Audible/Audnexus search, AI disambiguation   |
|                           |                               |
| 06-metadata   Fetch cover art (2400px), author, narrator,  |
|               series, subtitle, copyright, publisher,      |
|               rating, genre, official chapters from Audible |
|                           |                               |
| 07-organize   Create Author/Book (Year)/ structure,        |
|               move M4B + companion files to library         |
|                           |                               |
| 08-archive    Archive original source files                |
|                           |                               |
| 09-cleanup    Remove work directory, release locks          |
+-----------------------------------------------------------+
    |
    v
NFS_OUTPUT_DIR/Author/Book (Year)/Book.m4b
```

### ASIN Discovery

The pipeline tries multiple sources (in priority order):

1. **Folder name** -- `{ASIN}` or `[ASIN]` in directory name
2. **Readarr API** -- queries Readarr for the book's ASIN (if configured)
3. **Audible/Audnexus search** -- searches by title/author from folder name
4. **Manual entry** -- interactive prompt (when not in automation mode)

The ASIN must be from the **Audible URL** (`audible.com/pd/B084QHXYFP`), not the Amazon product ASIN.

## Installation

### Dependencies

| Tool | Purpose | Install |
|------|---------|---------|
| `ffmpeg` | Audio concat + AAC encoding + metadata | `brew install ffmpeg` / `apt install ffmpeg` |
| `jq` | JSON processing | `brew install jq` / `apt install jq` |
| `curl` | API requests | Usually pre-installed |
| `tone` | M4B chapter tagging | [github.com/sandreas/tone](https://github.com/sandreas/tone/releases) |
| Python 3.11+ | Python runtime | `brew install python` / `apt install python3` |
| `uv` | Python package manager | [docs.astral.sh/uv](https://docs.astral.sh/uv/) |

### Setup

```bash
git clone https://github.com/rodaddy/audiobook-pipeline.git
cd audiobook-pipeline
cp .env.example .env

# Edit .env -- set at minimum:
#   WORK_DIR     -- temporary processing space
#   NFS_OUTPUT_DIR -- your Plex/Audiobookshelf library root

# Run the interactive installer to check/install system dependencies
./install.sh

# Test it works
uv run audiobook-convert --help
```

## Configuration

Copy `.env.example` to `.env` and customize. Key variables:

### Directories

| Variable | Default | Description |
|----------|---------|-------------|
| `WORK_DIR` | `/var/lib/audiobook-pipeline/work` | Temporary processing workspace |
| `OUTPUT_DIR` | `/var/lib/audiobook-pipeline/output` | Local output before NFS move |
| `LOG_DIR` | `/var/log/audiobook-pipeline` | Pipeline logs |
| `NFS_OUTPUT_DIR` | `/mnt/media/AudioBooks` | Library root (Plex/Audiobookshelf) |
| `ARCHIVE_DIR` | `/var/lib/audiobook-pipeline/archive` | Archive original sources |

### Encoding

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_BITRATE` | `128` | Cap output bitrate (kbps) |
| `CHANNELS` | `1` | 1=mono (recommended for speech), 2=stereo |

### Metadata

| Variable | Default | Description |
|----------|---------|-------------|
| `METADATA_SOURCE` | `audible` | Primary source: `audible` or `audnexus` |
| `AUDIBLE_REGION` | `com` | Audible marketplace region |
| `CHAPTER_DURATION_TOLERANCE` | `5` | Percent tolerance for chapter matching |

### Metadata Sources

**`audible` (default)** -- Audible catalog API. Provides subtitle, copyright, publisher, ISBN, rating, genre path, 2400px cover art, and official chapter markers. Falls back to Audnexus on failure.

**`audnexus`** -- Community-maintained Audible mirror. Good for Plex users with the Audnexus agent, rate limit avoidance on large batches, and older books removed from the Audible catalog. Falls back to Audible API on failure.

Override per-run: `METADATA_SOURCE=audnexus uv run audiobook-convert /path/to/book/`

### Region Configuration

| Region | `AUDIBLE_REGION` | Region | `AUDIBLE_REGION` |
|--------|------------------|--------|------------------|
| US | `com` | Germany | `de` |
| UK | `co.uk` | France | `fr` |
| Australia | `com.au` | Japan | `co.jp` |
| Canada | `ca` | India | `in` |
| Italy | `it` | Spain | `es` |

### Metadata Tags

The pipeline writes these tags to M4B files via ffmpeg:

| Tag | ffmpeg key | Source |
|-----|------------|--------|
| Title | `title` | Audible/Audnexus |
| Artist | `artist` | Author + Narrator |
| Album Artist | `album_artist` | Author |
| Album | `album` | Book title |
| Composer | `composer` | Narrator |
| Genre | `genre` | Audible categories |
| Date | `date` | Release year |
| Description | `description` | Publisher summary |
| Sort Album | `sort_album` | Series sort key |
| Copyright | `copyright` | Audible |
| Publisher | `publisher` | Audible |
| Show | `show` | Series name |
| Grouping | `grouping` | Series + Book # |
| ASIN | `ASIN` | Audible ASIN |
| Cover Art | embedded | Up to 2400x2400px |

### Automation

| Variable | Default | Description |
|----------|---------|-------------|
| `INCOMING_DIR` | `/mnt/media/AudioBooks/_incoming` | Cron scanner watch directory |
| `STABILITY_THRESHOLD` | `120` | Seconds before processing new books |
| `MAX_RETRIES` | `3` | Retry attempts before quarantine |
| `FAILURE_WEBHOOK_URL` | _(empty)_ | Slack/Discord webhook for failures |

### AI-Assisted Search (optional)

```bash
# Any OpenAI-compatible endpoint: LiteLLM, OpenAI, Ollama, etc.
PIPELINE_LLM_BASE_URL="http://localhost:4000/v1"
PIPELINE_LLM_API_KEY="sk-..."
PIPELINE_LLM_MODEL="haiku"
```

## Automation

### Readarr Webhook

```bash
# Readarr -> Settings -> Connect -> Custom Script
/opt/audiobook-pipeline/bin/readarr-hook.sh
```

### Cron Scanner

Watches `INCOMING_DIR` for new audiobook directories:

```bash
*/5 * * * * /opt/audiobook-pipeline/bin/cron-scanner.sh
```

### Queue Processor (systemd)

```ini
# /etc/systemd/system/audiobook-queue.service
[Unit]
Description=Audiobook Pipeline Queue Processor
After=network.target

[Service]
Type=simple
ExecStart=/opt/audiobook-pipeline/bin/queue-processor.sh
Restart=always
User=audiobook

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

<details>
<summary><strong>No metadata found</strong></summary>

- Verify ASIN is from the Audible URL (not Amazon product URL)
- Check the book exists in your configured `AUDIBLE_REGION`
- Try `METADATA_SOURCE=audnexus` or a different region
</details>

<details>
<summary><strong>Chapter duration mismatch</strong></summary>

- Increase `CHAPTER_DURATION_TOLERANCE` (default 5%) to 10-15 for books with long intros
- Use `--verbose` to see detailed timestamp comparison
- Check source file integrity
</details>

<details>
<summary><strong>Pipeline stalls</strong></summary>

- Check disk space: `df -h $WORK_DIR $NFS_OUTPUT_DIR`
- Verify NFS mount: `ls -la $NFS_OUTPUT_DIR`
- Kill hung ffmpeg: `pkill -9 ffmpeg`
- Retry with verbose: `uv run audiobook-convert --verbose --force /path/to/book/`
</details>

<details>
<summary><strong>Permission denied</strong></summary>

- Check write access: `touch $NFS_OUTPUT_DIR/test && rm $NFS_OUTPUT_DIR/test`
- NFS: check export options (`no_root_squash`, user mapping)
- Set `FILE_OWNER=""` in `.env` to skip chown if not running as root
</details>

<details>
<summary><strong>Cover art download failed</strong></summary>

- Pipeline automatically falls back to Audnexus for cover art
- Rate limits are temporary -- wait a few minutes and retry
- Verify ASIN is correct and the book has cover art on audible.com
</details>

## Development

```bash
# Run tests
uv run pytest tests/

# Type check
uv run mypy src/

# Format
ruff format src/ tests/

# Lint shell scripts
shellcheck -x lib/*.sh stages/*.sh bin/audiobook-convert
```

## License

MIT -- see [LICENSE](LICENSE).
