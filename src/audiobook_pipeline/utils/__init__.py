"""Shared, dependency-free helpers used across every part of the pipeline.

Purpose:
    The bottom of the import graph. Anything here may be imported by any
    service, model, or app; nothing here imports from those, so this package
    can never participate in a cycle.

Key Components:
    - logging_config: every logging sink. The only consumer of
      ``config.LoggingSettings``, and it is called exactly once, by
      ``config.load_settings``.
    - ffmpeg: the ffmpeg/ffprobe subprocess boundary. OWNED code under the
      standard's narrow exception -- see that module's docstring for the
      libraries evaluated and why each was rejected.
    - http: an httpx client with tenacity retry. No hand-rolled backoff.
    - paths: filename sanitizing and library path construction.

Architecture:
    ``utils/`` is the shared floor, not a junk drawer. A module earns a place
    here by being needed in two or more services AND having no dependency on
    any of them. A helper used by exactly one service belongs in that service.

    The failure this prevents is the reach-across import: ``services/concat.py``
    importing a helper from ``services/organize.py`` because that is where it
    happened to be written first. That builds a dependency graph nobody
    designed, and it is how a small refactor turns into a chain of import
    errors.

Pattern/Convention:
    Import concrete names from the submodule, not from this package::

        from audiobook_pipeline.utils.ffmpeg import probe_duration

    Re-exporting through this ``__init__`` would make every ``utils`` import
    pull in every submodule -- including the subprocess and HTTP layers -- which
    is both slow and how an import cycle gets built by accident.

See Also:
    - audiobook_pipeline.config: the keystone that calls into logging_config
    - _DOCS/STANDARDS-python.md: the utils/ contract
"""

from __future__ import annotations
