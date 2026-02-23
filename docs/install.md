# Audiobook Pipeline Installation and Setup Guide

Agent-readable setup guide for the audiobook conversion pipeline.

## Prerequisites

Install required dependencies:

```bash
# macOS (via Homebrew)
brew install ffmpeg python@3.11

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify installations
ffmpeg -version
python3 --version  # Must be 3.11+
uv --version
```

**Required tools:**
- `ffmpeg` - Audio processing and M4B encoding
- `Python 3.11+` - Runtime environment
- `uv` - Python package manager (replaces pip/poetry)

## Configure Environment Variables

Create a `.env` file in the project root:

```bash
# Copy template if available, or create from scratch
cp .env.example .env  # if it exists
```

### Core Settings

```bash
# Intelligence level: simple, normal, ai, full
PIPELINE_LEVEL=full

# Source library (where unconverted audiobooks live)
SOURCE_LIBRARY=/Volumes/media_files/AudioBooks

# Destination library (where converted M4B files go)
DEST_LIBRARY=/Volumes/ThunderBolt/AudioBooks

# Temp directory for processing (MUST have space for large audio files)
# NEVER use /tmp -- audio files are huge
TEMP_DIR=/Volumes/ThunderBolt/AudioBookStuff/test-scratch
```

### NFS Mount Detection

The pipeline auto-detects NFS mounts to avoid permission issues:

```bash
# Optional: Override auto-detection
DETECT_NFS=true  # default: true

# If source/dest are on NFS mounts, pipeline adjusts file operations
# to work around permission quirks (chmod, chown, etc.)
```

### Library Structure

The pipeline organizes audiobooks as:

```
Author/
  Book Title (Year)/
    Book Title.m4b
```

For series:

```
Author/
  Series Name/
    Book 1 - Title (Year)/
      Book 1 - Title.m4b
    Book 2 - Title (Year)/
      Book 2 - Title.m4b
```

## LLM Setup (Optional)

Required for `ai` and `full` intelligence levels. Skip if using `simple` or `normal`.

### Option 1: LiteLLM Proxy (Recommended)

Configure connection to a LiteLLM proxy server:

```bash
# LiteLLM proxy endpoint
PIPELINE_LLM_BASE_URL=http://10.71.20.53:4000

# API key for proxy authentication
PIPELINE_LLM_API_KEY=your-proxy-key

# Model to use (check proxy config for available models)
PIPELINE_LLM_MODEL=haiku  # or claude-haiku-4-5
```

**Why PIPELINE_LLM_* instead of OPENAI_*?**

The pipeline uses custom env var names to avoid collisions with global OpenAI keys. This lets you run the pipeline with a LiteLLM proxy while keeping your personal OpenAI key separate.

### Option 2: Direct API Key

Use an OpenAI-compatible API directly:

```bash
PIPELINE_LLM_BASE_URL=https://api.anthropic.com/v1
PIPELINE_LLM_API_KEY=sk-ant-your-key-here
PIPELINE_LLM_MODEL=claude-3-5-haiku-20241022
```

### LLM Features by Level

| Level | Uses LLM For |
|-------|-------------|
| `simple` | Nothing (no LLM required) |
| `normal` | Nothing (no LLM required) |
| `ai` | Genre classification, series detection |
| `full` | AI + metadata enrichment + author disambiguation |

## First Run

Test the pipeline on a single audiobook:

```bash
# Dry-run mode (no changes, just shows what would happen)
uv run audiobook-convert --dry-run /Volumes/media_files/AudioBooks/SomeBook

# Check the output for:
# - Detected metadata (title, author, series)
# - Planned output path
# - Any warnings or errors

# If dry-run looks good, run for real:
uv run audiobook-convert /Volumes/media_files/AudioBooks/SomeBook
```

### Test Book Selection

Pick a book that:
- Has clear metadata (title, author in folder/file name)
- Is not too large (< 500MB for first test)
- Has a known ASIN (if using `full` level)

## Common Workflows

### Single Book Conversion

Convert one audiobook from source to destination:

```bash
uv run audiobook-convert /path/to/book/

# Override intelligence level for this run
uv run audiobook-convert --level ai /path/to/book/

# Force ASIN lookup (if auto-detection fails)
uv run audiobook-convert --asin B08F5ZQXYZ /path/to/book/
```

### Batch Conversion

Process multiple books at once:

```bash
# Convert all books in a directory
uv run audiobook-convert /Volumes/media_files/AudioBooks/

# Dry-run batch to preview changes
uv run audiobook-convert --dry-run /Volumes/media_files/AudioBooks/
```

### Reorganize Existing Library

Move and rename books to match the standard structure:

```bash
# Reorganize mode: no conversion, just move/rename
uv run audiobook-convert --reorganize /Volumes/ThunderBolt/AudioBooks/

# This implies --mode organize --ai-all
# and forces level to ai minimum (overrides PIPELINE_LEVEL)
```

Use this when:
- Library structure changed
- Books were manually added without pipeline
- Fixing metadata for existing M4B files

