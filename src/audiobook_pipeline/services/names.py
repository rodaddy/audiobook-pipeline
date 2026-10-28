"""Judging what a directory name IS -- an author, a series, or junk.

Purpose:
    The small decisions the path parser leans on. Kept separate because they
    are the part worth reading on their own: every one of them encodes a real
    misfiling that happened, and the reasons matter more than the regexes.

WHY AN AUTHOR IS RECOGNISED BY EXCLUSION
    There is no positive test for "is a person's name". What there IS is a
    reliable list of things a person's name is NOT: it has no digits, it is not
    "The Coldfire Trilogy", and it is not "Done" or "tFiles" or "Volumes".
    Walking a path upward and taking the first ancestor that survives all of
    those finds the author in every real layout tested, and refuses rather
    than guessing when none does.

    Every ceiling is measured PER CREDIT, not per folder. Checked against the
    129 author folders in the live library on 2026-08-02: 126 are accepted,
    and the three refused -- "Dragonlance", "Dragonlance Saga", "The Art of
    War" -- are franchise folders rather than people, which is correct.

    Refusing matters more than matching. A wrong author becomes a permanent
    folder in the library that cannot be told apart from a real one later,
    while an empty author is honest and sweepable.

Example:
    >>> looks_like_author("Brian McClellan")
    True
    >>> looks_like_author("The Coldfire Trilogy")
    False

See Also:
    - audiobook_pipeline.services.parse: the patterns that use these
"""

from __future__ import annotations

import re

from loguru import logger

from audiobook_pipeline.utils.text import (
    collapse_whitespace,
    fold_accents,
    has_digit,
    strip_brackets,
    strip_punctuation,
)

log = logger.bind(stage="names")

#: Words that mean a directory groups books rather than naming a person. Split
#: into two ideas that happen to share a test: words describing a COLLECTION
#: ("trilogy", "series"), and the names of pipeline/staging folders ("input",
#: "processing") that sit above real libraries and would otherwise be adopted
#: as authors when a book is converted straight out of one.
_NOT_AN_AUTHOR = frozenset({
    "trilogy",
    "series",
    "saga",
    "collection",
    "volumes",
    "books",
    "chronicle",
    "chronicles",
    "standalones",
    "chaptered",
    "audiobook",
    "stuff",
    "random",
    "newbooks",
    "output",
    "input",
    "incoming",
    "processing",
    "completed",
    "failed",
    "queue",
    "pipeline",
})

#: The most words ONE person's name plausibly has, counted per credit rather
#: than per folder. Measured against the live library 2026-08-02: real author
#: folders run to NINE words -- "Brandon Sanderson, Mary Robinette Kowal, Dan
#: Wells, Howard Tayler" and "J. R. R. Tolkien, Christopher Tolkien - editor".
#: A per-folder ceiling of five would have rejected eight real authors.
#:
#: Five per credit still catches a title wearing an author's coat, because a
#: title does not carry a comma between two plausible names.
_MAX_WORDS_PER_CREDIT = 5

#: Separates co-authors within one folder name.
_CREDIT_SEPARATORS = (",", "&")

#: Role suffixes attached to a credit: "Tracy Hickman - editor".
_ROLE_SUFFIXES = frozenset({"editor", "translator", "narrator", "foreword"})

#: The longest ONE credit may be, again per person rather than per folder.
#: "Brandon Sanderson, Mary Robinette Kowal, Dan Wells, Howard Tayler" is 64
#: characters and four real authors, so a per-folder ceiling rejected it.
_MAX_CHARS_PER_CREDIT = 40

#: A trailing pipeline hash, e.g. " - a7edd490030561fb". Written by the
#: pipeline's own work directories, so it appears on paths this parses.
_HASH_SUFFIX = re.compile(r"\s+-\s+[a-f0-9]{16}$")

#: Label suffixes that describe the FORMAT rather than the work.
_LABEL_SUFFIX = re.compile(
    r"\s+-\s+(?:Audiobook|Audio|Unabridged|Abridged)$", re.IGNORECASE
)

#: A single letter followed by another single letter: the "r a" of "r a
#: salvatore" once the periods are gone. Joining these is what makes an
#: initialled name match the same name spelled without spaces.
_INITIAL_RUN = re.compile(r"\b([a-z])\s+(?=[a-z]\b)")


def strip_hash(name: str) -> str:
    """Remove a trailing pipeline hash from a directory name.

    Args:
        name: A raw directory or file name.

    Returns:
        The name without the hash suffix.
    """
    return _HASH_SUFFIX.sub("", name)


def strip_label_suffix(name: str) -> str:
    """Remove a trailing format label such as "- Unabridged".

    Args:
        name: A raw directory name.

    Returns:
        The name without the label.
    """
    return _LABEL_SUFFIX.sub("", name)


