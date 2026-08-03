"""Deciding where a finished book lives, and putting it there.

Purpose:
    The last stage. Turns identified metadata into the library layout Plex
    reads -- ``Author/Series/Book N - Title.m4b`` -- and moves the file into
    place without stranding it or overwriting anything.

WHY AN EXISTING NEAR-DUPLICATE FOLDER IS REUSED
    The same author arrives spelled a dozen ways: "Ann Leckie" and
    "Ann Leckie - The Raven Tower", "Food: A Love Story" and "Food A Love Story
    (2014)". Creating a new folder for each spelling is how a library of 700
    books grows 900 directories and Plex shows the same author four times. A
    desired name that near-matches an existing sibling reuses the SIBLING.

WHY A MEANINGFUL EXTRA WORD BLOCKS THE MATCH
    "The Wheel of Time" must NOT match "Origins of The Wheel of Time" -- they
    are different works. Extra tokens are allowed only when every one of them
    is a stop word, which is what separates a punctuation difference from a
    different book.

Example:
    >>> _normalize("Food: A Love Story (2014)")
    'food a love story'

See Also:
    - audiobook_pipeline.utils.paths: filesystem-safe name sanitizing
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.utils.paths import sanitize_filename

log = logger.bind(stage="organize")

#: A marker file forcing this directory to be treated as the author folder.
#: Exists for multi-author franchises (Dragonlance, Warhammer) where the
#: catalogue's "author" changes per book but the shelf should not.
AUTHOR_OVERRIDE_MARKER = ".author-override"

#: Words that can differ between two names for the same thing. Any OTHER extra
#: word means a different work -- "origins" is not punctuation.
_STOP_WORDS = frozenset({
    "the",
    "a",
    "an",
    "of",
    "and",
    "in",
    "at",
    "to",
    "by",
    "for",
    "on",
    "with",
})

#: Jaccard threshold for reordered or partially-matching names. 0.85, not 0.8:
#: "The Wheel of Time" (4 tokens) against "Origins of The Wheel of Time" (5)
#: scores 4/5 = 0.8, and must not match.
_SIMILARITY_THRESHOLD = 0.85

#: Two or more consecutive single letters, which is what an initialled name
#: becomes once its punctuation is gone: "r a salvatore", "j r r tolkien".
_INITIAL_RUN = re.compile(r"\b(?:[a-z] ){1,}[a-z]\b")

_YEAR = re.compile(r"\s*\(\d{4}\)")

#: Parentheticals that describe the EDITION rather than distinguish the work.
#: Deliberately a fixed list, not "anything in brackets": a library really does
#: hold "Harry Potter (Jim Dale)" beside "Harry Potter (Stephen Fry)", and
#: stripping every parenthetical merges two different narrations into one
#: folder -- observed on the real library, 2026-08-02, as the ONLY collision
#: among 178 series folders.
_EDITION_NOTE = re.compile(
    r"\s*\((?:unabridged|abridged|audiobook|audio ?book|dramati[sz]ed|"
    r"deluxe|special|anniversary|revised|reissue|remastered)[^)]*\)",
    re.IGNORECASE,
)
_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")


def _normalize(name: str) -> str:
    """Reduce a folder name to its comparable core.

    Args:
        name: A folder name as it appears on disk.

    Returns:
        Lowercased, with years, parentheticals, and punctuation removed. A
        single trailing "s" is dropped so "Chronicles" matches "Chronicle";
        that also turns "James" into "Jame", which is harmless because BOTH
        sides get the same treatment.
    """
    text = _YEAR.sub("", name.lower())
    text = _EDITION_NOTE.sub("", text)
    text = _PUNCTUATION.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()

    # Join runs of single letters. "R.A. Salvatore" loses its dots to become
    # "ra salvatore" (2 tokens) while "R A Salvatore" is "r a salvatore" (3),
    # so the two spellings of ONE author share no comparable token set and each
    # gets its own folder -- which is the exact duplicate this function exists
    # to prevent. Observed while writing test_existing_author_folder_is_reused.
    text = _INITIAL_RUN.sub(lambda m: m.group(0).replace(" ", ""), text)

    return text[:-1] if text.endswith("s") else text


def is_near_match(desired: str, existing: str) -> bool:
    """Whether two normalized names refer to the same thing.

    Args:
        desired: The normalized name we want.
        existing: The normalized name already on disk.

    Returns:
        True when they should share a folder.
    """
    if desired == existing:
        return True

    desired_tokens = set(desired.split())
    existing_tokens = set(existing.split())

    # A single common word ("the") is not enough to match on.
    if len(desired_tokens) < 2 and len(existing_tokens) < 2:
        return False

    smaller, larger = sorted([desired_tokens, existing_tokens], key=len)
    if len(smaller) >= 2 and smaller <= larger and (larger - smaller) <= _STOP_WORDS:
        return True

    union = desired_tokens | existing_tokens
    overlap = len(desired_tokens & existing_tokens) / len(union) if union else 0.0
    return overlap >= _SIMILARITY_THRESHOLD


def reuse_existing_folder(parent: Path, desired: str) -> str:
    """Find a sibling folder that means the same thing as ``desired``.

    Args:
        parent: Directory to look in.
        desired: The folder name we would otherwise create.

    Returns:
        The existing folder's name when one near-matches, otherwise ``desired``
        unchanged.
    """
    if not parent.is_dir():
        return desired
    if (parent / desired).exists():
        return desired

    desired_norm = _normalize(desired)
    for existing in sorted(parent.iterdir(), key=lambda p: p.name):
        if existing.is_dir() and is_near_match(desired_norm, _normalize(existing.name)):
            log.debug("reusing {!r} for {!r}", existing.name, desired)
            return existing.name
    return desired


def book_filename(metadata: BookMetadata) -> str:
    """The filename for a finished book.

    Args:
        metadata: The identified book.

    Returns:
        ``Book N - Title.m4b`` for a series entry, ``Title.m4b`` otherwise.
        The YEAR is deliberately absent: it lives in the tags, and putting it
        in the name means the same book under two names when an edition
        changes.
    """
    if metadata.has_series and metadata.series_position:
        stem = f"Book {metadata.series_position} - {metadata.title}"
    else:
        stem = metadata.title
    return sanitize_filename(f"{stem}.m4b")


def build_library_path(library_root: Path, metadata: BookMetadata) -> Path:
    """Work out where a book belongs in the library.

    Layout is ``Author/Series/Book N - Title.m4b``, with the series level
    omitted for a standalone. Each directory level reuses an existing
    near-duplicate rather than creating a second spelling of it.

    Args:
        library_root: The library's root directory.
        metadata: The identified book.

    Returns:
        The full destination path, including filename.
    """
    author = sanitize_filename(metadata.author or "Unknown Author")
    author = reuse_existing_folder(library_root, author)
    destination = library_root / author

    if metadata.has_series:
        series = sanitize_filename(metadata.series)
        series = reuse_existing_folder(destination, series)
        destination = destination / series

    return destination / book_filename(metadata)


def find_author_override(start: Path, stop_at: Path) -> Path | None:
    """Look for an ``.author-override`` marker at or above ``start``.

    Args:
        start: Directory to begin at.
        stop_at: Directory to stop at, inclusive. Bounds the climb so a marker
            outside the library can never redirect a book into it.

    Returns:
        The directory holding the marker, or None.
    """
    current = start
    while True:
        if (current / AUTHOR_OVERRIDE_MARKER).is_file():
            return current
        if current == stop_at or current.parent == current:
            return None
        current = current.parent


def _unique_destination(destination: Path) -> Path:
    """Find a free path, never overwriting an existing file.

    Args:
        destination: The desired path.

    Returns:
        ``destination`` when free, otherwise the same name carrying a numeric
        suffix -- ``Title (2).m4b``. Overwriting is never correct here: the
        existing
        file is somebody's audiobook, and a collision means the identification
        was ambiguous -- which a human should see, not a silent replacement.
    """
    if not destination.exists():
        return destination

    for index in range(2, 100):
        candidate = destination.with_name(
            f"{destination.stem} ({index}){destination.suffix}"
        )
        if not candidate.exists():
            log.warning(
                "{} exists; writing {} instead", destination.name, candidate.name
            )
            return candidate

    msg = f"cannot find a free name for {destination}"
    raise FileExistsError(msg)


def place_book(source: Path, destination: Path, *, move: bool = True) -> Path:
    """Put a finished book at its library path.

    Args:
        source: The finished M4B.
        destination: Where it belongs.
        move: Move when True, copy when False. Copying leaves the source in
            place for a run that should not consume its input.

    Returns:
        The path actually written, which differs from ``destination`` when that
        name was taken.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    final = _unique_destination(destination)

    # shutil, not Path.rename: the library is normally a different filesystem
    # (an NFS mount), and rename fails across devices with EXDEV.
    if move:
        shutil.move(str(source), str(final))
    else:
        shutil.copy2(str(source), str(final))

    log.info("{} -> {}", "moved" if move else "copied", final)
    return final
