"""Audio on disk, before anything knows what book it is.

Purpose:
    Discovery walks a directory tree and has to answer one question per folder:
    is this ONE book made of several files, or several books that happen to
    share a folder? Getting it backwards is the most expensive mistake the
    pipeline can make, and it makes it silently.

    Defect 4 of the rewrite plan: the pre-rewrite code treated any folder with
    more than one ``.m4b`` as a chaptered book, so a folder of 40 separate
    novels was queued as a single concat -- 510 hours of audio into one file.
    Defect 3: a loose audio file at a collection root pruned the entire tree
    below it, collapsing 11 book directories into 1.

    Both were classification errors expressed as arithmetic, so nothing
    errored. These models make the classification explicit and testable.

Key Components:
    - AudioFile: one file with the duration that lets it be classified.
    - BookDirectory: a folder plus the verdict about what it contains.

Example:
    >>> from pathlib import Path
    >>> f = AudioFile(path=Path("a.m4b"), duration_ms=36_000_000)
    >>> f.duration_hours
    10.0

See Also:
    - _plans/python-rewrite-sequence.md: defects 3 and 4
"""

from __future__ import annotations

import statistics
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

#: Audio this long is a BOOK, not a chapter of one. Two hours is deliberately
#: conservative: real chapters run about an hour at the extreme, and a short
#: novella runs two or three. Anything at or above this in a multi-file folder
#: means the folder holds separate works.
SEPARATE_BOOK_MIN_MS = 2 * 60 * 60 * 1000

#: Extensions the pipeline will convert FROM. Deliberately wide -- the tool
#: exists because sources arrive in whatever shape they arrived in, and
#: pre-filtering by extension is how 76 source files became invisible to the
#: library diff (defect 1).
SOURCE_EXTENSIONS = frozenset({
    ".m4b",
    ".m4a",
    ".mp3",
    ".flac",
    ".ogg",
    ".opus",
    ".wma",
    ".wav",
    ".aac",
})


class AudioFile(BaseModel):
    """One audio file, with the duration that lets it be classified."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    duration_ms: int = Field(gt=0)

    @property
    def duration_hours(self) -> float:
        """Runtime in hours."""
        return self.duration_ms / 3_600_000

    @property
    def is_book_length(self) -> bool:
        """Whether this file is long enough to be a whole book on its own."""
        return self.duration_ms >= SEPARATE_BOOK_MIN_MS


class BookDirectory(BaseModel):
    """A directory of audio, plus the verdict about what it holds.

    ``files`` is ordered, and the order is the play order. Sorting happens at
    discovery where the filenames are still available; by the time a set of
    files reaches concat, the order it arrives in IS the answer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    files: tuple[AudioFile, ...] = Field(min_length=1)

    @property
    def total_duration_ms(self) -> int:
        """Combined runtime of every file."""
        return sum(f.duration_ms for f in self.files)

    @property
    def holds_separate_books(self) -> bool:
        """True when these files are distinct works rather than one book.

        Uses the MEDIAN file duration, not the mean and not any single file. A
        mean is dragged by one outlier -- a 3-minute "end credits" track among
        real chapters, or one 12-hour book among short ones -- and either
        direction of that error is expensive.

        FAILS SAFE TOWARD "SEPARATE". A single file is never a concat
        candidate, and when the answer is unclear the pipeline should convert
        books individually: producing 40 correct books when 1 was wanted is a
        tidy-up, while producing one 510-hour file when 40 were wanted destroys
        the source layout and takes hours of CPU to do it.
        """
        if len(self.files) <= 1:
            return True
        median_ms = statistics.median(f.duration_ms for f in self.files)
        return median_ms >= SEPARATE_BOOK_MIN_MS

    @property
    def is_multi_file_book(self) -> bool:
        """True when these files should be concatenated into ONE book."""
        return len(self.files) > 1 and not self.holds_separate_books

    @property
    def identity_path(self) -> Path:
        """The path that identifies this book, for naming and hashing.

        NOT always ``self.path``. Discovery splits a folder of separate works
        into one ``BookDirectory`` per file, and every one of those keeps the
        SHARED folder path -- so 19 Drizzt novels in one directory all report
        the same ``path`` and are distinguishable only by their file.

        Observed 2026-08-02 against the real source tree: exactly that shape,
        19 books deep. A caller that reached for ``path`` to name or hash them
        would silently collapse all 19 into one identity, and the failure would
        surface as 18 missing books rather than as an error.

        Returns:
            The single file's path when this is one file, the directory when
            these files concatenate into one book.
        """
        return self.path if self.is_multi_file_book else self.files[0].path
