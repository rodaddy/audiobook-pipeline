r"""Reducing two spellings of one book to a single comparable key.

Purpose:
    A book in the source library and the same book in the target library are
    almost never spelled identically. This module turns either spelling into
    one key, so "is this book already converted?" is a set lookup rather than
    a judgement call.

WHY THIS IS COMPOSED RATHER THAN WRITTEN
    The version this replaces was a single 30-line function stacking sixteen
    regex substitutions, several naming individual authors and series --
    ``tolkien``, ``dragonlance``, ``the \\w+ saga``. That shape cannot be
    tested a rule at a time, and every new library spelling added another
    line to the pile.

    Here each rule is a named helper in ``utils.text``, tested on its own,
    and ``normalize_title`` is the order they compose in. A new spelling adds
    a helper and one line, and the helper is reusable everywhere else.

Pattern/Convention:
    Callers ask for a key and compare keys::

        >>> normalize_title("Book 1 - Promise of Blood (Unabridged)")
        'promise of blood'

Example:
    >>> normalize_title("Promise of Blood Part 1 of 19") == normalize_title(
    ...     "Promise of Blood"
    ... )
    True

See Also:
    - audiobook_pipeline.services.names: the same idea for author names
    - audiobook_pipeline.utils.text: the individual rules composed here
"""

from __future__ import annotations

import re
from collections.abc import Callable

from loguru import logger
from rapidfuzz import fuzz

from audiobook_pipeline.utils.text import (
    collapse_whitespace,
    fold_accents,
    strip_asin,
    strip_brackets,
    strip_edition,
    strip_numbered_prefix,
    strip_part_suffix,
    strip_punctuation,
)

log = logger.bind(stage="matching")

#: How alike two titles must read before they count as one book. 85 is the
#: legacy threshold, kept because it was tuned against this library rather
#: than chosen: it accepts "The Final Empire" against "Mistborn: The Final
#: Empire" while refusing "Exile" against "Exodus".
FUZZY_THRESHOLD = 85

#: A whole name that is only a position: "part 1 of 3", "disc 2", "cd 01".
#: Matched against the NORMALIZED key, so punctuation is already gone.
_MARKER_ONLY = re.compile(r"(?:part|disc|disk|cd|chapter|ch)\s*\d+(?:\s*of\s*\d+)?")

#: The rules that turn a filename into a title key, in the order they apply.
#:
#: ORDER MATTERS and is not arbitrary. The edition word must go before the
#: part suffix ("...(Unabridged) Part 01 of 19" hides the part marker behind
#: brackets otherwise), and the numbered PREFIX must go after the part
#: SUFFIX, so "01-19" is read as a part range and not as a leading ordinal.
_TITLE_RULES: tuple[Callable[[str], str], ...] = (
    strip_asin,
    strip_edition,
    strip_brackets,
    strip_part_suffix,
    strip_numbered_prefix,
    fold_accents,
    strip_punctuation,
)


def normalize_title(title: str) -> str:
    """Reduce a title to the key another spelling of it also reduces to.

    Args:
        title: A filename stem or folder name.

    Returns:
        A lowercase key, or "" when nothing survives -- an empty key never
        matches, which is the safe answer for a file named only "01".

    Example:
        >>> normalize_title("Book 1 - Promise of Blood (Unabridged)")
        'promise of blood'
    """
    key = title.lower()
    for rule in _TITLE_RULES:
        key = rule(key)
    key = collapse_whitespace(key)
    # What survives may still be no title at all: a bare chapter number, or a
    # part marker that was the WHOLE name rather than a suffix on one.
    # ``strip_part_suffix`` deliberately anchors to the end of the string, so
    # a file called only "Part 1 of 3" keeps its text and arrives here intact.
    #
    # Returning such a key would let "01.mp3" match every other "01" in the
    # target and report an unconverted book as already converted -- the exact
    # failure this tool exists to catch.
    return "" if _is_only_a_marker(key) else key


def _is_only_a_marker(key: str) -> bool:
    """Whether a normalized key is a position rather than a title."""
    return not key or key.isdigit() or _MARKER_ONLY.fullmatch(key) is not None


def titles_match(left: str, right: str, *, threshold: int = FUZZY_THRESHOLD) -> bool:
    """Whether two ALREADY NORMALIZED title keys name the same book.

    Uses a token set ratio rather than a plain ratio so that one side
    carrying extra series or subtitle words still matches; that is the usual
    difference between a source filename and a finished library name.

    Args:
        left: A normalized title key.
        right: Another normalized title key.
        threshold: Minimum similarity, 0-100.

    Returns:
        True when the two are the same book. An empty key matches nothing,
        including another empty key -- two unnamed files are not evidence of
        anything.
    """
    if not left or not right:
        return False
    if left == right:
        return True
    return bool(fuzz.token_set_ratio(left, right) >= threshold)
