r"""The ffmpeg and ffprobe boundary. OWNED code, under a documented exception.

WHY THIS IS HAND-WRITTEN, AND WHAT WAS REJECTED
    ``_DOCS/STANDARDS-python.md`` requires reaching for a maintained library
    before writing your own. Evaluated 2026-08-02, all three candidates
    rejected, and the reasons are recorded here so the next reader does not
    re-litigate it:

    - **ffmpeg-python** -- unmaintained. An abandoned dependency is worse than
      owned code, because owned code at least has an owner.
    - **ffmpy** (522 stars, active) -- builds a command line and calls
      ``subprocess``. That is the part that is already trivial. It parses no
      ffprobe output, so it would remove zero lines of the code below, which is
      almost entirely parsing.
    - **pyffmpeg** -- bundles its own ffmpeg binary. Wrong for a tool whose
      users install ffmpeg through their package manager and expect their own
      codecs and version.

    Per the standard, owning this is not a licence to skip reading the
    reference implementations. The command construction below follows ffmpy's
    approach; the JSON parsing follows what ffprobe actually documents.

WHAT FFMPEG SILENTLY WILL NOT DO
    ffmpeg accepts ``-metadata ASIN=...``, exits 0, and writes nothing. MP4 has
    no standard atom for it, and unknown keys are dropped without a warning.
    The same is true of ``sort_album`` and ``publisher``. Those tags go through
    mutagen (``utils.tagging``) as freeform atoms instead. This module does not
    pretend to write them.

Key Components:
    - probe: one ffprobe call, parsed into a validated ProbeResult
    - build_chapter_metadata: a chapter table as FFMETADATA1 text
    - FfmpegError: a failure carrying the stderr that explains it

Example:
    >>> build_chapter_metadata(())
    ';FFMETADATA1\\n'

See Also:
    - audiobook_pipeline.models.media: the shape probe() returns
    - _DOCS/STANDARDS-python.md ## LAW: do not hand-roll a solved problem
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.models.media import AudioStream, ProbeResult
from audiobook_pipeline.utils.paths import sanitize_chapter_title

log = logger.bind(stage="ffmpeg")

#: Seconds. A probe that has not answered by now is hung, not slow -- ffprobe
#: reads a header, not the whole file. Without a timeout a single corrupt file
#: stalls an overnight batch indefinitely.
PROBE_TIMEOUT_SECONDS = 120

#: Encoding a 25-hour audiobook is not a 2-minute job. Generous rather than
#: tuned: the timeout exists to catch a WEDGED process, not to enforce a
#: performance budget, and a limit that kills real work 90% of the way through
#: a long book costs far more than one that waits too long for a stuck one.
ENCODE_TIMEOUT_SECONDS = 6 * 60 * 60

#: FFMETADATA1 chapter timebase. Milliseconds, matching the models, so no
#: conversion happens at the point the file is written.
CHAPTER_TIMEBASE = "1/1000"


class FfmpegError(RuntimeError):
    """An ffmpeg or ffprobe invocation failed.

    Carries stderr because that is where ffmpeg says what was actually wrong;
    the exit code alone distinguishes almost nothing.
    """

    def __init__(self, command: str, stderr: str) -> None:
        """Record the command that failed and what it printed.

        Args:
            command: The binary invoked, e.g. ``ffprobe``.
            stderr: Captured standard error.
        """
        self.command = command
        self.stderr = stderr.strip()
        super().__init__(f"{command} failed: {self.stderr or '(no stderr)'}")


def _run(args: list[str], *, timeout: int) -> str:
    """Run a command and return stdout, raising with stderr on failure.

    Args:
        args: Full argument vector. Never a shell string -- a book title
            containing a quote would otherwise be a shell injection.
        timeout: Seconds before the process is killed.

    Returns:
        Captured stdout.

    Raises:
        FfmpegError: Non-zero exit, a missing binary, or a timeout. All three
            are the same thing to a caller: the probe did not happen.
    """
    try:
        # argv form, never shell=True: a book title containing a quote
        # would otherwise be a shell injection.
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        msg = (
            f"{args[0]} is not installed or not on PATH. "
            f"ACTION REQUIRED: install ffmpeg (brew install ffmpeg, "
            f"apt install ffmpeg)."
        )
        raise FfmpegError(args[0], msg) from exc
    except subprocess.TimeoutExpired as exc:
        raise FfmpegError(args[0], f"timed out after {timeout}s") from exc

    if completed.returncode != 0:
        raise FfmpegError(args[0], completed.stderr)
    return completed.stdout


def _parse_chapters(raw: list[dict[str, Any]]) -> ChapterSet:
    """Build a chapter table from ffprobe's chapter list.

    ffprobe reports bounds as FLOAT SECONDS in strings. Converting to integer
    milliseconds here means the rest of the pipeline never carries a float
    offset, so chapter starts cannot accumulate representation error across a
    concatenation.

    Malformed entries are SKIPPED rather than failing the probe: a single
    unparseable chapter in a 160-chapter book should not cost the other 159.
    Every skip is logged, because a silent drop is how defect 10 looked.

    Args:
        raw: The ``chapters`` array from ffprobe's JSON.

    Returns:
        A validated chapter table, possibly empty.
    """
    chapters: list[Chapter] = []
    for index, entry in enumerate(raw, start=1):
        try:
            start_ms = int(float(entry["start_time"]) * 1000)
            end_ms = int(float(entry["end_time"]) * 1000)
        except (KeyError, TypeError, ValueError):
            log.warning("chapter {} has unreadable bounds, skipping", index)
            continue

        if end_ms <= start_ms:
            log.warning(
                "chapter {} ends at {}ms before its start {}ms, skipping",
                index,
                end_ms,
                start_ms,
            )
            continue

        title = sanitize_chapter_title(
            str(entry.get("tags", {}).get("title", "")) or f"Chapter {index}"
        )
        chapters.append(Chapter(start_ms=start_ms, end_ms=end_ms, title=title))

    return ChapterSet(chapters=tuple(chapters), source="embedded")


def _parse_stream(streams: list[dict[str, Any]]) -> AudioStream:
    """Extract audio parameters from ffprobe's stream list.

    Args:
        streams: The ``streams`` array, already filtered to audio by the
            ``-select_streams a:0`` argument.

    Returns:
        The first audio stream's parameters.

    Raises:
        FfmpegError: No audio stream. A video file or a corrupt container
            reaches here, and continuing would produce a book with no audio.
    """
    if not streams:
        raise FfmpegError("ffprobe", "file contains no audio stream")

    stream = streams[0]
    bit_rate_raw = stream.get("bit_rate")
    return AudioStream(
        codec=str(stream.get("codec_name", "unknown")),
        sample_rate=int(stream.get("sample_rate", 0) or 0),
        channels=int(stream.get("channels", 0) or 0),
        bit_rate=int(bit_rate_raw) if bit_rate_raw else None,
    )


def probe(path: Path, *, timeout: int = PROBE_TIMEOUT_SECONDS) -> ProbeResult:
    """Read duration, stream parameters, chapters, and tags from one file.

    ONE ffprobe call for all four. The pre-rewrite code made separate calls for
    duration and chapters at different points in the pipeline, which doubled
    the process spawns on a batch and let the two disagree when a file changed
    between them.

    Args:
        path: Audio file to inspect.
        timeout: Seconds before giving up.

    Returns:
        Everything the probe reported, validated.

    Raises:
        FfmpegError: ffprobe failed, returned unparseable JSON, reported no
            audio stream, or reported a zero duration.
    """
    stdout = _run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_chapters",
            "-show_streams",
            "-select_streams",
            "a:0",
            str(path),
        ],
        timeout=timeout,
    )

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise FfmpegError("ffprobe", f"returned unparseable JSON for {path}") from exc

    fmt = payload.get("format", {})
    try:
        duration_ms = int(float(fmt["duration"]) * 1000)
    except (KeyError, TypeError, ValueError) as exc:
        raise FfmpegError("ffprobe", f"no readable duration for {path}") from exc

    if duration_ms <= 0:
        raise FfmpegError("ffprobe", f"{path} reports a duration of zero")

    return ProbeResult(
        path=path,
        duration_ms=duration_ms,
        stream=_parse_stream(payload.get("streams", [])),
        format_name=str(fmt.get("format_name", "unknown")),
        chapters=_parse_chapters(payload.get("chapters", [])),
        tags={str(k): str(v) for k, v in fmt.get("tags", {}).items()},
    )


def run_ffmpeg(args: list[str], *, timeout: int = ENCODE_TIMEOUT_SECONDS) -> str:
    """Run an ffmpeg command that writes a file rather than reporting on one.

    Separate from ``probe`` because the two have nothing in common but the
    binary: a probe is a sub-second question with a JSON answer, while an
    encode is an hours-long job whose output is a file on disk. Sharing one
    timeout would mean either killing encodes or waiting hours on a stuck
    probe.

    Args:
        args: Arguments AFTER the ``ffmpeg`` binary name. ``-nostdin`` and
            ``-y`` are prepended -- without ``-nostdin`` ffmpeg inherits the
            terminal and a batch run stops dead on the first "overwrite? [y/N]"
            prompt, hours in, with no indication why.
        timeout: Seconds before the process is killed.

    Returns:
        Captured stdout, which for an encode is normally empty; ffmpeg reports
        progress on stderr.

    Raises:
        FfmpegError: Non-zero exit, missing binary, or timeout.
    """
    return _run(["ffmpeg", "-nostdin", "-y", *args], timeout=timeout)


def build_chapter_metadata(chapters: tuple[Chapter, ...]) -> str:
    r"""Render a chapter table as FFMETADATA1 text.

    The format ffmpeg reads with ``-i chapters.txt -map_metadata 1``. Written
    here rather than assembled inline at the call site so the timebase and the
    escaping have exactly one definition.

    Args:
        chapters: Ordered chapter marks. May be empty, which produces a header
            with no chapters -- a valid file that adds nothing.

    Returns:
        FFMETADATA1 text, ready to write to a temporary file.

    Example:
        >>> print(build_chapter_metadata((Chapter(start_ms=0, end_ms=1000,
        ...     title="One"),)))  # doctest: +NORMALIZE_WHITESPACE
        ;FFMETADATA1
        [CHAPTER]
        TIMEBASE=1/1000
        START=0
        END=1000
        title=One
        <BLANKLINE>
    """
    lines = [";FFMETADATA1"]
    for chapter in chapters:
        # `=`, `;`, `#`, `\` and newline are FFMETADATA1 syntax. A title
        # containing one -- "Chapter 3: Cause = Effect" -- silently corrupts
        # every chapter after it if not escaped.
        title = (
            chapter.title
            .replace("\\", "\\\\")
            .replace("=", "\\=")
            .replace(";", "\\;")
            .replace("#", "\\#")
            .replace("\n", " ")
        )
        lines.extend([
            "[CHAPTER]",
            f"TIMEBASE={CHAPTER_TIMEBASE}",
            f"START={chapter.start_ms}",
            f"END={chapter.end_ms}",
            f"title={title}",
        ])
    return "\n".join(lines) + "\n"
