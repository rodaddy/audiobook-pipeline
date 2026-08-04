"""Deciding where a finished book lives, and putting it there.

Purpose:
    The last stage. Turns identified metadata into the library layout Plex
    reads -- ``Author/Series/Book N - Title/Book N - Title.m4b`` -- and moves
    the file into place without stranding it or overwriting anything.

WHY THE LAYOUT IS COPIED FROM THE LIBRARY, NOT CHOSEN
    712 M4Bs are already filed under /Volumes/media_files/AudioBooks in exactly
    that shape, per-book folder and all. Anything this writes has to look like
    they do, or Plex shows the new arrivals as a second, differently-shaped
    library beside the real one.

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

import os
import re
import shutil
import uuid
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.services.index import LibraryIndex
from audiobook_pipeline.utils.paths import sanitize_filename
from audiobook_pipeline.utils.text import (
    fold_accents,
    strip_punctuation,
    strip_subtitle,
    strip_year,
)

log = logger.bind(stage="organize")

#: A marker file forcing this directory to be treated as the author folder.
#: Exists for multi-author franchises (Dragonlance, Warhammer) where the
#: catalogue's "author" changes per book but the shelf should not.
AUTHOR_OVERRIDE_MARKER = ".author-override"

#: Words that can differ between two names for the same thing. Any OTHER extra
#: word means a different work -- "origins" is not punctuation.
#:
#: The second group is series-form nouns. They name the CONTAINER, not the work,
#: so a catalogue calling something "The Powder Mage Trilogy" and a shelf calling
#: it "Powder Mage" mean one series -- and treating them as two is what split
#: that author's folder on the first live run. Measured across the real library
#: (2026-08-02): dropping these merges 5 folder pairs, and all 5 are genuine
#: duplicates -- Dragonlance/Dragonlance Saga, Mistborn/Mistborn Saga,
#: Sprawl/Sprawl Trilogy Series. Zero false merges.
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
    "trilogy",
    "saga",
    "cycle",
    "series",
    "sequence",
    "duology",
    "quartet",
    "collection",
    # _normalize strips one trailing "s", so a name ending in a series noun
    # arrives here already singularised: "series" -> "serie", "saga" is
    # untouched but "sagas" -> "saga". Both spellings have to be listed or the
    # match depends on where in the name the word happened to fall.
    "serie",
})

#: Jaccard threshold for reordered or partially-matching names. 0.85, not 0.8:
#: "The Wheel of Time" (4 tokens) against "Origins of The Wheel of Time" (5)
#: scores 4/5 = 0.8, and must not match.
_SIMILARITY_THRESHOLD = 0.85

#: Two or more consecutive single letters, which is what an initialled name
#: becomes once its punctuation is gone: "r a salvatore", "j r r tolkien".
_INITIAL_RUN = re.compile(r"\b(?:[a-z] ){1,}[a-z]\b")


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
    # Strip accents before comparing. "The Children of Hurin" and "The Children
    # of Húrin" are one book filed twice, and both spellings are in the real
    # library. NFD splits a letter from its accent so the combining marks can be
    # dropped; this ALSO settles macOS's decomposed storage, where the same
    # title compares unequal to itself depending on where it was typed.
    text = fold_accents(name.lower())

    text = strip_year(text)
    text = _EDITION_NOTE.sub("", text)
    text = strip_punctuation(text)

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

    smaller, larger = sorted([desired_tokens, existing_tokens], key=len)

    # The shared core has to carry MEANING. Counting tokens instead was the
    # earlier rule, and it required two of them -- which let "Mistborn Saga"
    # and "Mistborn" sit in the library as separate series, because the
    # smaller name is one word long and could never qualify. A single
    # distinctive word is a perfectly good name; a single STOP word is not,
    # which is what stops "The" matching "A".
    if (
        smaller <= larger
        and (smaller - _STOP_WORDS)
        and (larger - smaller) <= _STOP_WORDS
    ):
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


def shelf_title(metadata: BookMetadata) -> str:
    """The book's title as the library spells it, without the subtitle.

    Audible's title carries an edition subtitle after a colon -- "Forsworn: A
    Powder Mage Novella". The existing library does not: measured against
    /Volumes/media_files/AudioBooks, the shelf says "Book 1 - The Name of the
    Wind", never "The Name of the Wind: Kingkiller Chronicle Day One".

    Keeping the subtitle also drags a colon into the filename, which
    ``sanitize_filename`` has to turn into an underscore -- so the first live
    run produced "Book 0.1 - Forsworn_ A Powder Mage Novella.m4b" against 712
    existing files that contain no such thing.

    Args:
        metadata: The identified book.

    Returns:
        The title up to its subtitle. The full title survives in the tags,
        which is where a reader who wants it will look.
    """
    return strip_subtitle(metadata.title)


def book_stem(metadata: BookMetadata) -> str:
    """The name shared by a book's folder and its file.

    Args:
        metadata: The identified book.

    Returns:
        ``Book N - Title`` for a series entry, ``Title`` otherwise. The YEAR is
        deliberately absent: it lives in the tags, and putting it in the name
        means the same book under two names when an edition changes.
    """
    title = shelf_title(metadata)
    if metadata.has_series and metadata.series_position:
        return sanitize_filename(f"Book {metadata.series_position} - {title}")
    return sanitize_filename(title)


def build_library_path(
    library_root: Path, metadata: BookMetadata, *, index: LibraryIndex | None = None
) -> Path:
    """Work out where a book belongs in the library.

    Layout is ``Author/Series/Book N - Title/Book N - Title.m4b``. The book gets
    its OWN FOLDER, matching the 712 files already in the library -- Plex reads
    per-book folders for cover art and companion files, and a book written
    loose into the series folder is the odd one out.

    The series level is omitted for a standalone. Each directory level reuses an
    existing near-duplicate rather than creating a second spelling of it.

    Args:
        library_root: The library's root directory.
        metadata: The identified book.
        index: Durable folder and author reuse state, when batch wiring supplies it.

    Returns:
        The full destination path, including filename.
    """
    author = sanitize_filename(metadata.author or "Unknown Author")
    if index is not None:
        author = index.match_author(author)
    destination = library_root / _reused_folder(library_root, author, index)

    if metadata.has_series:
        series = sanitize_filename(metadata.series)
        destination = destination / _reused_folder(destination, series, index)

    stem = book_stem(metadata)
    destination = destination / _reused_folder(destination, stem, index)
    return destination / f"{stem}.m4b"


def _reused_folder(parent: Path, desired: str, index: LibraryIndex | None) -> str:
    """Use the durable index when supplied, otherwise preserve direct behavior."""
    if index is None:
        return reuse_existing_folder(parent, desired)
    return index.reuse_existing(parent, desired)


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


def place_claimed_book(source: Path, destination: Path, *, move: bool = True) -> Path:
    """Place at an already-reserved exact destination without suffixing or overwrite."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    try:
        shutil.copy2(source, temporary)
        os.link(temporary, destination)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
    temporary.unlink()
    if move:
        source.unlink()
    return destination
