# Contributing to audiobook-pipeline

Thanks for your interest in contributing. This guide covers everything you need to get started.

## Quick Start

```bash
# Fork and clone
gh repo fork rodaddy/audiobook-pipeline --clone
cd audiobook-pipeline

# Install Python 3.13 via uv, sync locked dependencies, print command help
./scripts/run setup

# Point a profile at local test paths, so a mistake cannot touch a real library
cp examples/config/simple-no-ai.json config/config.mine.json
# Edit the paths in config/config.mine.json, then:

# Discovery only -- converts nothing, writes no database
uv run audiobook-convert --profile mine --dry-run /path/to/test-audiobook/
```

Profiles other than `config.json` and `config.sandbox.json` are gitignored, so
`config.mine.json` stays local to your checkout.

## Development Setup

### Dependencies

`ffmpeg` and `uv` are the only external requirements; see the
[README](README.md#requirements). `./scripts/run setup` installs Python 3.13
through `uv` and syncs the locked dependencies.

### Project Structure

```
src/audiobook_pipeline/
  apps/       CLI entry points: convert, audit, watch
  services/   One module per stage, each callable and testable alone
  models/     Pydantic models -- every boundary in the system has one
  utils/      The shared floor: text, paths, ffmpeg, http, tagging, logging
  db/         SQLite state, connection handling, and typed row factories
  config.py   The keystone. Every setting, and the only place logging is set up
tests/        Mirrors the source tree
config/       Committed profiles. PLACEHOLDER paths only -- never a real one
examples/     Worked configuration profiles to copy
docs/         install.md, ai.md, and development history
.github/      Issue templates, PR template, CI workflow
```

Two rules the layout depends on. A service never imports another service --
shared behaviour goes in `utils/`, shared shapes in `models/`. And `utils/`
never imports from `services/`, so it cannot participate in a cycle.

### Searching the code

The repository is indexed for semantic search with
[qmd](https://github.com/rodaddy/qmd), which answers "how does X work"
questions that a text search cannot. The index is machine-local and
gitignored -- it is a rebuildable artifact of one checkout and was 15 MB, so
it is not shipped. Build your own if you want it:

```bash
qmd index .          # build the local index
qmd query "how are chapters carried across from the source"
```

This is entirely optional. `rg` covers most needs and needs no setup.

### How Stages Work

Each stage is a standalone bash script that:

1. Sets `SCRIPT_DIR` and `STAGE` variables
2. Sources its dependencies from `lib/`
3. Defines a `stage_<name>()` function
4. Checks required env vars with `: "${VAR:?error}"`
5. Does its work (with `run()` wrapper for dry-run support)
6. Updates the manifest with `manifest_set_stage` and `manifest_update`
7. Returns 0 on success, 1 on failure

Non-critical operations (cover art, chapters, verification) degrade gracefully -- they log warnings but don't fail the stage.

## Code Style

### Shell Scripts

- **Always** use `#!/usr/bin/env bash` -- never `#!/bin/bash`
- **Always** use `set -euo pipefail` at the top of executable scripts
- **Always** run `shellcheck` before submitting (CI enforces this)
- Quote all variables: `"$var"` not `$var`
- Use `[[ ]]` for conditionals, not `[ ]`
- Conditional execution over if/else when simple: `[[ -n "$var" ]] && do_thing`
- Functions use lowercase with underscores: `fetch_audible_book`, `build_plex_path`
- Library-internal functions start with underscore: `_audnexus_cache_valid`

### Logging

Use the functions from `lib/core.sh`:

```bash
log_debug "Detailed info for --verbose mode"
log_info  "Normal progress messages"
log_warn  "Something went wrong but we can continue"
log_error "Something failed -- may need intervention"
die       "Fatal error -- abort the pipeline"
```

### JSON Processing

- Use `jq` for all JSON parsing -- never inline Python
- Validate responses with `jq empty` or `jq -e` before caching
- Use `// empty` for optional fields to avoid null output

## Branching and Commits

### Branch Names

- `feat/description` -- new features
- `fix/description` -- bug fixes
- `docs/description` -- documentation changes
- `chore/description` -- maintenance, CI, dependencies

### Commit Messages

Use conventional commit format:

```
feat: add German Audible region support
fix: handle missing series position in metadata
docs: add troubleshooting section for region mismatch
chore: update shellcheck CI to v0.10
```

Keep the first line under 72 characters. Add a body for complex changes explaining the "why."

## Adding a New Metadata Field

1. **`lib/audible.sh`** -- add the field to `normalize_audible_json()` jq filter
2. **`lib/metadata.sh`** -- extract the field in `tag_m4b()` and add the tone flag
3. **`README.md`** -- add a row to the Metadata Fields table
4. **`config.env.example`** -- add config var if the field is configurable

The field should be conditional: `[[ -n "$field" ]] && tone_args+=("--meta-flag" "$field")` so it works with both Audible and Audnexus sources.

## Adding a New Stage

1. Create `stages/NN-name.sh` following the existing pattern
2. Add the stage to `STAGE_MAP` and `STAGE_ORDER` in `bin/audiobook-convert`
3. Add any new library functions in `lib/name.sh`
4. Source the library in `bin/audiobook-convert`
5. Update manifest schema if the stage produces new state

## Testing

There's no automated test suite yet (contributions welcome). For now:

```bash
# Lint all shell scripts
shellcheck -x -e SC1091,SC2034 lib/*.sh stages/*.sh bin/*.sh bin/audiobook-convert install.sh

# Dry-run a conversion
bin/audiobook-convert --dry-run --verbose /path/to/mp3-directory/

# Dry-run an enrichment
bin/audiobook-convert --dry-run --verbose /path/to/existing.m4b

# Test metadata-only mode
bin/audiobook-convert --mode metadata --dry-run --verbose /path/to/book.m4b

# Verify metadata was written
tone dump /path/to/tagged.m4b --format json
```

## Pull Request Process

1. Fork the repo and create a feature branch from `main`
2. Make your changes
3. Run `shellcheck` on all modified files
4. Test with `--dry-run --verbose` against a sample audiobook
5. Submit a PR against `main` -- the PR template will guide you
6. Address any review feedback

PRs that add new config options must also update `config.env.example` and the README.

## Questions?

Open a [Discussion](https://github.com/rodaddy/audiobook-pipeline/discussions) for questions, ideas, or anything that isn't a bug or feature request.
