"""Writing M4B tags, including the ones ffmpeg silently refuses to write.

THE BUG THIS MODULE EXISTS FOR
    ``ffmpeg -metadata ASIN=B00TEST123`` exits 0 and writes nothing. MP4 has no
    standard atom for ASIN, and ffmpeg drops unknown keys without a warning.
    The same is true of ``sort_album`` and ``publisher``. Every conversion
    reported success while the tag Plex's Audnexus agent matches on was absent,
    so books landed in the library unmatched and the cause was invisible --
    ffprobe does not surface the freeform atoms either, so even checking the
    output file appeared to confirm the tag was missing for no reason.

    mutagen writes them. It is maintained, has no dependencies outside the
    standard library, and speaks MP4 atoms directly rather than through a
    transcoder that has opinions about which ones matter.

WHY NOT mp4tags
    The pre-rewrite code shelled out to ``mp4tags`` from mp4v2. That worked,
    but it was an OPTIONAL install: absent, the pipeline logged a debug line
    and carried on producing untagged books. A dependency whose absence
    silently degrades output is worse than a required one, because nothing
    surfaces until someone notices a library full of unmatched books.

Key Components:
    - write_tags: apply BookMetadata to a finished M4B
    - read_asin: read back what was written, for verification

Example:
    >>> _freeform("ASIN")
    '----:com.apple.iTunes:ASIN'

See Also:
    - audiobook_pipeline.models.metadata: the shape being written
    - https://github.com/seanap/Plex-Audiobook-Guide: the tag conventions
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

from audiobook_pipeline.models.metadata import BookMetadata, CoverArt

log = logger.bind(stage="tag")

#: Standard MP4 atoms, keyed by the metadata field that feeds them. These are
#: the four-character codes Apple defined; every player understands them.
#:
#: The artist/album_artist split follows seanap's Plex-Audiobook-Guide, which
#: is what Rico's library is organised around: `artist` carries author AND
#: narrator so both are searchable, `album_artist` carries the author alone so
#: the library groups by writer rather than by writer-narrator pair.
STANDARD_ATOMS = {
    "\xa9nam": "title",
    "\xa9alb": "title",
    "aART": "author",
    "\xa9wrt": "narrator",
    "\xa9day": "release_year",
    "\xa9cmt": "summary",
    "desc": "summary",
    "cprt": "copyright",
}

#: Atoms with no standard MP4 equivalent, written as iTunes freeform. This is
#: the set ffmpeg drops silently.
FREEFORM_ATOMS = ("ASIN", "SERIES", "SERIES-PART", "PUBLISHER")

#: Marks the file as an audiobook rather than music. Plex and Apple Books both
#: read this to decide which UI to present -- without it a 12-hour book shows
#: up as a very long song.
MEDIA_TYPE_AUDIOBOOK = 2


def _freeform(name: str) -> str:
    """Build the mutagen key for an iTunes freeform atom.

    Args:
        name: The atom name, e.g. ``ASIN``.

    Returns:
        The ``----:mean:name`` key mutagen expects.
    """
    return f"----:com.apple.iTunes:{name}"


def _encode(value: str) -> MP4FreeForm:
    """Wrap a string as a UTF-8 freeform atom value."""
    return MP4FreeForm(value.encode("utf-8"))


def _album_text(metadata: BookMetadata) -> str:
    """Build the legacy-visible album text from series metadata."""
    if metadata.series and metadata.series_position:
        return f"{metadata.series}, Book {metadata.series_position}"
    if metadata.series:
        return metadata.series
    return metadata.title


def _grouping_text(metadata: BookMetadata) -> str:
    """Build the legacy-visible grouping text from series metadata."""
    if metadata.series_position:
        return f"{metadata.series}, Book #{metadata.series_position}"
    return metadata.series


def _standard_values(metadata: BookMetadata) -> dict[str, list[str]]:
    """Build the standard-atom payload, omitting anything empty.

    Empty values are OMITTED rather than written as empty strings: an empty
    atom is not the same as an absent one, and some players display it as a
    blank field rather than falling back to a sensible default.

    Args:
        metadata: Resolved book metadata.

    Returns:
        Atom keys mapped to their values.
    """
    values: dict[str, list[str]] = {}
    for atom, field in STANDARD_ATOMS.items():
        raw = getattr(metadata, field, "")
        if raw:
            values[atom] = [str(raw)]

    # artist is author + narrator, which no single field supplies.
    if metadata.author and metadata.narrator:
        values["\xa9ART"] = [f"{metadata.author}, {metadata.narrator}"]
    elif metadata.author:
        values["\xa9ART"] = [metadata.author]

    if metadata.genres:
        values["\xa9gen"] = [", ".join(metadata.genres)]

    if metadata.has_series:
        values["\xa9alb"] = [_album_text(metadata)]
        values["sonm"] = [metadata.sort_title]
        # soal is what a library sorts a series by. Without it a series lists
        # alphabetically, so book 10 sits between book 1 and book 2.
        values["soal"] = [metadata.sort_title]
        values["\xa9grp"] = [_grouping_text(metadata)]

    return values


def _write_freeform(tags: object, metadata: BookMetadata) -> None:
    """Write non-standard metadata, leaving absent values absent."""
    values = {
        "ASIN": metadata.asin,
        "SERIES": metadata.series,
        "SERIES-PART": metadata.series_position,
        "PUBLISHER": metadata.publisher,
    }
    for name in FREEFORM_ATOMS:
        value = values[name]
        if value:
            tags[_freeform(name)] = [_encode(value)]  # type: ignore[index]


def _write_series_atoms(tags: object, metadata: BookMetadata) -> None:
    """Write Apple Books work/movement atoms when the values are valid."""
    if not metadata.has_series:
        return
    tags["shwm"] = [1]  # type: ignore[index]
    tags["\xa9mvn"] = [metadata.series]  # type: ignore[index]
    if metadata.series_position.isdecimal():
        tags["\xa9mvi"] = [int(metadata.series_position)]  # type: ignore[index]


def _write_cover(tags: object, cover: CoverArt | None) -> None:
    """Embed validated cover art using Mutagen's native MP4 wrapper."""
    if cover is None:
        return
    image_format = (
        MP4Cover.FORMAT_JPEG
        if cover.content_type == "image/jpeg"
        else MP4Cover.FORMAT_PNG
    )
    tags["covr"] = [MP4Cover(cover.data, imageformat=image_format)]  # type: ignore[index]


