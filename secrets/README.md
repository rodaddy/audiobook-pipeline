Real credentials go here as `config.json`, which is gitignored.

Copy `config.example.json` to `config.json` and fill in the values. Only the
AI endpoint and its key belong here -- everything else is non-secret config
and belongs in `config/`, which is committed.

The pipeline runs fine with no file here at all; the AI stages are inert
unless `level` is `ai` or `full`.
