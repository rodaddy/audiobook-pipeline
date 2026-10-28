"""What a source path claims a book is, before anything verifies it.

Purpose:
    One shape, returned by every path pattern. Patterns can then be tried in
    order and composed without any of them knowing about the others.

WHY THE PATTERNS ALL RETURN THE SAME TYPE
    Each pattern answers a partial question: one finds a position, another an
    author, another only a title. Sharing a return type lets a later pattern
    fill a field an earlier one left empty (``merge``) without a chain of
    special cases, and lets any pattern be tested completely on its own.

Example:
    >>> ParsedPath(title="Homeland").merge(ParsedPath(author="R.A. Salvatore"))
    ParsedPath(author='R.A. Salvatore', title='Homeland', series='', position='')

See Also:
    - audiobook_pipeline.services.parse: the patterns that produce these
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ParsedPath(BaseModel):
    """The author, title, series, and position a path suggests.

    Every field defaults to empty rather than None: "not found" is the normal
    outcome for most fields on most paths, and an empty string is the value a
    caller can pass straight to a search or a folder name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    author: str = ""
    title: str = ""
    series: str = ""
    position: str = ""

    @property
    def is_empty(self) -> bool:
        """True when the path yielded nothing at all."""
        return not (self.author or self.title or self.series or self.position)

    def merge(self, other: ParsedPath) -> ParsedPath:
        """Fill this result's empty fields from ``other``.

        Args:
            other: A result from a later, weaker pattern.

        Returns:
            A new ParsedPath. THIS one wins every field it already has --
            patterns are tried strongest first, so an earlier answer is a more
            specific answer and must not be overwritten by a general one.
        """
        return ParsedPath(
            author=self.author or other.author,
            title=self.title or other.title,
            series=self.series or other.series,
            position=self.position or other.position,
        )
