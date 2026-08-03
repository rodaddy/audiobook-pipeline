"""Tests for the shared floor: paths, ffmpeg parsing, http retry, tagging.

These modules sit at the bottom of the import graph, so a defect here reaches
every service above it. The ffmpeg parsing tests use recorded ffprobe payloads
rather than real files -- the parsing is what has been wrong historically, not
the subprocess call.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from tenacity import wait_none

from audiobook_pipeline.models.chapter import Chapter
from audiobook_pipeline.utils.ffmpeg import (
    FfmpegError,
    _parse_chapters,
    _parse_stream,
    build_chapter_metadata,
)
from audiobook_pipeline.utils.http import (
    RetryableHttpError,
    get_json,
    is_retryable_status,
)
from audiobook_pipeline.utils.paths import (
    MAX_COMPONENT_BYTES,
    sanitize_chapter_title,
    sanitize_filename,
)


def _ffprobe_chapter(start: float, end: float, title: str | None) -> dict[str, Any]:
    """One chapter as ffprobe reports it: float seconds, in strings."""
    entry: dict[str, Any] = {"start_time": str(start), "end_time": str(end)}
    if title is not None:
        entry["tags"] = {"title": title}
    return entry


class TestSanitizeFilename:
    def test_replaces_every_illegal_character(self) -> None:
        assert sanitize_filename('a/b\\c:"d') == "a_b_c_d"

    def test_strips_leading_dots(self) -> None:
        """A leading dot hides the file on Unix."""
        assert sanitize_filename("..hidden") == "hidden"

    def test_strips_leading_underscores(self) -> None:
        assert sanitize_filename("__private") == "private"

    def test_collapses_underscore_runs(self) -> None:
        assert sanitize_filename("a___b") == "a_b"

    def test_strips_trailing_dots(self) -> None:
        """Windows and SMB strip these silently, so the name round-trips wrong."""
        assert sanitize_filename("name...") == "name"

    def test_leaves_a_clean_name_alone(self) -> None:
        assert sanitize_filename("chapter_01.mp3") == "chapter_01.mp3"

    def test_truncates_to_the_byte_limit(self) -> None:
        result = sanitize_filename("x" * 400)
        assert len(result.encode("utf-8")) <= MAX_COMPONENT_BYTES

    def test_truncation_preserves_the_extension(self) -> None:
        """Truncating book.m4b to 'boo' produces a file nothing recognizes."""
        result = sanitize_filename("x" * 400 + ".m4b")
        assert result.endswith(".m4b")
        assert len(result.encode("utf-8")) <= MAX_COMPONENT_BYTES

    def test_truncates_by_bytes_not_characters(self) -> None:
        """A CJK title hits the 255-BYTE limit at ~85 characters."""
        result = sanitize_filename("漢" * 200)
        assert len(result.encode("utf-8")) <= MAX_COMPONENT_BYTES
        # Valid UTF-8: never cut mid-codepoint.
        result.encode("utf-8").decode("utf-8")

    def test_never_returns_empty(self) -> None:
        """An empty component silently collapses the path rather than raising."""
        assert sanitize_filename("...") == "untitled"
        assert sanitize_filename("") == "untitled"

    def test_normalizes_unicode(self) -> None:
        """macOS stores decomposed; the same title must not make two folders."""
        decomposed = "Café"
        composed = "Café"
        assert sanitize_filename(decomposed) == sanitize_filename(composed)


class TestSanitizeChapterTitle:
    def test_illegal_characters_become_spaces_not_underscores(self) -> None:
        """'Chapter_ One' reads like an error; 'Chapter One' reads correctly."""
        assert sanitize_chapter_title("Chapter: One") == "Chapter One"

    def test_slashes_become_spaces(self) -> None:
        assert sanitize_chapter_title("a/b\\c") == "a b c"

    def test_collapses_and_strips_whitespace(self) -> None:
        assert sanitize_chapter_title("  hello  ") == "hello"

    def test_newlines_collapse(self) -> None:
        """A scraped title carries newlines that break downstream tools."""
        assert sanitize_chapter_title("Chapter\n\nOne") == "Chapter One"

    def test_never_returns_empty(self) -> None:
        """A blank entry in a player is worse than a generic label."""
        assert sanitize_chapter_title("") == "Chapter"
        assert sanitize_chapter_title("///") == "Chapter"


class TestChapterParsing:
    def test_converts_float_seconds_to_integer_milliseconds(self) -> None:
        """Floats through the pipeline accumulate error across a concat."""
        chapters = _parse_chapters([_ffprobe_chapter(0.0, 30.467, "Opening")])
        assert chapters.chapters[0].start_ms == 0
        assert chapters.chapters[0].end_ms == 30_467

    def test_reads_a_full_table(self) -> None:
        """Defect 10: The Martian's 160 chapters became 0."""
        raw = [
            _ffprobe_chapter(i * 60.0, (i + 1) * 60.0, f"Chapter {i + 1}")
            for i in range(160)
        ]
        chapters = _parse_chapters(raw)
        assert len(chapters.chapters) == 160
        assert chapters.source == "embedded"

    def test_untitled_chapter_gets_a_generated_name(self) -> None:
        chapters = _parse_chapters([_ffprobe_chapter(0.0, 60.0, None)])
        assert chapters.chapters[0].title == "Chapter 1"

    def test_skips_a_malformed_entry_without_losing_the_rest(self) -> None:
        """One unparseable chapter must not cost the other 159."""
        raw = [
            _ffprobe_chapter(0.0, 60.0, "Good"),
            {"start_time": "not-a-number", "end_time": "60"},
            _ffprobe_chapter(60.0, 120.0, "Also good"),
        ]
        chapters = _parse_chapters(raw)
        assert len(chapters.chapters) == 2

    def test_skips_a_zero_length_chapter(self) -> None:
        """Some encoders emit a zero-width mark at a file boundary."""
        raw = [_ffprobe_chapter(0.0, 0.0, "Artifact"), _ffprobe_chapter(0.0, 60.0, "A")]
        assert len(_parse_chapters(raw).chapters) == 1

    def test_no_chapters_yields_an_empty_set(self) -> None:
        assert _parse_chapters([]).is_empty