### Multi-Author Franchises

Handle shared universes like Dragonlance or Forgotten Realms:

```bash
# Create author override marker
touch "/Volumes/ThunderBolt/AudioBooks/Dragonlance/.author-override"

# All books under this folder will use "Dragonlance" as author
# regardless of individual book authors (Weis & Hickman, etc.)
```

Without `.author-override`:
```
Margaret Weis/
  Dragonlance - Chronicles 1 - Dragons of Autumn Twilight.m4b
Tracy Hickman/
  Dragonlance - Chronicles 1 - Dragons of Autumn Twilight.m4b  # duplicate!
```

With `.author-override`:
```
Dragonlance/
  Chronicles 1 - Dragons of Autumn Twilight.m4b
  Chronicles 2 - Dragons of Winter Night.m4b
  Legends 1 - Time of the Twins.m4b
```

## Troubleshooting

### ASIN Misidentification

**Problem:** Pipeline pulls wrong metadata from Audible.

**Solution:** Manually specify ASIN:

```bash
# Find correct ASIN from Audible URL
# https://www.audible.com/pd/B08F5ZQXYZ
uv run audiobook-convert --asin B08F5ZQXYZ /path/to/book/
```

### Cover Art Codec Issues

**Problem:** Plex doesn't display cover art.

**Solution:** Pipeline auto-strips mjpeg covers and re-encodes as PNG. If issues persist:

```bash
# Check cover codec
ffprobe book.m4b 2>&1 | grep -i "video.*mjpeg"

# Manual fix (if pipeline didn't catch it)
ffmpeg -i book.m4b -c copy -disposition:v:0 attached_pic \
  -map 0 -map -0:v -vf "select=eq(n\,0)" -vsync vfr cover.png
ffmpeg -i book.m4b -i cover.png -c copy -map 0 -map 1 \
  -disposition:v:0 attached_pic fixed.m4b
```

### Chaptered M4B Detection

**Problem:** Source directory has multiple `.m4b` files that are really chapters.

**Detection:** Pipeline sees multiple `.m4b` files and assumes they need concatenation.

**Solution:** Pipeline auto-detects and runs concat+encode workflow. If it fails:

```bash
# Check for multiple m4b files
ls /path/to/book/*.m4b

# Manual concat (if needed)
ffmpeg -f concat -safe 0 -i <(printf "file '%s'\n" *.m4b) \
  -c copy output.m4b
```

### NFS Permission Errors

**Problem:** `chmod` or `chown` fails on NFS mounts.

**Symptoms:**
```
PermissionError: [Errno 1] Operation not permitted: 'book.m4b'
```

**Solution:** Pipeline should auto-detect NFS mounts. If it doesn't:

```bash
# Check mount type
mount | grep /Volumes/media_files
# Look for "nfs" in output

# Force NFS detection in .env
DETECT_NFS=true

# Or move library to local storage
DEST_LIBRARY=/Volumes/ThunderBolt/AudioBooks  # local SSD
```

### Metadata Parsing Failures

**Problem:** Pipeline can't extract title/author from filename.

**Patterns supported:**
- `Author - Title`
- `Author - Series N - Title`
- `Title (Author)`
- `Author/Series/Title/`
- Bracketed positions: `[01]`, `[1]`, `[001]`

**Solution:** Rename source files to match a supported pattern before running pipeline.

### Temp Directory Full

**Problem:** Large audiobook fills temp directory during processing.

**Solution:** Always use external volume for temp, never `/tmp`:

```bash
# In .env
TEMP_DIR=/Volumes/ThunderBolt/AudioBookStuff/test-scratch

# Verify free space before large batch
df -h /Volumes/ThunderBolt
```

## Intelligence Level Reference

| Level | Metadata Source | Series Detection | Genre | AI Enrichment |
|-------|----------------|------------------|-------|---------------|
| `simple` | Filename only | Regex patterns | "Audiobook" | None |
| `normal` | Filename + FFprobe | Regex patterns | "Audiobook" | None |
| `ai` | Filename + FFprobe | AI analysis | AI classification | None |
| `full` | Audible API (ASIN) | Audible metadata | Audible categories | Full AI analysis |

**Override per-run:**

```bash
uv run audiobook-convert --level full /path/to/book/
# Overrides PIPELINE_LEVEL from .env for this run only
```

**When to use each level:**

- `simple` - Fast batch processing, known-good filenames, no LLM access
- `normal` - Default for most books, uses FFprobe metadata, no LLM needed
- `ai` - Better series detection and genre classification, requires LLM
- `full` - Maximum metadata quality, requires Audible access + LLM

## Next Steps

After setup:

1. Run dry-run on a test book
2. Verify output path and metadata
3. Run real conversion
4. Check M4B in Plex
5. Batch convert remaining library

For ongoing use:

- Add new books to SOURCE_LIBRARY
- Run pipeline on new arrivals
- Use `--reorganize` after manual library changes
- Check logs for errors: `~/.audiobook-pipeline/logs/`
