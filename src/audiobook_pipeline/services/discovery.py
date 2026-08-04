"""Walking a source tree and deciding what each folder actually holds.

Purpose:
    The first stage, and the one with the most expensive failure mode. Every
    later stage trusts its verdict: concat joins what discovery called one book,
    and the library ends up with whatever shape this produced.

TWO DEFECTS THIS STAGE EXISTS TO PREVENT
    Defect 3 -- a loose audio file at a collection root PRUNED THE TREE BELOW
    IT. The old walk treated "this directory contains audio" as "this directory
    is a book", stopped descending, and collapsed 11 book directories into 1.
    Here, a directory yields a candidate AND is still descended into; the two
    decisions are independent because they always were.

    Defect 4 -- any folder with several audio files was treated as one chaptered
    book, so a folder of 40 novels became a single 510-hour concat. The verdict
    is ``BookDirectory.holds_separate_books``, which uses the MEDIAN duration
    and fails safe toward "separate".

WHY NOTHING IS CLASSIFIED BY EXTENSION ALONE
    ``.m4b`` does not mean "already a finished book" and ``.mp3`` does not mean
    "needs converting" -- an ``.m4b`` with one chapter and a wrong title needs
    the same work as a folder of MP3s. Extensions decide only whether a file is
    AUDIO AT ALL (``SOURCE_EXTENSIONS``, deliberately wide). What the audio IS
    comes from probing it, which is the pipeline's actual job.

Example:
    >>> _is_hidden(Path("/x/.DS_Store"))
    True

See Also:
    - audiobook_pipeline.models.book: the classification rules themselves
    - _plans/python-rewrite-sequence.md: defects 1, 3, and 4
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.book import SOURCE_EXTENSIONS, AudioFile, BookDirectory
from audiobook_pipeline.utils.ffmpeg import FfmpegError, probe

log = logger.bind(stage="discovery")

_DISC_DIRECTORY = re.compile(
    r"^(?:cd|disc|disk|part|vol(?:ume)?)\s*[_ -]*(\d+)\b", re.IGNORECASE
)


def _is_hidden(path: Path) -> bool:
    """Whether a path is hidden or macOS resource-fork junk.

    Args:
        path: The path to test.

    Returns:
        True for dotfiles. ``._Foo.mp3`` (AppleDouble) is caught by the same
        rule, and it matters: those files carry a valid audio extension, are
        4 KB of metadata, and probe as zero-duration audio.
    """
    return path.name.startswith(".")


def is_source_audio(path: Path, *, excluded: frozenset[Path] = frozenset()) -> bool:
    """Whether a file is audio the pipeline could work with.

    Args:
        path: The file to test.
        excluded: Absolute output paths to keep out of this discovery run.

    Returns:
        True when the suffix is a known audio extension. This is the ONLY
        extension-based decision in the pipeline, and it answers "is this audio"
        -- never "is this already finished".
    """
    return (
        path.is_file()
        and not _is_hidden(path)
        and path.resolve() not in excluded
        and path.suffix.lower() in SOURCE_EXTENSIONS
    )


def _audio_files_in(directory: Path, excluded: frozenset[Path]) -> list[Path]:
    """Audio files directly inside a directory, in play order.

    Args:
        directory: The directory to list. Not recursive.
        excluded: Absolute output paths to keep out of this discovery run.

    Returns:
        Sorted paths. Sorted HERE because this is where the filenames are still
        available -- by the time these reach concat, the order they arrive in is
        the answer, and there is nothing left to re-derive it from.
    """
    return sorted(
        (
            child
            for child in directory.iterdir()
            if is_source_audio(child, excluded=excluded)
        ),
        key=lambda p: p.name.lower(),
    )


def _disc_index(directory: Path) -> int | None:
    """Return a disc number for conventional multi-disc directory names."""
    match = _DISC_DIRECTORY.match(directory.name)
    return int(match.group(1)) if match else None


def _disc_audio_paths(directory: Path, excluded: frozenset[Path]) -> list[Path]:
    """Collect audio below one disc directory in play order."""
    paths = _audio_files_in(directory, excluded)
    for child in sorted(directory.iterdir(), key=lambda path: path.name.lower()):
        if child.is_dir() and not _is_hidden(child):
            paths.extend(_disc_audio_paths(child, excluded))
    return paths


def _grouped_disc_paths(
    directory: Path, paths: list[Path], excluded: frozenset[Path]
) -> tuple[list[Path], frozenset[Path]]:
    """Return grouped disc audio and child directories consumed by that group.

    A parent with direct audio plus a named disc child is one book. A parent
    with no audio of its own needs at least two named disc children before that
    conclusion is safe; one ``CD1`` directory might simply be the supplied
    source root.
    """
    parts = [
        child
        for child in directory.iterdir()
        if child.is_dir() and not _is_hidden(child) and _disc_index(child) is not None
    ]
    if not parts or (not paths and len(parts) < 2):
        return paths, frozenset()

    ordered_parts = sorted(parts, key=lambda part: (_disc_index(part) or 0, part.name))
    disc_paths = [
        path for part in ordered_parts for path in _disc_audio_paths(part, excluded)
    ]
    if not disc_paths:
        return paths, frozenset()
    return [*paths, *disc_paths], frozenset(parts)


def _probe_duration(path: Path) -> int | None:
    """Read one file's duration in milliseconds.

    Args:
        path: The audio file.

    Returns:
        Duration in ms, or None when the file cannot be probed -- which is a
        real answer, not an error: a truncated download and a text file renamed
        to ``.mp3`` both land here, and neither should stop the walk.

        Zero-duration files arrive as ``FfmpegError`` rather than a zero,
        because ``probe`` refuses them at its own boundary. There is no
        ``<= 0`` check here for that reason: it would be unreachable, and an
        unreachable guard reads as protection that is not there.
    """
    try:
        return probe(path).duration_ms
    except FfmpegError as exc:
        logger.warning("skipping unreadable audio {}: {}", path, exc)
        return None


def _build_candidate(directory: Path, paths: list[Path]) -> BookDirectory | None:
    """Probe a directory's files and build its candidate.

    Args:
        directory: The directory holding the files.
        paths: Audio files in play order.

    Returns:
        The candidate, or None when nothing in the directory could be probed.
    """
    files = tuple(
        AudioFile(path=path, duration_ms=duration)
        for path in paths
        if (duration := _probe_duration(path)) is not None
    )
    if not files:
        log.warning("no readable audio in {}", directory)
        return None
    return BookDirectory(path=directory, files=files)


def walk_candidates(
    root: Path, *, excluded: frozenset[Path] = frozenset()
) -> Iterator[BookDirectory]:
    """Yield a candidate for every directory in the tree that holds audio.

    THE WALK NEVER STOPS EARLY. A directory that holds audio yields a candidate
    and is STILL descended into, because a collection root with a stray file at
    the top is exactly the shape that pruned 11 book directories into 1.

    Args:
        root: Directory to walk. Also considered itself.
        excluded: Absolute output paths to keep out of this discovery run.

    Yields:
        One candidate per directory containing readable audio, parents first.
    """
    if not root.is_dir():
        return

    paths = _audio_files_in(root, excluded)
    grouped_paths, consumed_parts = _grouped_disc_paths(root, paths, excluded)
    if grouped_paths:
        candidate = _build_candidate(root, grouped_paths)
        if candidate is not None:
            yield candidate

    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and not _is_hidden(child) and child not in consumed_parts:
            yield from walk_candidates(child, excluded=excluded)


def discover_books(
    root: Path, *, excluded: frozenset[Path] = frozenset()
) -> list[BookDirectory]:
    """Find every book under a source directory.

    A candidate holding SEPARATE books is split into one entry per file, so the
    caller receives books rather than folders. A candidate that is one
    multi-file book is kept whole for concat.

    Args:
        root: Directory to search.
        excluded: Absolute output paths to keep out of this discovery run.

    Returns:
        One entry per book found, in tree order.
    """
    books: list[BookDirectory] = []
    for candidate in walk_candidates(root, excluded=excluded):
        if candidate.is_multi_file_book:
            books.append(candidate)
            continue
        books.extend(
            BookDirectory(path=candidate.path, files=(audio,))
            for audio in candidate.files
        )

    log.info("discovered {} book(s) under {}", len(books), root)
    return books
