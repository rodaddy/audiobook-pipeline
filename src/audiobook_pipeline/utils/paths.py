r"""Turning book metadata into filenames that survive every filesystem.

Purpose:
    A library path is built from data the pipeline did not choose: an author
    name with a slash in it, a title with a colon, a chapter heading with a
    newline. Written unsanitized, those either fail the write or -- worse --
    silently create a directory nobody intended, because ``Foo/Bar`` is two
    path components.

WHY SANITIZING IS NOT THE SAME AS ESCAPING
    The goal is a name a HUMAN recognizes on a shelf, not a round-trippable
    encoding. ``Chapter: One`` becomes ``Chapter One``, not ``Chapter%3A One``.
    Nothing reads these names back to recover the original; the metadata tags
    hold the real values, and the filename exists to be browsed.

THE THREE FILESYSTEMS THAT DISAGREE
    This runs on macOS, writes to Linux NFS, and the result is read by Plex on
    whatever it likes. The strictest rule wins at every point:

    - Windows/SMB reserves ``< > : " / \\ | ? *`` and forbids a trailing dot
      or space, which macOS and Linux both allow.
    - HFS+/APFS is case-insensitive; ext4 is not.
    - A 255-BYTE component limit is common, and it is bytes, not characters --
      a CJK title hits it at ~85 characters.

Example:
    >>> sanitize_filename('The Book: A Story/Part 2')
    'The Book_ A Story_Part 2'
    >>> sanitize_chapter_title('Chapter: One')
    'Chapter One'

See Also:
    - audiobook_pipeline.models.metadata: where these names come from
"""

from __future__ import annotations

import re
import unicodedata

#: Characters no filesystem in the chain accepts, mapped to underscore. The
#: control-character range is included because a stray newline in a scraped
#: title produces a filename that breaks every shell tool downstream.
#: Extensions this pipeline treats as audio. Shared so "is this an audio
#: file" is one answer everywhere -- a path parser that disagrees with the
#: discoverer produces books the pipeline finds but cannot name.
#:
#: ``.m4b`` is here because a source can already BE an m4b; it does not mean
#: the file is finished, which is discovery's job to decide.
AUDIO_SUFFIXES = frozenset({
    ".mp3",
    ".m4a",
    ".m4b",
    ".flac",
    ".ogg",
    ".opus",
    ".wav",
    ".aac",
    ".wma",
})

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: Runs of underscores collapse. Three illegal characters in a row should not
#: produce three underscores -- that is noise the reader has to look past.
_UNDERSCORE_RUN = re.compile(r"_{2,}")

#: Leading dots hide a file on Unix, and leading underscores are what our own
#: sanitizing produces from a leading illegal character. Neither belongs at
#: the front of a book title.
_LEADING_JUNK = re.compile(r"^[._]+")

#: Trailing dots and spaces are silently stripped by Windows and SMB, so a
#: name ending in one round-trips differently depending on who reads it.
_TRAILING_JUNK = re.compile(r"[.\s]+$")

#: Whitespace runs, including the newlines and tabs a scraped title carries.
_WHITESPACE_RUN = re.compile(r"\s+")

#: Bytes, not characters. Most filesystems cap a single path COMPONENT here,
#: and truncating by character count overshoots on any non-ASCII title.
MAX_COMPONENT_BYTES = 255

#: Names Windows reserves for character devices. They cannot be used as a
#: filename there NO MATTER THE EXTENSION -- "CON.m4b" fails exactly as "CON"
#: does -- and the resulting OSError names neither the book nor the reason.
#:
#: The reservation applies to the stem alone, so "Contact.m4b" and "COM10.m4b"
#: are ordinary names and must be left alone. Compared casefolded because the
#: reservation is case-insensitive.
_WINDOWS_RESERVED = frozenset({
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{digit}" for digit in range(1, 10)),
    *(f"lpt{digit}" for digit in range(1, 10)),
})


def _truncate_to_bytes(value: str, limit: int) -> str:
    """Trim a string so its UTF-8 encoding fits within ``limit`` bytes.

    Truncates at a character boundary, never mid-codepoint: a filename ending
    in half a multi-byte character is invalid UTF-8, and the failure surfaces
    at whatever tries to read the directory rather than here.

    Args:
        value: The string to trim.
        limit: Maximum size in bytes.

    Returns:
        The longest prefix of ``value`` that encodes within ``limit`` bytes.
    """
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value

    return encoded[:limit].decode("utf-8", errors="ignore")


def sanitize_filename(filename: str, *, max_bytes: int = MAX_COMPONENT_BYTES) -> str:
    r"""Make one path component safe on every filesystem in the chain.

    Preserves an extension when one is present -- truncating ``book.m4b`` to
    ``boo`` would produce a file nothing recognizes as audio.

    Args:
        filename: Raw name, possibly containing illegal characters.
        max_bytes: Component size limit in bytes.

    Returns:
        A name safe to write. Never empty: an input that sanitizes to nothing
        returns ``untitled``, because an empty component silently collapses the
        path rather than raising.

    Example:
        >>> sanitize_filename('a/b\\\\c:"d')
        'a_b_c_d'
        >>> sanitize_filename('..hidden')
        'hidden'
    """
    # NFC first. macOS stores decomposed Unicode, so the same title arrives as
    # different byte sequences depending on where it was typed, and two books
    # that look identical end up in two directories.
    cleaned = unicodedata.normalize("NFC", filename)

    cleaned = _ILLEGAL_CHARS.sub("_", cleaned)
    cleaned = _UNDERSCORE_RUN.sub("_", cleaned)
    cleaned = _LEADING_JUNK.sub("", cleaned)
    cleaned = _TRAILING_JUNK.sub("", cleaned)

    if not cleaned:
        return "untitled"

    # Split the extension before truncating so a long name keeps its suffix.
    stem, dot, suffix = cleaned.rpartition(".")
    if dot and suffix and len(suffix) <= 5 and stem:
        room = max_bytes - len(f".{suffix}".encode())
        return f"{_escape_reserved(_truncate_to_bytes(stem, max(room, 1)))}.{suffix}"

    return _escape_reserved(_truncate_to_bytes(cleaned, max_bytes))


def _escape_reserved(stem: str) -> str:
    """Prefix a Windows device name so it can be used as a filename.

    Args:
        stem: A filename component with its extension already removed.

    Returns:
        The stem, prefixed with an underscore when Windows reserves it. The
        prefix is chosen over dropping or renaming because it keeps the title
        readable and reversible: a book called "Con" stays recognisable.
    """
    return f"_{stem}" if stem.casefold() in _WINDOWS_RESERVED else stem


def sanitize_chapter_title(title: str) -> str:
    """Clean a chapter title for embedding in a chapter table.

    Different rules from a filename, and deliberately so: this string is never
    written to disk as a name, so illegal characters become SPACES rather than
    underscores. ``Chapter: One`` reads correctly as ``Chapter One``; as
    ``Chapter_ One`` it reads like an error.

    Args:
        title: Raw chapter title from a probe or an API.

    Returns:
        A cleaned title. Never empty -- an input that cleans to nothing
        returns ``Chapter``, because a player showing a blank entry is worse
        than one showing a generic label.

    Example:
        >>> sanitize_chapter_title('Chapter: One')
        'Chapter One'
    """
    cleaned = unicodedata.normalize("NFC", title)
    cleaned = _ILLEGAL_CHARS.sub(" ", cleaned)
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned).strip()
    return cleaned or "Chapter"
