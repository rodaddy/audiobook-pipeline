# Example configuration profiles

Each JSON file here is a complete, working `--profile` layer. Copy one into
`config/`, edit the paths, and select it by name:

```bash
cp examples/config/plex.json config/config.plex.json
uv run audiobook-convert --profile plex --dry-run /path/to/one-book
```

The profile name is the middle part of the filename: `config/config.plex.json`
is selected by `--profile plex`.

| File | For |
| --- | --- |
| `simple-no-ai.json` | Convert and tag from the Audible catalogue. No LLM. The best starting point. |
| `plex.json` | A Plex library using the Audnexus agent. |
| `audiobookshelf.json` | An Audiobookshelf library. |
| `organize-only.json` | Files existing M4Bs into the library layout. Pair it with `--mode organize`, which is a command-line flag, not a config key. |
| `ai-assisted.json` | Adds LLM disambiguation for books the catalogue matches poorly. |
| `server-daemon.json` | A long-running watch-folder service. |

## How configuration is layered

Later entries here are overridden by earlier ones:

1. Environment variables — `AUDIOBOOK_` prefix, `__` for nesting
2. `secrets/config.json` — gitignored, for credentials
3. `config/config.PROFILE.json` — the file you selected with `--profile`
4. `config/config.json` — committed defaults
5. Built-in defaults

Any key you leave out of a profile falls through to the committed default, so a
profile only needs the keys it actually changes.

To override one value for a single command, use an environment variable rather
than editing a file:

```bash
AUDIOBOOK_PATHS__LIBRARY_DIR=/media/audiobooks \
  uv run audiobook-convert --dry-run /path/to/one-book
```

Note that `.env` files are **not** read. This project takes configuration from
JSON layers and environment variables only.

## Credentials

Never put an API key in a file under `config/` — that directory is committed.
Put it in `secrets/config.json`, which is gitignored:

```json
{
  "ai": {
    "api_key": "sk-..."
  }
}
```

Or pass it as `AUDIOBOOK_AI__API_KEY` in the environment.
