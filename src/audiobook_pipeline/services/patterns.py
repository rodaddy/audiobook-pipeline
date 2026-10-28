"""One function per naming convention a source folder might follow.

Purpose:
    Each function reads ONE layout and returns what it found. They share a
    signature, know nothing about each other, and are tried in order by
    ``parse``. Adding a new convention means adding a function and listing it,
    not editing a branching monolith.

WHY EACH PATTERN IS ITS OWN FUNCTION
    These grew from real folders, and they will keep growing. As one function
    with seven branches, every new layout risked the six before it, and a
    failing case could only be reproduced by constructing a whole path. As
    separate functions each is a two-line test.

Pattern/Convention:
    Every pattern takes the name to read and returns a ParsedPath, empty when
    the name is not its layout::

        >>> numbered_dash("Deathgate Cycle 1 - Dragon Wing").position
        '1'

See Also:
    - audiobook_pipeline.models.parsed: the shape they all return
    - audiobook_pipeline.services.names: the author heuristics they lean on
"""

from __future__ import annotations

import re

from loguru import logger

from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.services.names import (
    clean_collection_suffix,
    extract_author,
    looks_like_author,
)

log = logger.bind(stage="patterns")

#: "Author-Series-#N-Title". The explicit marker makes this the most
#: trustworthy layout, so it is tried first.
_HASH_MARKER = re.compile(r"-#(\d+)-")

#: "Deathgate Cycle 1 - Dragon Wing" -- a dash SEPARATES the position from the
#: title, so the split point is unambiguous.
_NUMBERED_DASH = re.compile(r"^(.+?)\s+(\d{1,3}(?:\.\d+)?)\s+-\s+(.+)$")

#: "The First Law 04 Best Served Cold" -- no dash, so the number is the only
#: boundary. Weaker than the dashed form and tried after it.
_NUMBERED_BARE = re.compile(r"^(.+?)\s+(\d{1,3}(?:\.\d+)?)\s+(.+)$")

#: "Mistborn [01] The Final Empire".
_BRACKET_POSITION = re.compile(r"^(.+?)\s+\[(\d+)\]\s+(.+)$")

#: The shortest run of characters that can be a real title. Guards the bare
#: numbered form from splitting "Book 3" into a series and a title of "3".
_MIN_TITLE = 3


def hash_marked(name: str) -> ParsedPath:
    """Read "Author-Series-#N-Title".

    Args:
        name: A directory name.

    Returns:
        Everything the marker delimits, or an empty result. The LAST marker is
        used, so a nested subseries resolves to the innermost position.
    """
    normalized = re.sub(r"-#-(\d+)", r"-#\1", name)
    normalized = re.sub(r"-#(\d+) ", r"-#\1-", normalized)

    markers = list(_HASH_MARKER.finditer(normalized))
    if not markers:
        return ParsedPath()

    last = markers[-1]
    prefix = normalized[: last.start()]
    author, _, series = prefix.partition("-")

    return ParsedPath(
        author=author.strip(),
        title=normalized[last.end() :].strip(),
        series=_HASH_MARKER.split(series)[0].strip(),
        position=last.group(1),
    )


def numbered_dash(name: str) -> ParsedPath:
    """Read "Series N - Title".

    Args:
        name: A directory name.

    Returns:
        Series, position, and title, or an empty result.
    """
    match = _NUMBERED_DASH.match(name)
    if not match:
        return ParsedPath()

    return ParsedPath(
        series=match.group(1).strip(),
        position=match.group(2),
        title=match.group(3).strip(),
    )


def bracket_position(name: str) -> ParsedPath:
    """Read "Series [NN] Title".

    Args:
        name: A directory name.

    Returns:
        Series, position, and title, or an empty result.
    """
    match = _BRACKET_POSITION.match(name)
    if not match:
        return ParsedPath()

    return ParsedPath(
        series=match.group(1).strip(),
        position=match.group(2),
        title=match.group(3).strip(),
    )


def numbered_bare(name: str) -> ParsedPath:
    """Read "Series NN Title", with no dash to separate them.

    Args:
        name: A directory name.

    Returns:
        Series, position, and title, or an empty result when the trailing part
        is too short to be a title -- which is what stops "Book 3" from
        parsing as a series called "Book" holding a title called "3".
    """
    match = _NUMBERED_BARE.match(name)
    if not match or len(match.group(3).strip()) < _MIN_TITLE:
        return ParsedPath()

    return ParsedPath(
        series=match.group(1).strip(),
        position=match.group(2),
        title=match.group(3).strip(),
    )


def author_dash_series(name: str) -> ParsedPath:
    """Read "Author - Series" from a directory that holds books.

    Args:
        name: A directory name, normally a grandparent.

    Returns:
        Author and series, or an empty result when the left side is not
        plausibly a person.
    """
    if " - " not in name:
        return ParsedPath()

    author, _, series = name.partition(" - ")
    if not looks_like_author(author.strip()):
        return ParsedPath()

    return ParsedPath(
        author=author.strip(), series=clean_collection_suffix(series.strip())
    )


def author_only(name: str) -> ParsedPath:
    """Read a directory that names nothing but an author.

    Args:
        name: A directory name.

    Returns:
        The author, or an empty result. This is the pattern that walks up a
        tree looking for "Brian McClellan" among "Done" and "tFiles".
    """
    candidate = extract_author(name)
    return (
        ParsedPath(author=candidate) if looks_like_author(candidate) else ParsedPath()
    )