def clean_collection_suffix(name: str) -> str:
    """Remove bracketed and parenthesised group markers.

    Args:
        name: A series or collection folder name, e.g. "Temeraire [1-5]".

    Returns:
        The name without them, e.g. "Temeraire".
    """
    return strip_brackets(name)


def normalize_author(name: str) -> str:
    """Reduce an author name to a key two libraries can be compared on.

    The same person is spelled several ways across two libraries: "R.A.
    Salvatore" and "R A Salvatore", "Weis & Hickman" and "Weis and Hickman".
    All of them must reduce to one key or the same book reads as missing.

    Args:
        name: An author folder name.

    Returns:
        A lowercase key with punctuation, accents, and initial spacing gone.

    Example:
        >>> normalize_author("R.A. Salvatore")
        'ra salvatore'
        >>> normalize_author("Weis & Hickman") == normalize_author("Weis and Hickman")
        True
    """
    folded = fold_accents(name).lower().replace("&", "and")
    collapsed = strip_punctuation(folded)
    # "r a salvatore" -> "ra salvatore": join runs of single letters, so an
    # initialled name matches the same name written without spaces.
    return collapse_whitespace(_INITIAL_RUN.sub(r"\1", collapsed))


def extract_author(name: str) -> str:
    """Pull the author out of a directory name that may carry more.

    Real folders arrive as "R.A. Salvatore - The Legend of Drizzt" or
    "Tad Williams (All Chaptered)". Both name an author; only the left-hand
    side of the first is one.

    Args:
        name: A directory name.

    Returns:
        The candidate author. Still has to pass ``looks_like_author`` -- this
        only isolates the part worth judging.
    """
    cleaned = strip_brackets(name)

    if " - " in cleaned:
        candidate = cleaned.split(" - ", 1)[0].strip()
        # A digit on the left means the split landed inside a series marker
        # ("Powder Mage 01 - Promise of Blood"), not between author and series.
        if not has_digit(candidate):
            log.debug("extract_author({!r}) -> {!r} via dash split", name, candidate)
            return candidate

    result = cleaned or name
    log.debug("extract_author({!r}) -> {!r}", name, result)
    return result


def looks_like_author(name: str) -> bool:
    """Whether a directory name plausibly names a person.

    Args:
        name: A candidate, normally from ``extract_author``.

    Returns:
        True only when nothing disqualifies it. Every clause below is a real
        misfiling: a collection word filed "The Coldfire Trilogy" as an author,
        a digit filed "Powder Mage 01", an article filed "The Martian", and a
        single word filed "Dragonlance" and "Noobtown" -- which are franchises,
        not people.
    """
    verdict = _rejection_reason(name)
    if verdict:
        log.debug("looks_like_author({!r}) -> False ({})", name, verdict)
        return False

    log.debug("looks_like_author({!r}) -> True", name)
    return True


def _rejection_reason(name: str) -> str:
    """Why ``name`` cannot be an author, if it cannot.

    Split out so the REASON is a value rather than a comment on a return.
    When a book lands under "Unknown Author", the debug log names the clause
    that rejected it instead of leaving five candidate explanations.

    Args:
        name: A candidate author.

    Returns:
        A short reason, or "" when the name survives every check.
    """
    lower = name.lower()
    people = split_credits(name)

    matched = next((word for word in _NOT_AN_AUTHOR if word in lower), "")
    if matched:
        return f"collection word: {matched}"
    if has_digit(name):
        return "contains a digit"
    longest_credit = max((len(person) for person in people), default=0)
    if longest_credit > _MAX_CHARS_PER_CREDIT:
        return f"a credit of {longest_credit} characters is not a name"

    longest = max((len(person.split()) for person in people), default=0)
    if longest > _MAX_WORDS_PER_CREDIT:
        return f"a credit of {longest} words is a title, not a name"
    if len(name.split()) == 1:
        return "a single word is a franchise, not a person"
    if lower.startswith(("the ", "a ", "an ")):
        return "starts with an article"
    return ""


def split_credits(name: str) -> list[str]:
    """Split a folder name into the individual people it credits.

    Args:
        name: An author folder name, e.g. "J. R. R. Tolkien, Christopher
            Tolkien - editor".

    Returns:
        One entry per person, with role suffixes removed. A name crediting
        nobody in particular comes back as a single entry, so callers never
        special-case the common one-author spelling.
    """
    parts = [name]
    for separator in _CREDIT_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(separator)]

    return [stripped for part in parts if (stripped := _strip_role(part.strip()))]


def _strip_role(credit: str) -> str:
    """Remove a trailing role from one credit.

    Args:
        credit: A single person, e.g. "Tracy Hickman - editor".

    Returns:
        The person's name alone.
    """
    person, separator, role = credit.rpartition(" - ")
    if separator and role.strip().lower() in _ROLE_SUFFIXES:
        return person.strip()
    return credit
