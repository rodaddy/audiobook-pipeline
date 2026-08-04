#!/usr/bin/env bash
# setup.sh -- install the locked Python 3.13 environment for the current CLI.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

say() {
  printf '%s\n' "$*"
}

if [[ -z "$(command -v uv 2>&1)" ]]; then
  say "uv is required. Install it from https://docs.astral.sh/uv/ and rerun setup."
  exit 1
fi

if [[ -z "$(command -v ffmpeg 2>&1)" ]]; then
  say "ffmpeg is required. macOS: brew install ffmpeg"
  exit 1
fi

say "Installing a uv-managed Python 3.13..."
uv python install 3.13

say "Syncing locked project dependencies with Python 3.13..."
uv sync --python 3.13

say "Current command surface:"
uv run --python 3.13 audiobook-convert --help
uv run --python 3.13 audiobook-audit --help
uv run --python 3.13 audiobook-watch --help

say ""
say "First run:"
say "  uv run audiobook-convert --dry-run /path/to/one-book"
say ""
say "Configuration comes from config/config.json, an optional --profile layer,"
say "gitignored secrets/config.json, and AUDIOBOOK_ environment overrides."
