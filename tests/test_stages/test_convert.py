"""Regression coverage for current encoder selection and encode arguments."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.config import EncodingSettings
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.models.media import AudioStream, ProbeResult
from audiobook_pipeline.services import convert
from audiobook_pipeline.services.convert import can_stream_copy, convert_to_m4b


def source_probe(path: Path, *, codec: str, bit_rate: int | None) -> ProbeResult:
    """Build the typed ffprobe result used by the conversion service."""
    return ProbeResult(
        path=path,
        duration_ms=60_000,
        stream=AudioStream(
            codec=codec, sample_rate=44_100, channels=2, bit_rate=bit_rate
        ),
    )


def chapter_table() -> ChapterSet:
    """Return one valid chapter for metadata mapping assertions."""
    return ChapterSet(chapters=(Chapter(start_ms=0, end_ms=60_000, title="One"),))


@pytest.fixture
def ffmpeg_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture ffmpeg input instead of performing a real encode."""
    calls: list[list[str]] = []

    def record(args: list[str], **_: object) -> str:
        calls.append(args)
        return ""

    monkeypatch.setattr(convert, "run_ffmpeg", record)
    return calls


@pytest.mark.parametrize(
    ("codec", "bit_rate", "expected"),
    [
        ("aac", 96_000, True),
        ("aac", 256_000, False),
        ("mp3", 96_000, False),
        ("aac", None, False),
    ],
)
def test_stream_copy_selection_requires_measured_compatible_aac(
    codec: str, bit_rate: int | None, expected: bool
) -> None:
    assert (
        can_stream_copy(codec=codec, source_bps=bit_rate, configured_kbps=128)
        is expected
    )


def test_aac_at_or_under_ceiling_uses_stream_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ffmpeg_calls: list[list[str]]
) -> None:
    monkeypatch.setattr(
        convert,
        "probe",
        lambda path, **_: source_probe(path, codec="aac", bit_rate=96_000),
    )

    convert_to_m4b(
        tmp_path / "in.m4b", tmp_path / "out.m4b", chapter_table(), EncodingSettings()
    )

    args = ffmpeg_calls[0]
    assert args[args.index("-c:a") + 1] == "copy"
    assert "-b:a" not in args


def test_other_sources_use_configured_encoder_bitrate_and_thread_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ffmpeg_calls: list[list[str]]
) -> None:
    monkeypatch.setattr(
        convert,
        "probe",
        lambda path, **_: source_probe(path, codec="mp3", bit_rate=64_000),
    )

    convert_to_m4b(
        tmp_path / "in.mp3",
        tmp_path / "out.m4b",
        chapter_table(),
        EncodingSettings(codec="aac", max_bitrate=128, threads=3),
        work_dir=tmp_path / "work",
    )

    args = ffmpeg_calls[0]
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-b:a") + 1] == "64k"
    assert args[args.index("-threads") + 1] == "3"
    assert args[args.index("-map_metadata") + 1] == "1"
    assert (tmp_path / "work" / "chapters.txt").is_file()
