"""The text operations the rest of the application shares.

Purpose:
    One named function per string transformation, so a caller asks for what it
    WANTS ("strip the subtitle") rather than carrying a regex that encodes how.
    Every function here is small, pure, and usable from any layer.

WHY THESE LIVE TOGETHER INSTEAD OF WHERE THEY ARE USED
    They were scattered, and the scattering caused real divergence. Measured
    2026-08-02 across the rewritten tree: 25 compiled regexes in 8 modules,
    with ``_WHITESPACE`` defined identically in two of them and ``_SUBTITLE``
    defined DIFFERENTLY in two -- ``identify`` treated an en-dash as a subtitle
    separator and ``organize`` did not, so the same book could be identified
    under one title and filed under another.

    A shared helper cannot drift from itself. When the rule for subtitles
    changes, it changes once.

Pattern/Convention:
    Callers import the function, never the pattern::

        >>> strip_subtitle("Forsworn: A Powder Mage Novella")
        'Forsworn'

Example:
    >>> collapse_whitespace("  The   Name  of the Wind ")
    'The Name of the Wind'
    >>> fold_accents("Húrin")
    'Hurin'

See Also:
    - audiobook_pipeline.utils.paths: turning text into a safe FILENAME
"""

from __future__ import annotations

import re
import unicodedata
from html import unescape

#: Any run of whitespace, including the newlines and tabs that arrive from
#: catalogue summaries.
_WHITESPACE_RUN = re.compile(r"\s+")

#: An edition subtitle: a colon or en-dash with text on BOTH sides. Audible
#: appends these ("Forsworn: A Powder Mage Novella") and the library does not
#: carry them. Requiring text on the left keeps a title that merely OPENS with
#: a colon from being erased entirely.
#:
#: EN_DASH is defined as an escape rather than written literally: it is
#: visually identical to the ASCII hyphen beside it, and Audible really does
#: use both spellings.
EN_DASH = "\u2013"

_SUBTITLE = re.compile(rf"(?<=\S)\s*[:{EN_DASH}]\s+\S.*$")

#: The same, plus a spaced ASCII hyphen. SEPARATE from ``_SUBTITLE`` because
#: the two are used for different jobs and must not be merged: matching is
#: allowed to be greedy, since a wrong guess only costs a scoring point, while
#: NAMING is not -- "Exile - Book Two of the Dark Elf Trilogy" must lose its
#: tail when scored against "Exile", but a book genuinely titled "Something -
#: Something" must keep its name on disk.
_SUBTITLE_OR_DASH = re.compile(rf"(?<=\S)\s*[:{EN_DASH}-]\s+\S.*$")

#: A four-digit year in parentheses, as folder names carry it: "(2014)".
_PARENTHESISED_YEAR = re.compile(r"\s*\(\d{4}\)")

#: Anything parenthesised or bracketed, with its leading space.
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")
_BRACKETED = re.compile(r"\s*\[[^\]]*\]")

#: Everything that is not a word character or a space.
_PUNCTUATION = re.compile(r"[^\w\s]")

#: A single digit anywhere. A person's name has none.
_DIGIT = re.compile(r"\d")

#: An HTML tag, as catalogue summaries arrive full of them.
_TAG = re.compile(r"<[^>]+>")


def collapse_whitespace(value: str) -> str:
    """Reduce every whitespace run to one space and trim the ends.

    Args:
        value: Any string.

    Returns:
        The string with normalised spacing.
    """
    return _WHITESPACE_RUN.sub(" ", value).strip()


def strip_subtitle(value: str) -> str:
    """Remove an edition subtitle.

    Args:
        value: A title, possibly "Title: Subtitle".

    Returns:
        The part before the separator, or the original when removing it would
        leave nothing -- an unnamed book is worse than a long name.
    """
    return _SUBTITLE.sub("", value).strip() or value


def strip_subtitle_or_dash(value: str) -> str:
    """Remove a subtitle, treating a spaced hyphen as a separator too.

    For MATCHING only. A greedy split costs at most a scoring point here,
    whereas the same greed applied to a filename would rename a book whose
    real title contains a dash.

    Args:
        value: A candidate title from the catalogue.

    Returns:
        The part before the separator, or the original when nothing is left.
    """
    return _SUBTITLE_OR_DASH.sub("", value).strip() or value


def strip_year(value: str) -> str:
    """Remove a parenthesised year.

    Args:
        value: A title or folder name, e.g. "Food: A Love Story (2014)".

    Returns:
        The name without the year. The year belongs in the tags, where an
        edition change cannot turn one book into two folders.
    """
    return _PARENTHESISED_YEAR.sub("", value).strip()


def strip_brackets(value: str) -> str:
    """Remove parenthesised and bracketed asides.

    Args:
        value: A name, e.g. "Tad Williams (All Chaptered)" or "Temeraire [1-5]".

    Returns:
        The name without them.
    """
    return _BRACKETED.sub("", _PARENTHETICAL.sub("", value)).strip()


def strip_punctuation(value: str) -> str:
    """Remove every non-word, non-space character.

    Args:
        value: Any string.

    Returns:
        The string with punctuation gone and spacing collapsed. Used for
        COMPARISON only -- it destroys a name for display purposes.
    """
    return collapse_whitespace(_PUNCTUATION.sub("", value))


def strip_html(value: str) -> str:
    """Remove HTML tags and unescape entities.

    Args:
        value: A catalogue summary.

    Returns:
        Plain text with normalised spacing.
    """
    return collapse_whitespace(unescape(_TAG.sub("", value)))


def fold_accents(value: str) -> str:
    """Reduce accented characters to their base letters.

    Decomposes to NFD and drops the combining marks. This also settles macOS's
    decomposed storage, where the same title compares unequal to itself
    depending on where it was typed -- "The Children of Húrin" is one book, and
    the real library holds it under two spellings.

    Args:
        value: Any string.

    Returns:
        The string with accents removed.
    """
    decomposed = unicodedata.normalize("NFD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def has_digit(value: str) -> bool:
    """Whether the string contains any digit.

    Args:
        value: Any string.

    Returns:
        True when a digit is present. A person's name has none, which is what
        separates "Brian McClellan" from "Powder Mage 01".
    """
    return _DIGIT.search(value) is not None
