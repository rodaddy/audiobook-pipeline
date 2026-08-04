"""Fetch bounded, validated cover art for an already-resolved metadata URL."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

import httpx
from loguru import logger
from pydantic import ValidationError

from audiobook_pipeline.models.metadata import CoverArt

log = logger.bind(stage="cover")

COVER_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
MAX_COVER_BYTES = 10 * 1024 * 1024
COVER_CHUNK_BYTES = 64 * 1024
IMAGE_CONTENT_TYPES = frozenset(("image/jpeg", "image/png"))


def _is_https_url(url: str) -> bool:
    """Whether a cover URL names an HTTPS network location."""
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _content_type(response: httpx.Response) -> str:
    """Return the normalized media type without any header parameters."""
    value = str(response.headers.get("content-type", ""))
    return value.split(";", 1)[0].lower()


def _content_encoding(response: httpx.Response) -> str:
    """Return the normalized content encoding supplied by the server."""
    return str(response.headers.get("content-encoding", "")).strip().lower()


def _read_image(response: httpx.Response) -> bytes | None:
    """Read at most ``MAX_COVER_BYTES`` from a streaming response."""
    chunks: list[bytes] = []
    size = 0
    for chunk in response.iter_raw(chunk_size=COVER_CHUNK_BYTES):
        size += len(chunk)
        if size > MAX_COVER_BYTES:
            log.warning("cover fetch rejected: max_bytes")
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_cover(client: httpx.Client, url: str) -> CoverArt | None:
    """Fetch an HTTPS JPEG or PNG without taking ownership of ``client``.

    Optional cover art must never turn an otherwise valid book into a failed
    conversion, so expected remote failures are logged as stable reason codes
    and return ``None``.  The URL and response body are deliberately never
    logged because both are external input.
    """
    if not url:
        return None
    if not _is_https_url(url):
        log.warning("cover fetch rejected: scheme")
        return None
    try:
        with client.stream(
            "GET",
            url,
            headers={"accept-encoding": "identity"},
            timeout=COVER_TIMEOUT,
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            if response.is_redirect:
                log.warning("cover fetch rejected: redirect")
                return None
            if _content_encoding(response) not in {"", "identity"}:
                log.warning("cover fetch rejected: content_encoding")
                return None
            content_type = _content_type(response)
            if content_type not in IMAGE_CONTENT_TYPES:
                log.warning("cover fetch rejected: content_type")
                return None
            data = _read_image(response)
    except httpx.HTTPError:
        log.warning("cover fetch failed: http")
        return None
    if data is None:
        return None
    cover_type: Literal["image/jpeg", "image/png"] = (
        "image/jpeg" if content_type == "image/jpeg" else "image/png"
    )
    try:
        return CoverArt(content_type=cover_type, data=data)
    except ValidationError:
        log.warning("cover fetch rejected: image_magic")
        return None
