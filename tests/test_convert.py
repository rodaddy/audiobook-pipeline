"""Tests for encoding a joined file into a chaptered M4B."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.config import EncodingSettings
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.models.media import AudioStream, ProbeResult
from audiobook_pipeline.services import convert
from audiobook_pipeline.services.convert import (
    _target_bitrate,
    can_stream_copy,
    convert_to_m4b,
)


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture the ffmpeg argument vector instead of running it."""
    calls: list[list[str]] = []

    def record(args: list[str], **_: object) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(convert, "run_ffmpeg", record)
    return calls


def set_source(
    monkeypatch: pytest.MonkeyPatch,
    *,
    codec: str = "mp3",
    bit_rate: int | None = 128_000,
) -> None:
    """Make `probe` report a source with the given stream parameters."""

    def fake_probe(path: Path, **_: object) -> ProbeResult:
        return ProbeResult(
            path=path,
            duration_ms=60_000,
            stream=AudioStream(
                codec=codec, sample_rate=44_100, channels=2, bit_rate=bit_rate
            ),
        )

    monkeypatch.setattr(convert, "probe", fake_probe)


def chapters_of(count: int) -> ChapterSet:
    """A table of `count` one-minute chapters."""
    return ChapterSet(
        chapters=tuple(
            Chapter(
                start_ms=i * 60_000, end_ms=(i + 1) * 60_000, title=f"Chapter {i + 1}"
            )
            for i in range(count)
        )
    )


def flag(args: list[str], name: str) -> str:
    """The value following a flag in an argument vector."""
    return args[args.index(name) + 1]


# ---------------------------------------------------------------------------
# bitrate is a ceiling, never a floor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "source_bps", "expected"),
    [
        (128, 64_000, 64),  # never encode ABOVE the source
        (128, 320_000, 128),  # never exceed the configured ceiling
        (128, 128_000, 128),  # equal is fine
        (128, None, 128),  # unmeasurable source falls back to the ceiling
        (128, 500, 1),  # absurdly low source still yields a legal bitrate
    ],
)
def test_target_bitrate_is_a_ceiling(
    configured: int, source_bps: int | None, expected: int
) -> None:
    assert _target_bitrate(configured=configured, source_bps=source_bps) == expected


def test_low_bitrate_source_is_not_upsampled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    """A 64k source re-encoded at 128k is twice the size and worse audio."""
    set_source(monkeypatch, codec="mp3", bit_rate=64_000)

    convert_to_m4b(
        tmp_path / "in.mp3",
        tmp_path / "out.m4b",
        chapters_of(2),
        EncodingSettings(max_bitrate=128),
    )

    assert flag(captured[0], "-b:a") == "64k"


# ---------------------------------------------------------------------------
# stream copy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("codec", "source_bps", "expected"),
    [
        ("aac", 96_000, True),  # already what we would produce
        ("aac", 128_000, True),  # at the ceiling
        ("aac", 256_000, False),  # above the ceiling, must be re-encoded
        ("mp3", 96_000, False),  # wrong codec
        ("aac", None, False),  # unmeasurable is NOT "fine"
    ],
)
def test_stream_copy_decision(
    codec: str, source_bps: int | None, expected: bool
) -> None:
    assert (
        can_stream_copy(codec=codec, source_bps=source_bps, configured_kbps=128)
        is expected
    )


def test_aac_source_is_copied_not_re_encoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    set_source(monkeypatch, codec="aac", bit_rate=96_000)

    convert_to_m4b(
        tmp_path / "in.m4b",
        tmp_path / "out.m4b",
        chapters_of(2),
        EncodingSettings(max_bitrate=128),
    )

    assert flag(captured[0], "-c:a") == "copy"
    assert "-b:a" not in captured[0]


def test_high_bitrate_aac_is_re_encoded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    set_source(monkeypatch, codec="aac", bit_rate=256_000)

    convert_to_m4b(
        tmp_path / "in.m4b",
        tmp_path / "out.m4b",
        chapters_of(2),
        EncodingSettings(max_bitrate=128),
    )

    assert flag(captured[0], "-b:a") == "128k"


# ---------------------------------------------------------------------------
# chapters
# ---------------------------------------------------------------------------


def test_chapters_are_written_and_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    set_source(monkeypatch)

    convert_to_m4b(
        tmp_path / "in.mp3",
        tmp_path / "out.m4b",
        chapters_of(19),
        EncodingSettings(),
        work_dir=tmp_path / "work",
    )

    written = (tmp_path / "work" / "chapters.txt").read_text(encoding="utf-8")
    assert written.count("[CHAPTER]") == 19
    assert flag(captured[0], "-map_metadata") == "1"


def test_empty_chapter_table_writes_no_metadata_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    """An empty table would REPLACE whatever the source carried."""
    set_source(monkeypatch)

    convert_to_m4b(
        tmp_path / "in.mp3", tmp_path / "out.m4b", ChapterSet(), EncodingSettings()
    )

    assert "-map_metadata" not in captured[0]
    assert not (tmp_path / "chapters.txt").exists()


# ---------------------------------------------------------------------------
# container
# ---------------------------------------------------------------------------


def test_faststart_is_always_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    """Without it Plex reports the book as unplayable, not as slow."""
    set_source(monkeypatch)

    convert_to_m4b(
        tmp_path / "in.mp3", tmp_path / "out.m4b", chapters_of(1), EncodingSettings()
    )

    assert flag(captured[0], "-movflags") == "+faststart"


def test_only_the_audio_stream_is_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    set_source(monkeypatch)

    convert_to_m4b(
        tmp_path / "in.mp3", tmp_path / "out.m4b", chapters_of(1), EncodingSettings()
    )

    assert flag(captured[0], "-map") == "0:a"


def test_channel_count_comes_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    set_source(monkeypatch, codec="mp3")

    convert_to_m4b(
        tmp_path / "in.mp3",
        tmp_path / "out.m4b",
        chapters_of(1),
        EncodingSettings(channels=1),
    )

    assert flag(captured[0], "-ac") == "1"


def test_output_directory_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, captured: list[list[str]]
) -> None:
    set_source(monkeypatch)
    output = tmp_path / "deep" / "nested" / "out.m4b"

    assert (
        convert_to_m4b(tmp_path / "in.mp3", output, chapters_of(1), EncodingSettings())
        == output
    )
    assert output.parent.is_dir()
