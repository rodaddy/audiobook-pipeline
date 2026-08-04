# audiobook-pipeline

Convert directories of audio into chaptered M4B audiobooks, enrich their
metadata from the Audible catalogue, and place completed books in a library.

## First run

Requirements are `ffmpeg`, `uv`, and Python 3.13 managed by `uv`. From the
repository root:

```bash
./scripts/run setup
uv run audiobook-convert --dry-run /path/to/one-book
```

The dry run discovers books and prints the plan without converting anything or
acquiring the batch lease. When it looks correct, remove `--dry-run`:

```bash
uv run audiobook-convert /path/to/one-book
```

See [docs/install.md](docs/install.md) for configuration, profile, audit, and
watch details. See [docs/ai.md](docs/ai.md) before enabling AI resolution.

## Commands

```bash
# Convert, enrich, metadata-only, or organize-only work.
uv run audiobook-convert /path/to/books
uv run audiobook-convert --mode enrich /path/to/books
uv run audiobook-convert --mode metadata /path/to/books
uv run audiobook-convert --mode organize /path/to/books

# Keep the first real batch bounded.
uv run audiobook-convert --limit 1 /path/to/books

# Read-only library audit and source-to-library comparison.
uv run audiobook-audit /path/to/library
uv run audiobook-audit /path/to/source --diff /path/to/library

# Continuously process stable candidates in the configured incoming directory.
uv run audiobook-watch
```

Each command accepts `--profile NAME`, which loads `config/config.NAME.json`
over the committed shared configuration. `sandbox` and `live` are the profiles
currently committed to this repository.

Library audits exit `1` when they find critical problems. Library diffs exit
`1` while source books are missing from the target. Warnings and informational
findings remain report-only.

## Configuration

The application reads committed JSON configuration in `config/config.json`, an
optional selected profile, and an optional gitignored `secrets/config.json`.
Environment variables override all JSON layers. Their names start with
`AUDIOBOOK_`, with `__` separating nested fields:

```bash
AUDIOBOOK_PATHS__LIBRARY_DIR=/media/audiobooks \
  uv run audiobook-convert --dry-run /path/to/one-book
```

Defaults keep pipeline data under `data/`; choose real paths through a profile
or environment variables before a non-dry run. Do not put credentials in a
committed configuration file.

## Modes and levels

`--mode` controls the pipeline operation: `convert`, `enrich`, `metadata`, or
`organize`. `AUDIOBOOK_LEVEL` controls policy independently:

| Level | Behavior |
| --- | --- |
| `simple` | Omits organization and archive stages. |
| `normal` | Default level; runs the normal stage sequence without AI resolution. |
| `ai` | Enables OpenAI-compatible candidate resolution when configured. |
| `full` | Uses the same configured AI resolver as `ai`. |

AI levels require `AUDIOBOOK_AI__BASE_URL`; [docs/ai.md](docs/ai.md) shows the
complete opt-in configuration.

## Author override marker

Place an empty `.author-override` file in a franchise directory to force every
organized book below it into that directory's author folder. For example,
`Dragonlance/.author-override` files the subtree under author `Dragonlance`,
even when Audible credits different writers. The search is bounded by the
source passed to `audiobook-convert`, and it changes placement only; embedded
author tags retain the credited author.

## Credits

[CREDITS.md](CREDITS.md) records the direct dependencies and external catalogue
services evidenced by the current source.
