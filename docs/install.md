# Installation and first conversion

## Prerequisites

Install `ffmpeg` with your operating system package manager and install `uv`.
The project requires Python 3.13; use the interpreter managed by `uv`, not a
system Python installation.

```bash
# macOS
brew install ffmpeg uv

# Verify the tools used by the application.
ffmpeg -version
uv --version
```

From a checkout, the setup helper installs Python 3.13 through `uv`, syncs the
locked project dependencies, and prints the current command help:

```bash
./scripts/run setup
```

The same helper is available directly as `./scripts/setup/setup.sh`.

## Configuration layers

`load_settings` applies configuration in this order, where earlier entries win:

1. Explicit application construction values (used by tests).
2. Environment variables with the `AUDIOBOOK_` prefix and `__` nesting.
3. Gitignored `secrets/config.json`.
4. `config/config.PROFILE.json` selected by `--profile`.
5. Committed `config/config.json`.
6. Built-in defaults.

The committed default keeps all paths under `data/`. The repository currently
also provides `sandbox` and `live` profile files. Select one only when its paths
are appropriate for the machine running the command:

```bash
uv run audiobook-convert --profile sandbox --dry-run /path/to/one-book
```

For an uncommitted local override, export a nested environment variable for the
one command. For example:

```bash
AUDIOBOOK_PATHS__LIBRARY_DIR=/media/audiobooks \
  uv run audiobook-convert --dry-run /path/to/one-book
```

Keep credentials out of committed JSON. The optional `secrets/config.json` is
the local JSON layer for credentials; shell environment values have higher
precedence.

## First conversion

Start with one representative source directory. `audiobook-convert` accepts a
directory, not an individual audio file.

```bash
uv run audiobook-convert --dry-run /path/to/one-book
```

Dry run reports discovery only. It does not convert, write a pipeline database,
or acquire the global batch admission lease. If discovery looks right, perform
the conversion:

```bash
uv run audiobook-convert /path/to/one-book
```

For a larger source, keep the first non-dry run bounded:

```bash
uv run audiobook-convert --limit 1 /path/to/incoming
```

## Conversion modes and levels

Choose an operation with `--mode`:

```bash
uv run audiobook-convert --mode convert /path/to/books
uv run audiobook-convert --mode enrich /path/to/books
uv run audiobook-convert --mode metadata /path/to/books
uv run audiobook-convert --mode organize /path/to/books
```

`convert` runs the full conversion sequence. `enrich` begins with identity and
metadata work, `metadata` applies identity and metadata work without
organization, and `organize` runs organization only.

Set `AUDIOBOOK_LEVEL` separately from the mode. `simple` omits organization and
archive stages; `normal` is the default; `ai` and `full` enable the configured
AI candidate resolver. See [ai.md](ai.md) for the required AI configuration.

## Audit and library diff

`audiobook-audit` is read-only. Pass a library directory to inspect it, or omit
the path to see the configured library and pipeline database summary:

```bash
uv run audiobook-audit /path/to/library
uv run audiobook-audit /path/to/library --check tags --check duplicates
uv run audiobook-audit /path/to/library --json-output
uv run audiobook-audit --status failed --failures
```

An audit exits `1` when critical findings exist and `0` for warnings or
informational findings alone. The command audits a finished library layout;
running all checks against a `simple`-level source directory will intentionally
report its retained source audio and non-library placement.

To list books present in a source tree but missing from a finished library,
compare them directly:

```bash
uv run audiobook-audit /path/to/source --diff /path/to/library
```

The diff exits `1` while any source book is missing from the target and `0`
when every source book matches.

## Watch folder

`audiobook-watch` polls `paths.incoming_dir`. It waits until a candidate is
stable, records durable claims in `paths.work_dir/watch.db`, retries according
to `automation.max_retries`, and moves permanently failed candidates to
`paths.failed_dir`.

Set those values through JSON or `AUDIOBOOK_PATHS__...` and
`AUDIOBOOK_AUTOMATION__...` environment variables, then start the watcher:

```bash
uv run audiobook-watch --profile sandbox
```

Use `Ctrl-C` to stop the watcher cleanly. Watch processing uses the configured
`automation.watch_mode`, which defaults to `convert`.

## Author override marker

Create an empty marker in a franchise directory when every organized book in
that subtree should share the franchise author folder:

```bash
touch /path/to/source/Dragonlance/.author-override
uv run audiobook-convert /path/to/source/Dragonlance
```

The marker directory name (`Dragonlance`) overrides the catalogue author for
library placement. The search cannot climb above the source directory passed
to the command, and embedded author tags remain unchanged.
