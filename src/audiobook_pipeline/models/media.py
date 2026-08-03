"""What ffprobe reports about an audio file, as a shape rather than a dict.

Purpose:
    ffprobe's JSON is deeply nested, partially optional, and stringly typed --
    durations arrive as ``"3600.123456"``, sample rates as ``"44100"``, and any
    field may be absent depending on the container. The pre-rewrite code walked
    that structure inline at seven call sites, each with its own idea of what
    was optional.

    Parsing it once, here, means a caller receives a ProbeResult with real
    ints and floats or an error naming the field -- not a dict that happens to
    be missing ``sample_rate`` for FLAC.

WHY THE CODEC FIELDS MATTER MORE THAN THEY LOOK
    The convert stage stream-copies AAC sources instead of re-encoding, which
    avoids a lossy round trip. That is only safe when codec, sample rate, and
    channel count MATCH across every input file: concatenating mismatched
    streams with ``-c copy`` produces a file that plays correctly until the
    first boundary and is noise afterwards. These fields exist so that check
    can be made rather than assumed.

Example:
    >>> AudioStream(codec="aac", sample_rate=44100, channels=2).is_compatible_with(
    ...     AudioStream(codec="aac", sample_rate=44100, channels=2)
    ... )
    True

See Also:
    - audiobook_pipeline.models.chapter: chapters read out of the same probe
    - _plans/python-rewrite-sequence.md: the AAC passthrough finding
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from audiobook_pipeline.models.chapter import ChapterSet


class AudioStream(BaseModel):
    """The audio stream parameters that decide whether files can be joined."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    codec: str = Field(min_length=1)
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0, le=8)

    #: bits per second. Absent for some lossless containers, so optional --
    #: and genuinely optional rather than defaulted to 0, because 0 would read
    #: as "silent" to anything doing arithmetic on it.
    bit_rate: int | None = Field(default=None, gt=0)

    def is_compatible_with(self, other: AudioStream) -> bool:
        """True when these two streams can be concatenated with ``-c copy``.

        Bit rate deliberately excluded: a VBR encode varies per file and does
        not affect whether the streams join cleanly. Codec, sample rate, and
        channel count do.

        Args:
            other: The stream to compare against.

        Returns:
            Whether a stream copy is safe.
        """
        return (
            self.codec == other.codec
            and self.sample_rate == other.sample_rate
            and self.channels == other.channels
        )


class ProbeResult(BaseModel):
    """Everything one ffprobe call told us about one file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    duration_ms: int = Field(gt=0)
    stream: AudioStream
    chapters: ChapterSet = ChapterSet()

    #: Tags read from the container. Kept as strings because that is what they
    #: are on disk -- interpreting "01/12" as a track number is the tag
    #: reader's job, not the prober's.
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def duration_hours(self) -> float:
        """Runtime in hours. Used by the book-vs-chapter classifier."""
        return self.duration_ms / 3_600_000
