# Contributing to audiobook-pipeline

Thanks for your interest in contributing. This guide covers everything you need to get started.

## Quick Start

```bash
# Fork and clone
gh repo fork rodaddy/audiobook-pipeline --clone
cd audiobook-pipeline

# Set up config for local testing
cp config.env.example config.env
# Edit config.env -- point WORK_DIR, OUTPUT_DIR, etc. to local test paths

# Check dependencies
./install.sh

# Test with dry-run
bin/audiobook-convert --dry-run --verbose /path/to/test-audiobook/
```

## Development Setup

### Dependencies

See the [README](README.md#installation) for the full dependency list. Run `./install.sh` to check and install everything.

### Project Structure

```
bin/              CLI entry points (audiobook-convert, cron-scanner, queue-processor)
lib/              Shared libraries (sourced by stages, never executed directly)
stages/           Pipeline stages (01-09, each self-contained with stage_*() function)
config.env.example  Configuration template (copy to config.env)
docs/development/   Architecture docs, phase plans, and research history
.github/            Issue templates, PR template, CI workflow
```

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
