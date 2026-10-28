# AI candidate resolution

AI resolution is opt-in. The default `normal` level does not construct an AI
resolver or make AI requests.

## Enable it for one run

The `ai` and `full` levels require an OpenAI-compatible base URL. Supply values
through the `AUDIOBOOK_` environment namespace rather than adding credentials
to committed JSON:

```bash
AUDIOBOOK_LEVEL=ai \
AUDIOBOOK_AI__BASE_URL=https://provider.example/v1 \
AUDIOBOOK_AI__MODEL=your-model \
AUDIOBOOK_AI__API_KEY="$AI_API_KEY" \
  uv run audiobook-convert --dry-run /path/to/one-book
```

For a durable local deployment, place the equivalent nested settings in the
gitignored `secrets/config.json`. Environment variables still take precedence.

`full` uses the same configured resolver as `ai`; it is a level value, not a
separate command or a different provider contract.

## What the resolver does

The resolver receives a bounded set of catalogue candidates and can select one
of those candidates or abstain. It is used only at `ai` or `full` levels when an
endpoint is configured and the source is ambiguous (or `ai.all_books` is
enabled). Provider errors, timeouts, and malformed replies degrade to an
abstention rather than becoming canonical metadata.

The regular metadata path queries the Audible catalogue. Chapter data may be
fetched from Audnexus and is accepted only when its duration matches the local
audio within the configured checks.

## Disable it

Use the default level or explicitly select `normal` for a run:

```bash
AUDIOBOOK_LEVEL=normal uv run audiobook-convert --dry-run /path/to/one-book
```

No AI endpoint or API key is needed at `simple` or `normal` levels. If an AI
level is selected without `AUDIOBOOK_AI__BASE_URL`, settings validation rejects
startup before conversion begins.
