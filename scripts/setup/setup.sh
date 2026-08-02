#!/usr/bin/env bash
# setup.sh -- pick a profile, check your tools, write a working .env.
#
# Interactive and safe: it never overwrites an existing .env without asking,
# and it tells you what is missing rather than failing halfway through a
# conversion. Run it from the repo root:
#
#     ./scripts/run setup
#
# or directly:
#
#     ./scripts/setup/setup.sh
#
# Everything it does can be done by hand -- copy a file from examples/config/
# to .env and edit two lines. This exists so you do not have to read all 44
# settings to convert your first book.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

say()  { printf '%s\n' "$*"; }
ok()   { printf '  \033[32mOK\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mMISS\033[0m  %s\n' "$*"; }

say ""
say "audiobook-pipeline setup"
say "========================"

# ── 1. Required tools ────────────────────────────────────────────────────────
say ""
say "Checking tools:"
missing_required=0

if command -v ffmpeg >/dev/null 2>&1; then
  ok "ffmpeg    $(ffmpeg -version 2>/dev/null | head -1 | cut -d' ' -f3)"
else
  bad "ffmpeg    REQUIRED -- nothing works without it"
  missing_required=1
fi

if command -v ffprobe >/dev/null 2>&1; then
  ok "ffprobe   (ships with ffmpeg)"
else
  bad "ffprobe   REQUIRED -- ships with ffmpeg"
  missing_required=1
fi

if command -v uv >/dev/null 2>&1; then
  ok "uv        $(uv --version 2>/dev/null | cut -d' ' -f2)"
else
  bad "uv        REQUIRED -- https://docs.astral.sh/uv/"
  missing_required=1
fi

if command -v mp4tags >/dev/null 2>&1; then
  ok "mp4tags   (ASIN + series atoms will be written)"
else
  warn "mp4tags   optional, from mp4v2. Without it the ASIN and series-sort"
  warn "          atoms are skipped: audio and standard tags are still fine,"
  warn "          but Audiobookshelf/Prologue cannot auto-match by ASIN."
  warn "          macOS: brew install mp4v2    Debian: apt install mp4v2-utils"
fi

if [[ $missing_required -ne 0 ]]; then
  say ""
  say "Install the REQUIRED tools above, then run this again."
  say "  macOS:  brew install ffmpeg uv"
  say "  Debian: apt install ffmpeg && curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# ── 2. Pick a profile ────────────────────────────────────────────────────────
say ""
say "Which describes you?"
say ""
say "  1) simple-no-ai      Turn a folder of MP3s into one chaptered M4B."
say "                       No AI, no API keys. Output stays put."
say ""
say "  2) organize-only     My M4Bs are fine, my FOLDERS are a mess."
say "                       Never re-encodes; files are copied and filed."
say ""
say "  3) plex              Build a library Plex reads correctly."
say "                       (Music library + Audnexus agent.)"
say ""
say "  4) audiobookshelf    Build a library for Audiobookshelf/Prologue."
say "                       Writes the ASIN atom for auto-matching."
say ""
say "  5) ai-assisted       Hundreds of badly-named files, full automation."
say "                       Needs an LLM endpoint (Ollama works, and is free)."
say ""
say "  6) server-daemon     Unattended watch-folder on a NAS or server."
say "                       The only profile using system paths."
say ""
printf 'Choice [1-6]: '
read -r choice

case "$choice" in
  1) profile="simple-no-ai" ;;
  2) profile="organize-only" ;;
  3) profile="plex" ;;
  4) profile="audiobookshelf" ;;
  5) profile="ai-assisted" ;;
  6) profile="server-daemon" ;;
  *) say "Not a choice. Run again and pick 1-6."; exit 1 ;;
esac

src="examples/config/${profile}.env"
[[ -f "$src" ]] || { say "Missing $src -- is the repo complete?"; exit 1; }

# ── 3. Write .env ────────────────────────────────────────────────────────────
if [[ -f .env ]]; then
  say ""
  warn "A .env already exists."
  printf 'Overwrite it? Your current settings will be lost. [y/N]: '
  read -r reply
  if [[ ! "$reply" =~ ^[Yy]$ ]]; then
    say "Left your .env alone. The profile you picked is at:"
    say "  $src"
    exit 0
  fi
  cp .env ".env.backup.$(date +%Y%m%d%H%M%S)"
  ok "Backed up your old .env"
fi

cp "$src" .env
ok "Wrote .env from ${profile}"

# ── 4. Library path ──────────────────────────────────────────────────────────
if grep -q '^NFS_OUTPUT_DIR="/path/to' .env 2>/dev/null; then
  say ""
  say "Where should finished books go? They are filed as Author/Series/Title."
  say "Press Enter to keep everything inside ./data/library for now."
  printf 'Library path: '
  read -r libpath
  if [[ -n "$libpath" ]]; then
    libpath="${libpath/#\~/$HOME}"
    if [[ ! -d "$libpath" ]]; then
      warn "$libpath does not exist yet -- it will be created on first run."
    fi
    python3 - "$libpath" <<'PY'
import pathlib, sys, re
p = pathlib.Path(".env")
p.write_text(re.sub(r'^NFS_OUTPUT_DIR=.*$',
                    f'NFS_OUTPUT_DIR="{sys.argv[1]}"',
                    p.read_text(), flags=re.M))
PY
    ok "Library set to $libpath"
  else
    python3 - <<'PY'
import pathlib, re
p = pathlib.Path(".env")
p.write_text(re.sub(r'^NFS_OUTPUT_DIR=.*$',
                    'NFS_OUTPUT_DIR="./data/library"',
                    p.read_text(), flags=re.M))
PY
    ok "Library set to ./data/library (self-contained)"
  fi
fi

# ── 5. Install and verify ────────────────────────────────────────────────────
say ""
say "Installing dependencies..."
uv sync --quiet
ok "Dependencies installed"

say ""
say "Done. Next step -- ALWAYS dry-run first:"
say ""
say "  uv run audiobook-convert --dry-run /path/to/your/book/"
say ""
say "It prints what it WOULD do and changes nothing. When it looks right,"
say "drop --dry-run. To check an existing library:"
say ""
say "  uv run audiobook-audit /path/to/library/"
say ""
