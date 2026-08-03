"""Joining a folder of audio files into one, and building its chapter table.

Purpose:
    A multi-file book arrives as 19 MP3s named ``chapter01.mp3``... and has to
    become one file with 19 chapter marks at the right offsets. The join itself
    is a stream copy; the chapters are the part worth getting right.

WHERE THE CHAPTERS COME FROM, IN ORDER
    1. **Marks already embedded in the source files.** A source that carries a
       real chapter table -- names, not "Chapter 1" -- knows more than the
       filenames do. The pre-rewrite code discarded these and rebuilt from file
       boundaries, so a 3-file source carrying 47 real chapter names came out
       with 3 chapters called ``Part 1``, ``Part 2``, ``Part 3``.
    2. **File boundaries.** One chapter per input file, titled from the
       filename. Correct for the file-per-chapter layout, which is most of them.

    Never both, and never a merge of the two: a source with embedded marks in
    SOME files is the ambiguous case, and taking the embedded ones where present
    would interleave two different numbering schemes into one table.

WHY THE OFFSETS ARE ACCUMULATED, NOT PROBED
    Each file's chapter offsets are relative to that file. Concatenated, file 2
    starts where file 1 ended, so every mark in it shifts by file 1's duration.
    Accumulating the durations we already probed is exact; re-probing the joined
    output would be a second source of truth that can disagree with the first.

Example:
    >>> _title_from_filename(Path("01 - The Long Road.mp3"))
    'The Long Road'

See Also:
    - audiobook_pipeline.models.chapter: the ordering invariants
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.utils.ffmpeg import FfmpegError, probe, run_ffmpeg
from audiobook_pipeline.utils.paths import sanitize_chapter_title

log = logger.bind(stage="concat")

#: Leading track numbers: "01 - ", "01. ", "01_", "Track 03 ". Stripped so a
#: chapter reads "The Long Road" rather than "01 - The Long Road", which every
#: player then displays next to its own track number.
_LEADING_INDEX = re.compile(
    r"^(?:track|part|chapter|ch|disc|cd)?[\s_-]*\d{1,4}[\s_.-]+",
    re.IGNORECASE,
)

#: Trailing part markers: "Part 01 of 19", "(1 of 5)", "01-19". These describe
#: the FILE's place in a split, not the chapter's name, and every file in the
#: book carries one -- so keeping them produces 19 chapters all called some
#: variation of the book's own title.
#:
#: Observed 2026-08-02 on the real source tree: "Promise of Blood01-19",
#: "The Autumn Republic (Unabridged) Part 01 of 19", "Forsworn (1 of 5)".
#: Commit 82f43a9 stripped these from BOOK titles; chapter titles are built by
#: different code and never got the same treatment.
_TRAILING_PART = re.compile(
    r"[\s_(\[-]*(?:part|disc|cd|file)?[\s_-]*\d{1,4}\s*(?:of|/|-)\s*\d{1,4}[\s_)\]]*$",
    re.IGNORECASE,
)

#: A parenthetical that says how the file was produced rather than what it is.
_PRODUCTION_NOTE = re.compile(
    r"[\s_]*[(\[](?:unabridged|abridged|audiobook|audio ?book)[)\]]",
    re.IGNORECASE,
)

#: Where a chapter table came from. Recorded on the ChapterSet so a log line
#: and the database can say it without re-deriving how it got there.
SOURCE_EMBEDDED = "embedded"
SOURCE_FILES = "files"


def _title_from_filename(path: Path) -> str:
    """Derive a chapter title from a filename.

    Args:
        path: The audio file.

    Returns:
        A cleaned title. Falls back to the bare stem when stripping the index
        would leave nothing -- a file honestly named ``01.mp3`` becomes
        ``01``, which is at least the truth, rather than an empty entry.
    """
    stem = path.stem.replace("_", " ").strip()
    stripped = _PRODUCTION_NOTE.sub("", stem)
    stripped = _TRAILING_PART.sub("", stripped)
    stripped = _LEADING_INDEX.sub("", stripped).strip()
    return sanitize_chapter_title(stripped or stem)


def _titles_are_informative(files: tuple[AudioFile, ...]) -> bool:
    """Whether the filenames actually distinguish one chapter from another.

    A split-by-size source names every part after the BOOK -- once the part
    markers come off, all 19 files say "Promise of Blood". Nineteen identically
    named chapters are worse than numbered ones: a player shows a list that
    cannot be navigated, and it looks like a bug in the file rather than an
    absence of information in the source.

    Args:
        files: Source files in play order.

    Returns:
        True when the derived titles are distinct enough to use. A single file
        is trivially informative.
    """
    if len(files) <= 1:
        return True

    titles = [_title_from_filename(f.path) for f in files]

    # Every title identical: the filenames name the BOOK, not its chapters.
    if len(set(titles)) == 1:
        return False

    # Distinct, but only because of a number left in them. "The Crimson
    # Campaign 01", "The Crimson Campaign 02" pass a naive uniqueness check
    # while carrying exactly as little information as the identical case --
    # observed on the real tree, where the surviving digits are a DISC number
    # and every chapter would be named after the book plus one.
    generic = [re.sub(r"\d+", "", t).strip() for t in titles]

    # Mostly-repeats counts as uninformative even when a few names differ.
    # Observed: an 8-file book of "Intro" plus seven identical "Forsworn"
    # entries -- the set has two members, so a uniqueness check passes, and
    # the player still shows seven rows nobody can tell apart. Requiring the
    # MAJORITY to be distinct is what separates that from a real chapter list.
    return len(set(generic)) * 2 > len(generic)


def _embedded_chapters(files: tuple[AudioFile, ...]) -> ChapterSet | None:
    """Collect embedded chapter marks across files, shifted into place.

    Args:
        files: Source files in play order.

    Returns:
        The combined table, or None when the sources carry no embedded marks
        or carry them inconsistently. None means "use file boundaries", not
        "error".
    """
    collected: list[Chapter] = []
    offset_ms = 0
    carriers = 0

    for audio in files:
        try:
            result = probe(audio.path)
        except FfmpegError as exc:
            log.warning("cannot read chapters from {}: {}", audio.path, exc)
            return None

        if not result.chapters.is_empty:
            carriers += 1
            collected.extend(
                chapter.model_copy(
                    update={
                        "start_ms": chapter.start_ms + offset_ms,
                        "end_ms": chapter.end_ms + offset_ms,
                    }
                )
                for chapter in result.chapters.chapters
            )
        offset_ms += audio.duration_ms

    if not collected:
        return None

    # Marks in SOME files is the ambiguous case. Taking the embedded ones where
    # present would interleave two numbering schemes -- real chapter names from
    # one file, nothing from the next -- into a single table that reads as
    # corrupt rather than as a fallback.
    if carriers != len(files):
        log.warning(
            "{} of {} files carry chapters; using file boundaries instead",
            carriers,
            len(files),
        )
        return None

    return ChapterSet(chapters=tuple(collected), source=SOURCE_EMBEDDED)


def _file_boundary_chapters(files: tuple[AudioFile, ...]) -> ChapterSet:
    """Build one chapter per input file, titled from its filename.

    Args:
        files: Source files in play order.

    Returns:
        A table with one mark per file, offsets accumulated from the durations
        already probed at discovery.
    """
    informative = _titles_are_informative(files)
    if not informative:
        log.info("filenames carry no per-chapter names; numbering instead")

    chapters: list[Chapter] = []
    offset_ms = 0
    for index, audio in enumerate(files, start=1):
        title = _title_from_filename(audio.path) if informative else f"Chapter {index}"
        chapters.append(
            Chapter(
                start_ms=offset_ms,
                end_ms=offset_ms + audio.duration_ms,
                title=title,
            )
        )
        offset_ms += audio.duration_ms
    return ChapterSet(chapters=tuple(chapters), source=SOURCE_FILES)


def build_chapters(book: BookDirectory) -> ChapterSet:
    """Build the chapter table for a book, preferring embedded marks.

    Args:
        book: The book being joined.

    Returns:
        The table to write. Never empty for a multi-file book -- there is
        always at least one mark per file.
    """
    embedded = _embedded_chapters(book.files)
    if embedded is not None:
        log.info("using {} embedded chapter(s)", len(embedded.chapters))
        return embedded

    boundaries = _file_boundary_chapters(book.files)
    log.info("using {} file-boundary chapter(s)", len(boundaries.chapters))
    return boundaries


def write_concat_list(files: tuple[AudioFile, ...], destination: Path) -> Path:
    """Write the file list ffmpeg's concat demuxer reads.

    Args:
        files: Source files in play order.
        destination: Directory to write the list into.

    Returns:
        Path to the written list.
    """
    destination.mkdir(parents=True, exist_ok=True)
    list_path = destination / "concat.txt"

    # Single quotes with '' escaping is the concat demuxer's own syntax. A
    # book named "Foo's Story" is common enough that getting this wrong fails
    # on real input rather than on a contrived case.
    lines = [f"file '{str(f.path).replace(chr(39), chr(39) * 2)}'" for f in files]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list_path


def concat_files(book: BookDirectory, output: Path) -> Path:
    """Join a book's files into one, without re-encoding.

    Stream copy (``-c copy``): the files are already in their source format and
    the convert stage does the encoding. Re-encoding here would cost hours and
    a generation of quality to produce an intermediate that is thrown away.

    Args:
        book: The multi-file book to join.
        output: Path for the joined file.

    Returns:
        The output path.

    Raises:
        FfmpegError: ffmpeg failed.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    list_path = write_concat_list(book.files, output.parent)

    run_ffmpeg([
        "-f",
        "concat",
        # The list holds absolute paths from outside the list's own
        # directory, which the demuxer refuses without this.
        "-safe",
        "0",
        "-i",
        str(list_path),
        "-c",
        "copy",
        str(output),
    ])

    log.info("joined {} file(s) into {}", len(book.files), output.name)
    return output
