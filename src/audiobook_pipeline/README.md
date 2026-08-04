# Audiobook Pipeline

Convert loose audio files into chaptered, tagged M4B audiobooks.

Purpose:
    Takes a directory of audio in whatever shape it arrived -- a folder of
    MP3s, a set of FLAC discs, a single unchaptered M4B, a pile of files whose
    names encode a series position nobody agreed on -- and produces one M4B per
    book: correctly chaptered, tagged from Audible, and filed as
    ``Author/Series/Title`` where Plex and Audiobookshelf can find it.

    The hard part is not the transcode. It is working out what the files
    actually ARE when the folder name is wrong, the tags are missing, and the
    only clue that two files are one book is that their durations sum to a
    plausible runtime.

Key Components:
    - config: THE keystone. Reads config, validates, sets up logging, passes
      everything down. The only module that reads the environment.
    - models: Pydantic shapes for every value crossing a boundary.
    - utils: the shared floor -- logging, ffmpeg, http, paths.
    - db: SQLite state via stdlib sqlite3 and a Pydantic row factory. No ORM.
    - services: one concern per module -- discover, concat, convert, identify,
      tag, organize, diff.
    - apps: entry points. Parse args, build config, run.

Architecture:
    Configuration flows one way. ``load_settings()`` runs once at startup and
    hands each service the section it needs through its constructor; nothing
    reaches back for a global. That is what makes a service testable with a
    fake instead of a mutated environment.

    Values crossing a module boundary are Pydantic models, never bare dicts.
    The pre-rewrite code passed 68 ``dict[str, Any]`` between layers, so a
    layer receiving one knew only what the code that built it happened to put
    there -- and a malformed API payload surfaced as a ``KeyError`` three
    frames from the cause.

Pattern/Convention:
    Import concrete names from the submodule, never through this package::

        from audiobook_pipeline.models.chapter import Chapter

    Re-exporting here would make every import pull in every submodule, which is
    how an import cycle gets built by accident.

Example:
    >>> from audiobook_pipeline.config import load_settings
    >>> settings = load_settings(configure_logging=False)
    >>> settings.encoding.codec
    'aac'

See Also:
    - _plans/python-rewrite-sequence.md: what is built in what order, and why
    - _DOCS/STANDARDS-python.md: the standard this implements

---
*Auto-generated from `__init__.py` by `_githooks/generate_folder_docs.py`.*
