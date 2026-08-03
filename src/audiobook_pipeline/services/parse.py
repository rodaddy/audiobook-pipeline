"""Reading a source path into an author, series, position, and title.

Purpose:
    Composes the pattern functions. Tries the layouts strongest first against
    the levels of the path that carry information, and merges what each one
    finds. Knows the ORDER; the patterns themselves know the layouts.

WHY THE SOURCE TREE IS WORTH READING AT ALL
    The catalogue is better than a path when it answers, and it does not
    always answer -- Audible genuinely does not carry every novella. Measured
    on the 2026-08-02 live run, 2 of the first 6 books came back with no match
    and were filed under "Unknown Author" while the author's name sat one
    directory above them.

WHY THE PATH NEVER OVERRULES THE CATALOGUE
    A path says what somebody's folders are called; the catalogue says what the
    book IS. Everything here is a FALLBACK and a search hint, which is also why
    refusing to answer is a good outcome: a guessed author becomes a permanent
    library folder that cannot be told from a real one later, while an empty
    author is honest and easy to sweep.

Example:
    >>> from pathlib import Path
    >>> root = Path("/src")
    >>> parse_path(root / "C S Friedman/Coldfire/01 - Black Sun Rising", root).author
    'C S Friedman'

See Also:
    - audiobook_pipeline.services.patterns: the layouts this tries
    - audiobook_pipeline.services.names: the author heuristics
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.services import patterns
from audiobook_pipeline.services.names import strip_hash, strip_label_suffix
from audiobook_pipeline.utils.paths import AUDIO_SUFFIXES
from audiobook_pipeline.utils.text import strip_subtitle, strip_year

log = logger.bind(stage="parse")

#: Layouts that name a BOOK, tried against the folder or file that is the book.
#: Ordered strongest first: an explicit "#N" marker beats a dashed number,
#: which beats a bare number, because each is less able to be a coincidence.
_BOOK_PATTERNS = (
    patterns.hash_marked,
    patterns.numbered_dash,
    patterns.bracket_position,
    patterns.numbered_bare,
)

#: Layouts that name an AUTHOR, tried against each ancestor in turn.
_AUTHOR_PATTERNS = (patterns.author_dash_series, patterns.author_only)

#: How far above a book to look for an author. Three levels covers
#: Author/Series/Book, which is the deepest real layout measured. Bounded so a
#: climb cannot wander up to "Volumes" and adopt it.
_MAX_CLIMB = 3

#: Names that mean "this folder is a filename, not a title".
_GENERIC = frozenset({"file", "audio", "audiobook", "mp3", "m4b", "track", "untitled"})


def parse_path(source: Path, root: Path) -> ParsedPath:
    """Read everything a source path is willing to say about a book.

    Args:
        source: The book -- a directory for a multi-file book, the file
            itself otherwise.
        root: The directory the run was pointed at. Bounds the upward walk so
            an ancestor outside the run can never supply an author.

    Returns:
        What the path claims. Any field may be empty; empty is the correct
        answer for a loose file with no folders around it.
    """
    book = _book_name(source)
    result = _first_match(book, _BOOK_PATTERNS)
    log.debug("book name {!r} -> {}", book, result.model_dump())

    result = result.merge(_walk_for_author(source, root))

    if not result.title:
        result = result.merge(ParsedPath(title=_clean_title(book)))

    result = _drop_author_echoing_series(result)
    log.debug("parse_path({}) -> {}", source.name, result.model_dump())
    return result


def _book_name(source: Path) -> str:
    """The name that identifies the book itself.

    Args:
        source: A book directory or an audio file.

    Returns:
        The directory name, or the file's stem. When that name is generic --
        "file.m4b", "audio.mp3" -- the PARENT names the book instead, because
        a filename the ripper chose says nothing.

    A suffix is only dropped when it is an AUDIO extension. Testing for any
    suffix read the tail of "Powder Mage 0.2 - Servant of the Crown" as one and
    handed back "Powder Mage 0", collapsing three novellas onto one name.
    Checked against the known extensions rather than the filesystem, so this
    stays a pure function of the path.
    """
    is_audio = source.suffix.lower() in AUDIO_SUFFIXES
    name = strip_hash(source.stem if is_audio else source.name)

    if name.lower() in _GENERIC:
        parent = strip_hash(source.parent.name)
        log.debug("generic name {!r}; using parent {!r}", name, parent)
        return strip_label_suffix(parent)

    return strip_label_suffix(name)


def _first_match(
    name: str, candidates: tuple[Callable[[str], ParsedPath], ...]
) -> ParsedPath:
    """Try each pattern in order and take the first that recognises the name.

    Args:
        name: The name to read.
        candidates: Patterns, strongest first.

    Returns:
        The first non-empty result, or an empty one.
    """
    for pattern in candidates:
        found = pattern(name)
        if not found.is_empty:
            log.debug("{} matched {!r}", pattern.__name__, name)
            return found
    return ParsedPath()


def _walk_for_author(source: Path, root: Path) -> ParsedPath:
    """Climb from the book toward the root looking for an author.

    Walks rather than checking a fixed depth, because the author sits at a
    different level in every real layout: directly above the book in
    ``Author/Book``, two above in ``Author/Series/Book``. Measured across 35
    source books, no fixed depth was right for more than half of them.

    Args:
        source: The book.
        root: The bound. The walk stops here, so a run pointed straight at a
            book folder finds no author rather than adopting a Downloads
            directory.

    Returns:
        The author, and the series when one sits between author and book.
    """
    if not source.is_relative_to(root):
        return ParsedPath()

    ancestors = list(source.parents)[:_MAX_CLIMB]
    for depth, ancestor in enumerate(ancestors):
        if ancestor == root or not ancestor.is_relative_to(root):
            break

        found = _first_match(strip_hash(ancestor.name), _AUTHOR_PATTERNS)
        if found.is_empty:
            continue

        # Anything BETWEEN the author and the book names the series.
        if not found.series and depth > 0:
            found = found.merge(
                ParsedPath(series=strip_hash(ancestors[depth - 1].name))
            )

        log.debug("author {!r} found at {!r}", found.author, ancestor.name)
        return found

    log.debug("no ancestor of {!r} names an author", source.name)
    return ParsedPath()


def _clean_title(name: str) -> str:
    """Last-resort title, when no pattern claimed one.

    Args:
        name: The book's own name.

    Returns:
        The name with a year and any subtitle removed.
    """
    return strip_subtitle(strip_year(name))


def _drop_author_echoing_series(result: ParsedPath) -> ParsedPath:
    """Clear an author that is really just the series name again.

    A folder like "Dragonlance/Dragonlance Saga" yields the same string for
    both. Keeping it would file a franchise as a person.

    Args:
        result: A parsed path.

    Returns:
        The result, with the author cleared when it duplicates the series.
    """
    if result.author and result.author.lower() == result.series.lower():
        log.debug("clearing author {!r}: it repeats the series", result.author)
        return result.model_copy(update={"author": ""})
    return result