class TestStreamParsing:
    def test_reads_codec_rate_and_channels(self) -> None:
        stream = _parse_stream([
            {"codec_name": "aac", "sample_rate": "44100", "channels": 2}
        ])
        assert stream.codec == "aac"
        assert stream.sample_rate == 44100
        assert stream.channels == 2

    def test_absent_bitrate_is_none(self) -> None:
        stream = _parse_stream([
            {"codec_name": "flac", "sample_rate": "44100", "channels": 2}
        ])
        assert stream.bit_rate is None

    def test_no_audio_stream_raises(self) -> None:
        """A video file reaching here would produce a book with no audio."""
        with pytest.raises(FfmpegError, match="no audio stream"):
            _parse_stream([])


class TestChapterMetadata:
    def test_empty_table_is_a_bare_header(self) -> None:
        assert build_chapter_metadata(()) == ";FFMETADATA1\n"

    def test_renders_timebase_start_end_and_title(self) -> None:
        text = build_chapter_metadata((
            Chapter(start_ms=0, end_ms=30_467, title="Opening"),
        ))
        assert "[CHAPTER]" in text
        assert "TIMEBASE=1/1000" in text
        assert "START=0" in text
        assert "END=30467" in text
        assert "title=Opening" in text

    def test_escapes_equals_in_a_title(self) -> None:
        """'Cause = Effect' silently corrupts every chapter after it."""
        text = build_chapter_metadata((
            Chapter(start_ms=0, end_ms=1000, title="Cause = Effect"),
        ))
        assert "title=Cause \\= Effect" in text

    def test_escapes_semicolon_and_hash(self) -> None:
        text = build_chapter_metadata((
            Chapter(start_ms=0, end_ms=1000, title="A; B #1"),
        ))
        assert "\\;" in text
        assert "\\#" in text

    def test_flattens_a_newline_in_a_title(self) -> None:
        """A newline would terminate the key and orphan the rest of the file."""
        text = build_chapter_metadata((Chapter(start_ms=0, end_ms=1000, title="A\nB"),))
        assert "title=A B" in text

    def test_renders_multiple_chapters_in_order(self) -> None:
        text = build_chapter_metadata((
            Chapter(start_ms=0, end_ms=1000, title="One"),
            Chapter(start_ms=1000, end_ms=2000, title="Two"),
        ))
        assert text.count("[CHAPTER]") == 2
        assert text.index("title=One") < text.index("title=Two")


class TestRetryClassification:
    @pytest.mark.parametrize("status", [408, 425, 429, 500, 502, 503, 504])
    def test_transient_statuses_are_retryable(self, status: int) -> None:
        assert is_retryable_status(status)

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
    def test_permanent_statuses_are_not(self, status: int) -> None:
        """A 404 means no chapter table exists; it will not exist in 4 seconds.

        Retrying it turns a clean "no data" into twelve wasted seconds per
        book, which across a 700-book library is over two hours.
        """
        assert not is_retryable_status(status)


#: get_json with the real retry policy but no waiting between attempts.
#:
#: `retry_with` is tenacity's own mechanism for exactly this: it rebuilds the
#: wrapper with one field replaced, so the stop condition and the exception
#: predicate stay the REAL ones -- a test that redefined the whole policy would
#: pass while the shipped policy was wrong.
#:
#: The backoff ARITHMETIC is tenacity's and is not what these tests check; they
#: check WHICH failures retry. Paying the real four seconds proves nothing
#: about that, and it compounds: every retry test added later pays it again,
#: until the suite is slow enough that people stop running it.
_get_json_no_wait = get_json.retry_with(wait=wait_none())


class TestGetJson:
    def test_returns_the_decoded_object(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"asin": "B00TEST"})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            assert get_json(client, "https://example.test/x") == {"asin": "B00TEST"}

    def test_retries_a_transient_failure_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            assert _get_json_no_wait(client, "https://example.test/x") == {"ok": True}
        assert calls["n"] == 3

    def test_gives_up_after_max_attempts(self) -> None:
        calls = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            pytest.raises(RetryableHttpError),
        ):
            _get_json_no_wait(client, "https://example.test/x")
        assert calls["n"] == 3

    def test_does_not_retry_a_404(self) -> None:
        """THE cost control. One call, one answer."""
        calls = {"n": 0}

        def handler(_: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            pytest.raises(httpx.HTTPStatusError),
        ):
            get_json(client, "https://example.test/missing")
        assert calls["n"] == 1

    def test_rejects_a_non_object_payload(self) -> None:
        """A contract change must not read as empty data."""

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3])

        with (
            httpx.Client(transport=httpx.MockTransport(handler)) as client,
            pytest.raises(TypeError, match="expected a JSON object"),
        ):
            get_json(client, "https://example.test/x")


class TestFfmpegError:
    def test_carries_stderr(self) -> None:
        """The exit code alone distinguishes almost nothing."""
        error = FfmpegError("ffprobe", "Invalid data found when processing input")
        assert "Invalid data" in str(error)

    def test_handles_empty_stderr(self) -> None:
        assert "(no stderr)" in str(FfmpegError("ffmpeg", ""))
