"""The command-line applications. Thin by design.

Purpose:
    An app parses arguments, calls ``load_settings`` once, and hands off to
    ``services``. It contains no domain logic, so the same conversion can be
    driven from a script, a test, or another program without going through
    argument parsing.

WHY THE APPS ARE THIN
    The pre-rewrite ``cli.py`` grew a hand-rolled ``.env`` parser and its own
    configuration precedence, which is how the keystone rule got broken --
    ``cli_audit.py:122`` read ``os.environ["PLEX_TOKEN"]`` directly. A CLI that
    only marshals arguments has nowhere to put that.

Key Components:
    - convert: turn a directory of audio into library-ready M4Bs
    - audit: report on what the library and database contain

Pattern/Convention:
    Each child package exposes one ``__main__.py`` console entry point. Keep
    parsing and presentation here; put reusable work in ``services``.

Example:
    >>> from audiobook_pipeline.apps.convert.__main__ import main
    >>> callable(main)
    True

See Also:
    - audiobook_pipeline.config: load_settings, the one sanctioned entry point
"""

from __future__ import annotations
