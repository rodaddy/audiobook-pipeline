"""Behavior tests for the isolated metadata, cover, and tag boundary."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import httpx
import pytest
from loguru import logger
from mutagen.mp4 import MP4Cover, MP4FreeForm
from pydantic import ValidationError

from audiobook_pipeline.models.metadata import BookMetadata, CoverArt
from audiobook_pipeline.services import audible, cover
from audiobook_pipeline.utils import tagging

JPEG = b"\xff\xd8\xffcover"
PNG = b"\x89PNG\r\n\x1a\ncover"


def _client(handler: httpx.MockTransport) -> httpx.Client:
    """Build a caller-owned client around one deterministic transport."""
    return httpx.Client(transport=handler)


def _response(status: int, content: bytes, content_type: str) -> httpx.Response:
    """Build a fixed mock HTTP response."""
    return httpx.Response(
        status,
        headers={"content-type": content_type},
        stream=httpx.ByteStream(content),
    )


@pytest.mark.parametrize(
    ("content_type", "data"), (("image/jpeg", JPEG), ("image/png", PNG))
)
def test_fetch_cover_accepts_valid_image_bytes(
    content_type: Literal["image/jpeg", "image/png"], data: bytes
) -> None:
    """JPEG and PNG cover responses survive the bounded fetch boundary."""
    with _client(
        httpx.MockTransport(lambda _: _response(200, data, content_type))
    ) as client:
        result = cover.fetch_cover(client, "https://covers.example/art")
    assert result == CoverArt(content_type=content_type, data=data)


@pytest.mark.parametrize(
    ("url", "content_type", "data"),
    (
        ("http://covers.example/art", "image/jpeg", JPEG),
        ("https://covers.example/art", "text/html", JPEG),
        ("https://covers.example/art", "image/jpeg", b"not-an-image"),
    ),
)
def test_fetch_cover_rejects_untrusted_input(
    url: str, content_type: str, data: bytes
) -> None:
    """Wrong schemes, media types, and magic bytes never become cover art."""
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(200, data, content_type)

    with _client(httpx.MockTransport(handler)) as client:
        assert cover.fetch_cover(client, url) is None
    assert calls == (0 if url.startswith("http:") else 1)


def test_fetch_cover_bounds_stream_and_hides_url_from_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversize optional content is skipped without leaking its URL to logs."""
    monkeypatch.setattr(cover, "MAX_COVER_BYTES", 3)
    messages: list[str] = []
    sink_id = logger.add(messages.append, format="{message}")
    try:
        with _client(
            httpx.MockTransport(lambda _: _response(200, JPEG, "image/jpeg"))
        ) as client:
            assert cover.fetch_cover(client, "https://secret.example/private") is None
    finally:
        logger.remove(sink_id)
    assert messages == ["cover fetch rejected: max_bytes\n"]


class _OversizedStream(httpx.SyncByteStream):
    """One raw transport chunk that must be split before the size check."""

    def __iter__(self) -> Iterator[bytes]:
        yield JPEG + b"x" * (cover.COVER_CHUNK_BYTES * 2)


class _RecordingResponse(httpx.Response):
    """Response seam that records the decoded chunk size requested by the code."""

    def __init__(self, stream: httpx.SyncByteStream) -> None:
        super().__init__(200, headers={"content-type": "image/jpeg"}, stream=stream)
        self.requested_chunk_size: int | None = None

    def iter_raw(self, chunk_size: int | None = None) -> Iterator[bytes]:
        self.requested_chunk_size = chunk_size
        yield from super().iter_raw(chunk_size=chunk_size)


