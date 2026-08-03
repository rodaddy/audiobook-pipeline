"""Chapter marks: the thing this pipeline exists to get right.

Purpose:
    A chaptered M4B is the entire point of the tool, and chapters are the part
    that silently goes wrong. Defect 10 of the rewrite plan: the pre-rewrite
    concat stage discarded every embedded chapter it was given -- The Martian
    went from 160 chapters to 0, a Salvatore collection from 184 to 0 -- and
    the output was a valid, playable, completely unnavigable M4B. Nothing
    errored.

    These models make that failure impossible to reach quietly: an empty
    chapter set is a distinct, inspectable state, and a chapter that cannot be
    a chapter is refused at construction rather than written into a file.

WHY MILLISECONDS, AND WHY INTEGERS
    ffprobe reports chapter bounds as float seconds; Audnexus reports integer
    milliseconds. Carrying floats through the pipeline means chapter starts
    accumulate representation error across a concat, and two files that should
    abut end up overlapping by a fraction of a sample. Milliseconds as ints are
    exact, they are what FFMETADATA1 wants with ``TIMEBASE=1/1000``, and they
    are what the Audnexus API already speaks.

Key Components:
    - Chapter: one mark. start/end in ms, plus a title.
    - ChapterSet: an ordered run of them, with the invariants that make a
      chapter TABLE rather than a list of marks.

Example:
    >>> Chapter(start_ms=0, end_ms=30467, title="Opening Credits").duration_ms
    30467

See Also:
    - audiobook_pipeline.utils.ffmpeg: reads these out of a file
    - _plans/python-rewrite-sequence.md: defect 10
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: A chapter shorter than this is a container artifact, not a chapter -- some
#: encoders emit a zero-or-near-zero mark at a file boundary. Not a rounding
#: allowance: real chapters are minutes long, and 1000ms is far below anything
#: a human would call a chapter while still being unambiguously non-zero.
MIN_CHAPTER_MS = 1000


class Chapter(BaseModel):
    """One chapter mark: where it starts, where it ends, what it is called.

    Immutable. A chapter table is read far more than it is built, and an
    accidental mutation mid-pipeline is the kind of defect that only shows up
    as a wrong offset in a finished file.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Offset from the start of the FINISHED book, not the source file. The
    #: concat stage rebases these; carrying source-relative offsets forward is
    #: how every chapter after the first file lands in the wrong place.
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)

    #: Never empty. An untitled chapter gets "Chapter N" assigned by the reader
    #: that produced it, because a player showing a blank entry is worse than
    #: one showing a generic label.
    title: str = Field(min_length=1)

    @property
    def duration_ms(self) -> int:
        """How long this chapter runs."""
        return self.end_ms - self.start_ms

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> Chapter:
        """Refuse a chapter that ends at or before its start.

        Deliberately a refusal and not a repair. A zero-length or inverted
        chapter means the thing that produced it was wrong -- a bad probe, a
        miscomputed offset -- and silently dropping or clamping it writes a
        plausible chapter table over a real bug.
        """
        if self.end_ms <= self.start_ms:
            msg = (
                f"chapter {self.title!r} ends at {self.end_ms}ms but starts at "
                f"{self.start_ms}ms. A chapter must end after it starts. "
                f"ACTION REQUIRED: this indicates a bad probe or a "
                f"miscomputed concat offset, not a chapter to skip."
            )
            raise ValueError(msg)
        return self


class ChapterSet(BaseModel):
    """An ordered chapter table for one finished book.

    The invariants here are what make this a TABLE rather than a bag of marks:
    chapters are in order and do not overlap. A player given overlapping marks
    behaves differently per player -- some clamp, some seek to the wrong one,
    some show duplicate entries -- so the bug presents as "chapters are weird
    on my phone" rather than as an error anyone can act on.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    chapters: tuple[Chapter, ...] = ()

    #: Where the table came from. Carried so a log line can say it and so the
    #: metadata stage can prefer embedded marks over a fetched table without
    #: re-deriving how it got them.
    source: str = "unknown"

    @property
    def is_empty(self) -> bool:
        """True when there are no chapters.

        Checked explicitly at the point a book is written, so "this book has no
        chapters" is a decision someone made rather than a silent outcome.
        """
        return not self.chapters

    @property
    def total_duration_ms(self) -> int:
        """End of the last chapter, or 0 when empty."""
        return self.chapters[-1].end_ms if self.chapters else 0

    @model_validator(mode="after")
    def _ordered_and_non_overlapping(self) -> ChapterSet:
        """Refuse a table whose marks are out of order or overlap.

        Checked pairwise rather than by sorting: sorting would SILENTLY FIX an
        out-of-order table, and an out-of-order table means the code that built
        it computed an offset wrong. Fixing it here hides that.
        """
        for previous, current in zip(self.chapters, self.chapters[1:], strict=False):
            if current.start_ms < previous.end_ms:
                msg = (
                    f"chapter {current.title!r} starts at {current.start_ms}ms, "
                    f"before {previous.title!r} ends at {previous.end_ms}ms. "
                    f"Chapters must be ordered and non-overlapping. "
                    f"ACTION REQUIRED: the offsets were computed wrong -- "
                    f"sorting them here would hide that."
                )
                raise ValueError(msg)
        return self
