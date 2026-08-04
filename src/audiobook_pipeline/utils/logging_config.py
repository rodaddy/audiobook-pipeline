"""Every logging sink the application has, configured in exactly one place.

Purpose:
    Called once, by ``config.load_settings``, before anything else exists. No
    other module calls ``logger.add`` or ``logger.remove``.

WHY THAT MATTERS MORE THAN IT SOUNDS
    The pre-rewrite code configured logging in two places: ``config.py`` for
    pipeline runs and ``cli_audit.py`` inline for the audit command. Two
    definitions of where log output goes means changing the format requires
    finding every caller that reimplemented it, and the two drifted -- the
    audit path had no file sink at all, so a failed audit left no record.

THE THREE SINKS, AND WHY ALL THREE
    Each answers a different question, and a sink that answers none is noise:

    - **Console** is what you WATCH while a conversion runs. Human-formatted,
      level-filtered, stderr so it composes with a shell pipeline.
    - **Rotating file** is what you TAIL afterwards. Always DEBUG regardless of
      console level -- the whole point is that the detail exists when something
      failed an hour ago and the console scrollback is gone.
    - **Structured JSON** is what a log PLATFORM ingests. Off by default; a
      desktop user does not need it. The question it answers is "which books
      failed at the convert stage last week", which against human-formatted
      lines means re-parsing them with a regex.

Pattern/Convention:
    Modules bind a stage name once at import and use that logger::

        log = logger.bind(stage="convert")
        log.info("encoding {}", book.title)

    The stage appears in a fixed-width column, so a multi-stage run reads as
    columns rather than prose. Records with no bound stage still format
    correctly -- see ``_default_stage``.

Example:
    >>> from audiobook_pipeline.config import LoggingSettings
    >>> from pathlib import Path
    >>> setup(LoggingSettings(file_sink=False), log_dir=Path("data/logs"))

See Also:
    - audiobook_pipeline.config.LoggingSettings: the only input to setup()
    - _DOCS/STANDARDS-python.md ## Logging
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Record

    from audiobook_pipeline.config import LoggingSettings

#: Console and file share one format so a line means the same thing in both.
#: Fixed-width level and stage columns: a run that interleaves stages is read
#: by scanning a column, which ragged output makes impossible.
LOG_FORMAT = (
    "{time:YYYY-MM-DDTHH:mm:ssZ} | {level: <8} | {extra[stage]: <12} | {message}"
)


class ProcessStderrUnavailableError(RuntimeError):
    """Raised when the interpreter has no process stderr for console logging."""

    def __init__(self) -> None:
        """Describe the missing console sink and the required remediation."""
        super().__init__(
            "process stderr is unavailable; cannot configure console logging"
        )


def _default_stage(record: Record) -> bool:
    """Give every record a ``stage`` so the format string cannot fail.

    loguru raises a KeyError inside its own formatter when a format references
    an ``extra`` key the record lacks -- and a logging system that raises while
    reporting an error destroys the diagnostic it was called to produce. This
    filter runs on every sink and defaults the key rather than trusting each
    call site to bind it.

    Args:
        record: The loguru record, mutated in place.

    Returns:
        Always True. This is a filter by signature only; it exists for the
        side effect and never suppresses a record.
    """
    record["extra"].setdefault("stage", "")
    return True


def setup(settings: LoggingSettings, *, log_dir: Path) -> None:
    """Configure all logging sinks. Call once, from ``load_settings``.

    Removes loguru's default stderr handler first, so calling this twice in one
    process replaces the sinks rather than doubling every line.

    Args:
        settings: Sink toggles, level, rotation, retention.
        log_dir: Where file sinks are written. Created if absent, but only
            when a file sink is actually enabled -- a console-only run should
            not leave an empty directory behind.
    """
    process_stderr = sys.__stderr__
    if process_stderr is None:
        raise ProcessStderrUnavailableError

    logger.remove()
    logger.add(
        process_stderr,
        format=LOG_FORMAT,
        level=settings.level,
        filter=_default_stage,
        backtrace=settings.verbose_tracebacks,
        diagnose=settings.verbose_tracebacks,
    )

    if not (settings.file_sink or settings.json_sink):
        return

    log_dir.mkdir(parents=True, exist_ok=True)

    if settings.file_sink:
        logger.add(
            str(log_dir / "pipeline.log"),
            format=LOG_FORMAT,
            # DEBUG regardless of the console level. The file exists for the
            # investigation that happens after the console is gone; matching it
            # to the console level would throw away exactly the detail that
            # investigation needs.
            level="DEBUG",
            rotation=settings.rotation,
            retention=settings.retention,
            filter=_default_stage,
            backtrace=settings.verbose_tracebacks,
            diagnose=settings.verbose_tracebacks,
        )

    if settings.json_sink:
        logger.add(
            str(log_dir / "pipeline.jsonl"),
            level="DEBUG",
            rotation=settings.rotation,
            retention=settings.retention,
            serialize=True,
            filter=_default_stage,
        )
