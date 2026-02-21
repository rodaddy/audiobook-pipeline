# Contributing

## Dependencies

- bash 4.0+
- ffmpeg, jq, tone, curl, bc (see README for install instructions)

## Project Structure

```
bin/              CLI entry points
lib/              Shared library functions (sourced, not executed)
stages/           Pipeline stages (01-09, each self-contained)
config.env.example  Configuration template
docs/development/ Architecture docs and planning history
```

## Development

```bash
# Copy and edit config for local testing
cp config.env.example config.env

# Run with verbose logging
bin/audiobook-convert --verbose --dry-run /path/to/test-audiobook/

# Test a specific mode
bin/audiobook-convert --mode enrich --dry-run /path/to/test.m4b
```

## Pull Requests

1. Fork and create a feature branch
2. Test with `--dry-run` against sample audiobooks
3. Verify `--help` output is current
4. Submit PR with a description of what changed and why
