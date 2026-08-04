# Credits

This list reflects the direct dependencies declared in `pyproject.toml` and the
external services called by the current Python source. It intentionally avoids
stale version claims.

## Runtime dependencies

- [Click](https://click.palletsprojects.com/) for the command-line interfaces.
- [HTTPX](https://www.python-httpx.org/) for catalogue, cover, and AI HTTP calls.
- [Loguru](https://loguru.readthedocs.io/) for application logging.
- [Pydantic](https://docs.pydantic.dev/) and
  [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
  for validated configuration and models.
- [RapidFuzz](https://rapidfuzz.github.io/RapidFuzz/) for title and author
  matching.
- [Tenacity](https://tenacity.readthedocs.io/) for HTTP retry policy.
- [Mutagen](https://mutagen.readthedocs.io/) for MP4/M4B tags that FFmpeg does
  not preserve.
- [filelock](https://py-filelock.readthedocs.io/) for cross-process batch
  admission.
- [psutil](https://psutil.readthedocs.io/) for CPU-aware scheduling.
- [OpenAI Python](https://github.com/openai/openai-python), declared as a
  project dependency.

## External catalogue services

- [Audible](https://www.audible.com/), whose catalogue endpoint is queried by
  `services/audible.py`.
- [Audnexus](https://audnex.us/), whose chapter endpoint is queried by
  `services/identify.py`.

## Audio tooling

- [FFmpeg](https://ffmpeg.org/) and `ffprobe`, installed outside Python and
  invoked by `utils/ffmpeg.py` for probing, concatenation, encoding, and
  chapter metadata.
