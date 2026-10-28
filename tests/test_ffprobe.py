"""Regression tests for the typed ffprobe boundary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from audiobook_pipeline.utils.ffmpeg import FfmpegError, probe


def _completed(
    payload: object, returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=returncode,
        stdout=json.dumps(payload),
        stderr="bad",
    )


def _payload() -> dict[str, object]:
    return {
        "format": {"duration": "123.456", "format_name": "mp3"},
        "streams": [
            {
                "codec_name": "aac",
                "sample_rate": "44100",
                "channels": 2,
                "bit_rate": "128000",
            }
        ],
        "chapters": [
            {"start_time": "0", "end_time": "60", "tags": {"title": "One"}},
            {"start_time": "60", "end_time": "123.456", "tags": {}},
        ],
    }


def test_probe_returns_typed_media_and_chapters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: _completed(_payload())
    )

    result = probe(tmp_path / "book.mp3")

    assert result.duration_ms == 123_456
    assert result.stream.codec == "aac"
    assert result.stream.bit_rate == 128_000
    assert result.stream.channels == 2
    assert result.stream.sample_rate == 44_100
    assert [chapter.title for chapter in result.chapters.chapters] == [
        "One",
        "Chapter 2",
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"format": {}, "streams": [{}]}, "no readable duration"),
        ({"format": {"duration": "0"}, "streams": [{}]}, "duration of zero"),
        ({"format": {"duration": "1"}, "streams": []}, "no audio stream"),
    ],
)
def test_probe_rejects_incomplete_audio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _completed(payload))
    with pytest.raises(FfmpegError, match=message):
        probe(tmp_path / "bad.mp3")


def test_probe_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    completed = subprocess.CompletedProcess(
        args=["ffprobe"], returncode=0, stdout="not json", stderr=""
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)
    with pytest.raises(FfmpegError, match="unparseable JSON"):
        probe(tmp_path / "bad.mp3")


def test_probe_surfaces_process_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: _completed({}, returncode=1)
    )
    with pytest.raises(FfmpegError, match="bad"):
        probe(tmp_path / "bad.mp3")