def test_fetch_cover_requests_bounded_raw_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized raw stream cannot make the reader request an unlimited chunk."""
    monkeypatch.setattr(cover, "MAX_COVER_BYTES", 3)
    response = _RecordingResponse(_OversizedStream())
    requested_encoding = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested_encoding
        requested_encoding = request.headers["accept-encoding"]
        return response

    with _client(httpx.MockTransport(handler)) as client:
        assert cover.fetch_cover(client, "https://covers.example/art") is None
    assert response.requested_chunk_size == cover.COVER_CHUNK_BYTES
    assert requested_encoding == "identity"


def test_fetch_cover_rejects_encoded_body_before_reading() -> None:
    """A compressed response cannot expand before the cover byte limit runs."""

    class _FailIfReadStream(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            raise AssertionError("encoded cover body was read")

    response = httpx.Response(
        200,
        headers={"content-type": "image/jpeg", "content-encoding": "gzip"},
        stream=_FailIfReadStream(),
    )
    with _client(httpx.MockTransport(lambda _: response)) as client:
        assert cover.fetch_cover(client, "https://covers.example/art") is None


def test_fetch_cover_handles_http_failure_and_keeps_client_open() -> None:
    """The caller retains a usable client after an optional HTTP failure."""
    client = _client(httpx.MockTransport(lambda _: _response(503, b"", "image/jpeg")))
    try:
        assert cover.fetch_cover(client, "https://covers.example/art") is None
        assert not client.is_closed
        assert client.get("https://covers.example/health").status_code == 503
    finally:
        client.close()


def test_fetch_cover_never_follows_https_to_http_redirect() -> None:
    """A caller-wide redirect policy cannot downgrade a cover request."""
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.scheme == "http":
            pytest.fail("cover fetch followed an HTTP redirect")
        return httpx.Response(
            302,
            headers={"location": "http://covers.example/insecure", "content-type": ""},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        assert cover.fetch_cover(client, "https://covers.example/art") is None
        assert requested_urls == ["https://covers.example/art"]
        assert not client.is_closed
    finally:
        client.close()


def test_fetch_cover_skips_empty_url_without_network() -> None:
    """Missing metadata causes no network request at all."""
    client = _client(httpx.MockTransport(lambda _: pytest.fail("network was used")))
    try:
        assert cover.fetch_cover(client, "") is None
    finally:
        client.close()


def test_cover_art_rejects_mismatched_bytes_and_is_frozen() -> None:
    """The model repeats the byte/type check for non-network callers."""
    with pytest.raises(ValidationError):
        CoverArt(content_type="image/png", data=JPEG)
    art = CoverArt(content_type="image/jpeg", data=JPEG)
    with pytest.raises(ValidationError):
        art.data = PNG


def test_audible_maps_preferred_cover_and_expanded_metadata() -> None:
    """The boundary chooses 1024px art and retains tag-relevant fields."""
    metadata = audible._to_metadata({
        "title": "Book",
        "asin": "B000TEST",
        "authors": [{"name": "Author"}],
        "narrators": [{"name": "Narrator"}],
        "series": [{"title": "Series", "sequence": "2"}],
        "release_date": "2020-01-01",
        "publisher_name": "Publisher",
        "publisher_summary": "<p>Summary</p>",
        "copyright": "Copyright",
        "product_images": {"500": "https://small", "1024": "https://large"},
    })
    assert metadata is not None
    assert metadata.cover_url == "https://large"
    assert metadata.publisher == "Publisher"
    assert metadata.summary == "Summary"
    assert metadata.copyright == "Copyright"
    assert audible._cover_url({"500": "https://small"}) == "https://small"


class _FakeMP4:
    """Safe in-memory boundary that exposes the tags Mutagen would receive."""

    def __init__(self) -> None:
        self.tags: dict[str, Any] | None = {}
        self.saved = False

    def add_tags(self) -> None:
        self.tags = {}

    def save(self) -> None:
        self.saved = True


def _tagged(
    monkeypatch: pytest.MonkeyPatch,
    metadata: BookMetadata,
    cover_art: CoverArt | None = None,
) -> _FakeMP4:
    """Write metadata through the fake MP4 boundary and return captured tags."""
    audio = _FakeMP4()
    monkeypatch.setattr(tagging, "MP4", lambda _: audio)
    tagging.write_tags(Path("book.m4b"), metadata, cover=cover_art)
    return audio


def _freeform_value(tags: dict[str, Any], name: str) -> str:
    """Decode one captured iTunes freeform atom."""
    return bytes(tags[tagging._freeform(name)][0]).decode("utf-8")


def test_write_tags_preserves_legacy_series_text_and_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Series output matches the established album/grouping contract."""
    cover_art = CoverArt(content_type="image/png", data=PNG)
    audio = _tagged(
        monkeypatch,
        BookMetadata(
            title="Book",
            author="Author",
            narrator="Narrator",
            asin="B000TEST",
            series="Series",
            series_position="2",
            publisher="Publisher",
            summary="Summary",
            copyright="Copyright",
            release_year=2020,
            genres=("Fiction",),
        ),
        cover_art,
    )
    assert audio.tags is not None
    assert audio.saved
    assert audio.tags["\xa9alb"] == ["Series, Book 2"]
    assert audio.tags["\xa9grp"] == ["Series, Book #2"]
    assert audio.tags["\xa9ART"] == ["Author, Narrator"]
    assert audio.tags["\xa9day"] == ["2020"]
    assert audio.tags["\xa9cmt"] == ["Summary"]
    assert audio.tags["cprt"] == ["Copyright"]
    assert audio.tags["\xa9gen"] == ["Fiction"]
    assert _freeform_value(audio.tags, "ASIN") == "B000TEST"
    assert _freeform_value(audio.tags, "PUBLISHER") == "Publisher"
    assert _freeform_value(audio.tags, "SERIES") == "Series"
    assert _freeform_value(audio.tags, "SERIES-PART") == "2"
    assert audio.tags["\xa9mvi"] == [2]
    embedded = audio.tags["covr"][0]
    assert isinstance(embedded, MP4Cover)
    assert bytes(embedded) == PNG
    assert embedded.imageformat == MP4Cover.FORMAT_PNG


def test_write_tags_omits_absent_fields_and_non_numeric_movement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standalone and fractional-series tags retain their exact text semantics."""
    standalone = _tagged(monkeypatch, BookMetadata(title="Standalone"))
    assert standalone.tags is not None
    assert standalone.tags["\xa9alb"] == ["Standalone"]
    assert tagging._freeform("ASIN") not in standalone.tags
    assert "\xa9grp" not in standalone.tags
    assert "\xa9mvi" not in standalone.tags

    positionless = _tagged(monkeypatch, BookMetadata(title="Book", series="Series"))
    assert positionless.tags is not None
    assert positionless.tags["\xa9alb"] == ["Series"]
    assert positionless.tags["\xa9grp"] == ["Series"]
    assert "\xa9mvi" not in positionless.tags

    series = _tagged(
        monkeypatch,
        BookMetadata(title="Novella", series="Series", series_position="0.5"),
    )
    assert series.tags is not None
    assert series.tags["\xa9alb"] == ["Series, Book 0.5"]
    assert series.tags["\xa9grp"] == ["Series, Book #0.5"]
    assert "\xa9mvi" not in series.tags


def test_read_asin_reads_the_freeform_atom(monkeypatch: pytest.MonkeyPatch) -> None:
    """ASIN verification reads back the atom written by the tag boundary."""
    tags = {
        tagging._freeform("ASIN"): [
            MP4FreeForm(b"B000TEST")  # type: ignore[no-untyped-call]
        ]
    }
    monkeypatch.setattr(tagging, "MP4", lambda _: SimpleNamespace(tags=tags))
    assert tagging.read_asin(Path("book.m4b")) == "B000TEST"
