"""Tests for ffprobe subprocess wrappers."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from audiobook_pipeline.ffprobe import (
    count_chapters,
    duration_to_timestamp,
    get_bitrate,
    get_channels,
    get_codec,
    get_duration,
    get_sample_rate,
    validate_audio_file,
)


def _mock_result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr="",
    )


class TestGetDuration:
    @patch("audiobook_pipeline.ffprobe._run_ffprobe")
    def test_parses_float(self, mock_run):
        mock_run.return_value = _mock_result("123.456\n")
        assert get_duration(Path("test.mp3")) == 123.456


class TestGetBitrate:
    @patch("audiobook_pipeline.ffprobe._run_ffprobe")
    def test_parses_int(self, mock_run):
        mock_run.return_value = _mock_result("128000\n")
        assert get_bitrate(Path("test.mp3")) == 128000


class TestGetCodec:
    @patch("audiobook_pipeline.ffprobe._run_ffprobe")
    def test_parses_string(self, mock_run):
        mock_run.return_value = _mock_result("aac\n")
        assert get_codec(Path("test.mp3")) == "aac"


class TestGetChannels:
    @patch("audiobook_pipeline.ffprobe._run_ffprobe")
    def test_parses_int(self, mock_run):
        mock_run.return_value = _mock_result("2\n")
        assert get_channels(Path("test.mp3")) == 2


class TestGetSampleRate:
    @patch("audiobook_pipeline.ffprobe._run_ffprobe")
    def test_parses_int(self, mock_run):
        mock_run.return_value = _mock_result("44100\n")
        assert get_sample_rate(Path("test.mp3")) == 44100


class TestValidateAudioFile:
    def test_missing_file(self, tmp_path):
        assert validate_audio_file(tmp_path / "nonexistent.mp3") is False

    @patch("audiobook_pipeline.ffprobe.get_codec")
    @patch("audiobook_pipeline.ffprobe._run_ffprobe")
    def test_valid_file(self, mock_run, mock_codec, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"fake")
        mock_run.return_value = _mock_result()
        mock_codec.return_value = "mp3"
        assert validate_audio_file(f) is True

    @patch("audiobook_pipeline.ffprobe._run_ffprobe")
    def test_ffprobe_fails(self, mock_run, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"fake")
        mock_run.return_value = _mock_result(returncode=1)
        assert validate_audio_file(f) is False


class TestDurationToTimestamp:
    def test_zero(self):
        assert duration_to_timestamp(0) == "00:00:00"

    def test_hours(self):
        assert duration_to_timestamp(3661) == "01:01:01"

    def test_fractional(self):
        assert duration_to_timestamp(90.7) == "00:01:30"


class TestCountChapters:
    @patch("subprocess.run")
    def test_with_chapters(self, mock_run):
        mock_run.return_value = _mock_result(
            '{"chapters": [{"id": 0}, {"id": 1}, {"id": 2}]}'
        )
        assert count_chapters(Path("test.m4b")) == 3

    @patch("subprocess.run")
    def test_no_chapters(self, mock_run):
        mock_run.return_value = _mock_result('{"chapters": []}')
        assert count_chapters(Path("test.mp3")) == 0

    @patch("subprocess.run")
    def test_ffprobe_error(self, mock_run):
        mock_run.return_value = _mock_result(returncode=1)
        assert count_chapters(Path("test.mp3")) == 0

    @patch("subprocess.run")
    def test_invalid_json(self, mock_run):
        mock_run.return_value = _mock_result("not json")
        assert count_chapters(Path("test.mp3")) == 0