def write_tags(
    path: Path, metadata: BookMetadata, *, cover: CoverArt | None = None
) -> None:
    """Write every tag onto a finished M4B.

    Args:
        path: The M4B to tag, modified in place.
        metadata: What to write.
        cover: Validated optional JPEG or PNG bytes to embed.

    Raises:
        FileNotFoundError: The file does not exist.
        mutagen.MutagenError: The file is not a readable MP4 container.
    """
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:  # pragma: no cover - add_tags() guarantees this
        msg = "MP4 container has no tag block and one could not be created"
        raise RuntimeError(msg)
    for atom, values in _standard_values(metadata).items():
        tags[atom] = values
    tags["stik"] = [MEDIA_TYPE_AUDIOBOOK]
    tags["pgap"] = True
    _write_freeform(tags, metadata)
    _write_series_atoms(tags, metadata)
    _write_cover(tags, cover)
    audio.save()
    log.debug("tagging complete")


def read_asin(path: Path) -> str:
    """Read the ASIN back off a tagged file.

    Exists because verification cannot use ffprobe: ffprobe does not surface
    freeform atoms at all, so a file with a correct ASIN looks untagged to it.
    That is what made the original bug so hard to see -- every tool used to
    check the output agreed the tag was missing.

    Args:
        path: The M4B to inspect.

    Returns:
        The ASIN, or an empty string when absent.
    """
    audio = MP4(path)
    if audio.tags is None:
        return ""

    values = audio.tags.get(_freeform("ASIN"))
    if not values:
        return ""

    raw = values[0]
    return bytes(raw).decode("utf-8", errors="replace")
