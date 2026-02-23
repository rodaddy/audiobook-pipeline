# Technology Stack

**Analysis Date:** 2026-02-21

## Languages

**Primary:**
- Python >=3.11 - Entire codebase (uses StrEnum, `X | Y` union syntax)

**Secondary:**
- Bash - Build/automation scripts in `scripts/`

## Runtime

**Environment:**
- Python 3.13 (`.venv/lib/python3.13/` observed)
- Requires >=3.11 per `pyproject.toml`

**Package Manager:**
- uv (project preference per global rules)
- Lockfile: `requirements.txt` auto-generated from `pyproject.toml` via `scripts/gen-requirements.py`

## Frameworks

**Core:**
- Click >=8.1 - CLI framework (`src/audiobook_pipeline/cli.py`)
- Pydantic Settings >=2.0 (with TOML support) - Configuration management (`src/audiobook_pipeline/config.py`)

**Testing:**
- pytest >=8.0 - Test runner
- pytest-httpx >=0.30 - HTTP request mocking for API tests

**Build/Dev:**
- Hatchling - Build backend (`pyproject.toml` `[build-system]`)
- pre-commit >=3.0 - Git hooks for README/requirements generation

## Key Dependencies

**Critical:**
- `openai` >=1.0 - OpenAI-compatible API client for AI metadata resolution (`src/audiobook_pipeline/ai.py`). Used with LiteLLM proxy, not directly with OpenAI.
- `httpx` >=0.27 - HTTP client for Audible catalog API (`src/audiobook_pipeline/api/audible.py`)
- `rapidfuzz` >=3.0 - Fuzzy string matching for metadata scoring (`src/audiobook_pipeline/api/search.py`)
- `loguru` >=0.7 - Structured logging throughout all modules (`src/audiobook_pipeline/config.py`)
- `pydantic-settings[toml]` >=2.0 - Typed config with .env file + env var layering (`src/audiobook_pipeline/config.py`)

**Infrastructure:**
- `click` >=8.1 - CLI entry point with auto mode detection (`src/audiobook_pipeline/cli.py`)

## External CLI Tools (not Python packages)

**Required at runtime:**
- `ffprobe` (from FFmpeg) - Audio file inspection: duration, bitrate, codec, channels, tags, chapters (`src/audiobook_pipeline/ffprobe.py`)
- `ffmpeg` - Audio conversion (planned stages, not yet implemented)

## Configuration

**Environment:**
- `.env` file loaded via custom parser in `src/audiobook_pipeline/cli.py` (not python-dotenv)
- Pydantic Settings provides layered resolution: `.env` file < environment variables < constructor kwargs
- AI config uses `PIPELINE_LLM_*` env vars (not `OPENAI_*`) to avoid collisions with global OpenAI keys
- See `.env.example` for all available configuration variables

**Key env var groups:**
- `WORK_DIR`, `MANIFEST_DIR`, `OUTPUT_DIR`, `LOG_DIR` - Directory paths
- `MAX_BITRATE`, `CHANNELS`, `CODEC` - Encoding settings
- `METADATA_SOURCE`, `AUDIBLE_REGION` - Metadata resolution
- `NFS_OUTPUT_DIR` - Plex/Audiobookshelf library root
- `PIPELINE_LLM_BASE_URL`, `PIPELINE_LLM_API_KEY`, `PIPELINE_LLM_MODEL` - AI endpoint
- `INCOMING_DIR`, `QUEUE_DIR`, `PROCESSING_DIR` - Automation directories

**Build:**
- `pyproject.toml` - Project metadata, dependencies, build config, entry point
- `.pre-commit-config.yaml` - Two local hooks: `gen-readme` and `gen-requirements`

## Platform Requirements

**Development:**
- Python >=3.11
- uv package manager
- FFmpeg/ffprobe installed
- pre-commit for git hooks

**Production:**
- Linux (uses `fcntl` for file locking, with `msvcrt` fallback for Windows)
- FFmpeg/ffprobe on PATH
- NFS mount at `NFS_OUTPUT_DIR` for Plex library output
- Optional: LiteLLM proxy at `PIPELINE_LLM_BASE_URL` for AI-assisted metadata

## Entry Points

**CLI:**
- `audiobook-convert` console script defined in `pyproject.toml` -> `audiobook_pipeline.cli:main`

---

*Stack analysis: 2026-02-21*
