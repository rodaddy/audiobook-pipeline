"""What an API said about a book, validated at the boundary it arrives on.

Purpose:
    Two external services describe the same book in different vocabularies:
    Audible search returns titles, authors, and an ASIN; Audnexus returns a
    chapter table keyed on that ASIN. Both are third-party JSON that can change
    shape without warning, and both were previously consumed as raw dicts.

    Validating here means a payload that changed shape fails at the fetch with
    the field named, instead of surfacing as a KeyError inside the tag writer
    twenty minutes into a batch.

THE FIELD-NAME TRANSLATION IS DELIBERATE
    Audnexus speaks ``startOffsetMs``/``lengthMs``; this pipeline speaks
    ``start_ms``/``end_ms``. The alias lives on the model rather than in the
    fetch function, so the external vocabulary stops at this file. Every layer
    above it sees one chapter shape regardless of which service produced it --
    which is what lets the concat stage prefer embedded marks over a fetched
    table without caring where either came from.

Example:
    >>> payload = {"startOffsetMs": 0, "lengthMs": 30467, "title": "Opening"}
    >>> AudnexusChapter.model_validate(payload).end_ms
    30467

See Also:
    - audiobook_pipeline.models.chapter: what these convert INTO
    - _plans/python-rewrite-sequence.md: defects 5 and 8, the matching bugs
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from audiobook_pipeline.models.chapter import Chapter, ChapterSet


class InvalidCoverArtError(ValueError):
    """Raised when a cover's claimed type does not match its image bytes."""


class AudnexusChapter(BaseModel):
    """One chapter as Audnexus reports it.

    Audnexus gives a start offset and a LENGTH; this pipeline works in start
    and END. The conversion is here rather than at the call site so there is
    exactly one place that can get the arithmetic wrong.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    start_offset_ms: int = Field(alias="startOffsetMs", ge=0)
    length_ms: int = Field(alias="lengthMs", gt=0)
    title: str = Field(min_length=1)

    @property
    def end_ms(self) -> int:
        """Absolute end offset, derived from start plus length."""
        return self.start_offset_ms + self.length_ms

    def to_chapter(self) -> Chapter:
        """Convert to the pipeline's own chapter shape."""
        return Chapter(
            start_ms=self.start_offset_ms, end_ms=self.end_ms, title=self.title
        )


class AudnexusChapters(BaseModel):
    """A full chapter table fetched for one ASIN.

    ``extra="ignore"`` rather than forbid: this is a third-party payload and
    the service adding a field is not our error to raise. The fields we USE
    are validated; the rest is allowed to exist.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    chapters: tuple[AudnexusChapter, ...] = ()
    runtime_length_ms: int = Field(alias="runtimeLengthMs", default=0, ge=0)

    #: Audnexus's own confidence flag. Carried rather than acted on: a table
    #: marked inaccurate may still be the best available, and the duration
    #: check below is the guard that actually matters.
    is_accurate: bool = Field(alias="isAccurate", default=False)

    def to_chapter_set(self) -> ChapterSet:
        """Convert to the pipeline's chapter table."""
        return ChapterSet(
            chapters=tuple(chapter.to_chapter() for chapter in self.chapters),
            source="audnexus",
        )

    def matches_duration(
        self, actual_ms: int, *, tolerance_pct: float, tolerance_ms: int
    ) -> bool:
        """Whether this table plausibly belongs to audio of ``actual_ms``.

        THE WRONG-EDITION GUARD. An ASIN lookup can return the abridged
        edition, a re-recording with a different narrator, or a box set --
        all structurally valid chapter tables for a DIFFERENT recording.
        Applying one produces a book whose chapter marks drift further from the
        audio the longer it plays, with no error at any point.

        Both tolerances apply and the LARGER wins, because percentage alone
        fails at both ends: 1% of a 40-hour box set is 24 minutes of slop,
        while 1% of a 90-minute novella is 54 seconds and rejects tables that
        differ only by a publisher's intro.

        Args:
            actual_ms: Measured duration of the real audio.
            tolerance_pct: Allowed divergence as a percentage of runtime.
            tolerance_ms: Allowed divergence as a flat floor.

        Returns:
            Whether the table is close enough to be this recording's.
        """
        if not self.runtime_length_ms:
            return False
        allowed = max(self.runtime_length_ms * tolerance_pct / 100, tolerance_ms)
        return abs(self.runtime_length_ms - actual_ms) <= allowed


class AudibleResult(BaseModel):
    """One search hit from Audible.

    ``extra="ignore"``: the search API returns dozens of fields and pinning all
    of them would make an unrelated API addition fail the pipeline.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="ignore")

    asin: str = Field(min_length=1)
    title: str = Field(min_length=1)

    #: Both optional and both load-bearing. Defect 5 was that the author was
    #: never passed to the scorer, so 30% of the match weight was permanently
    #: zero and a same-titled book by the wrong author scored identically.
    authors: tuple[str, ...] = ()
    narrators: tuple[str, ...] = ()

    subtitle: str = ""
    series: str = ""
    series_position: str = ""
    release_year: int | None = None
    publisher: str = ""
    summary: str = ""
    genres: tuple[str, ...] = ()

    @property
    def primary_author(self) -> str:
        """First credited author, or empty when none was returned."""
        return self.authors[0] if self.authors else ""


class CoverArt(BaseModel):
    """Validated image bytes that may safely be embedded in an M4B.

    The fetch boundary validates the response before it reaches this model;
    repeating the magic-byte check here keeps every future caller held to the
    same JPEG/PNG-only contract.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    content_type: Literal["image/jpeg", "image/png"]
    data: bytes = Field(min_length=1)

    @model_validator(mode="after")
    def has_matching_image_magic(self) -> CoverArt:
        """Reject claimed image types whose bytes do not match their header."""
        if self.content_type == "image/jpeg" and self.data.startswith(b"\xff\xd8\xff"):
            return self
        if self.content_type == "image/png" and self.data.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            return self
        raise InvalidCoverArtError


class BookMetadata(BaseModel):
    """The resolved metadata written into a finished M4B.

    Mutable, unlike most models here: this is assembled across several stages
    -- identification fills the ASIN and title, the tag stage adds what it
    read from the source, the organize stage may correct the series. Freezing
    it would mean rebuilding the whole object at each step.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    author: str = ""
    narrator: str = ""

    #: Amazon Standard Identification Number. The join key for Audnexus, and
    #: the tag Plex's Audnexus agent matches on -- which ffmpeg silently
    #: refuses to write, so it goes through mutagen as a freeform atom.
    asin: str = ""

    series: str = ""
    series_position: str = ""
    release_year: int | None = None
    publisher: str = ""
    summary: str = ""
    copyright: str = ""
    genres: tuple[str, ...] = ()
    cover_url: str = ""

    @property
    def has_series(self) -> bool:
        """Whether this book belongs to a series."""
        return bool(self.series)

    @property
    def sort_title(self) -> str:
        """Title as it should sort within a series.

        ``Series N - Title`` for a series book, plain title otherwise. This is
        what makes a series list in reading order rather than alphabetically,
        which is the difference between a usable shelf and a jumbled one.
        """
        if self.series and self.series_position:
            return f"{self.series} {self.series_position} - {self.title}"
        return self.title
