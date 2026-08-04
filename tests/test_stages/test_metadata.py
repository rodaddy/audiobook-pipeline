"""Legacy metadata-stage guards mapped to the typed rewrite boundaries."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from mutagen import MutagenError
from mutagen.mp4 import MP4Cover

from audiobook_pipeline.config import Settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.db.rows import BookRow
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import ChapterSet
from audiobook_pipeline.models.metadata import BookMetadata, CoverArt
from audiobook_pipeline.models.stage import (
    PipelineLevel,
    PipelineMode,
    Stage,
    StageStatus,
    stages_for,
)
from audiobook_pipeline.services import metadata_stage
from audiobook_pipeline.services.pipeline import RunContext, book_hash, process_book
from audiobook_pipeline.utils import tagging

JPEG = b"\xff\xd8\xffcover"
PNG = b"\x89PNG\r\n\x1a\ncover"


class FakeMP4:
    """In-memory MP4 boundary exposing tags written by the public tag service."""

    def __init__(self) -> None:
        """Initialize an empty writable tag block."""
        self.tags: dict[str, Any] | None = {}
        self.saved = False

    def add_tags(self) -> None:
        """Create a tag block when the writer needs one."""
        self.tags = {}

    def save(self) -> None:
        """Record the durable write boundary."""
        self.saved = True


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RunContext]:
    """Return an isolated current pipeline context and caller-owned client."""
    for key in list(os.environ):
        if key.startswith("AUDIOBOOK_"):
            monkeypatch.delenv(key, raising=False)
    settings = Settings()
    settings.paths.work_dir = tmp_path / "work"
    settings.paths.library_dir = tmp_path / "library"
    with connect(tmp_path / "pipeline.db") as conn:
        client = httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(404, json={}))
        )
        yield RunContext(config=settings, conn=conn, client=client)
        client.close()


def source_book(tmp_path: Path) -> BookDirectory:
    """Create one source book with the typed duration required by the pipeline."""
    source = tmp_path / "source" / "Book" / "book.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    return BookDirectory(
        path=source.parent,
        files=(AudioFile(path=source, duration_ms=60_000),),
    )


def tagged(
    monkeypatch: pytest.MonkeyPatch,
    metadata: BookMetadata,
    cover: CoverArt | None = None,
) -> FakeMP4:
    """Write typed metadata through a fake MP4 and return its captured atoms."""
    audio = FakeMP4()
    monkeypatch.setattr(tagging, "MP4", lambda _: audio)
    tagging.write_tags(Path("book.m4b"), metadata, cover=cover)
    return audio


def freeform(tags: dict[str, Any], name: str) -> str:
    """Decode one iTunes freeform value captured from the tag boundary."""
    return bytes(tags[tagging._freeform(name)][0]).decode("utf-8")


def test_tags_preserve_series_asin_and_cover_art(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rewritten writer retains the established Plex/Apple tag contract."""
    audio = tagged(
        monkeypatch,
        BookMetadata(
            title="Leviathan Wakes",
            author="James Corey",
            narrator="Jefferson Mays",
            asin="B005LZHV6Q",
            series="The Expanse",
            series_position="1",
            release_year=2011,
            publisher="Publisher",
            summary="Summary",
            copyright="Copyright",
            genres=("Audiobook",),
        ),
        CoverArt(content_type="image/png", data=PNG),
    )
    assert audio.tags is not None and audio.saved
    tags = audio.tags
    assert tags["\xa9ART"] == ["James Corey, Jefferson Mays"]
    assert tags["\xa9alb"] == ["The Expanse, Book 1"]
    assert tags["\xa9grp"] == ["The Expanse, Book #1"]
    assert tags["\xa9day"] == ["2011"]
    assert tags["\xa9mvi"] == [1]
    assert tags["stik"] == [2] and tags["pgap"] is True
    assert freeform(tags, "ASIN") == "B005LZHV6Q"
    assert freeform(tags, "PUBLISHER") == "Publisher"
    embedded = tags["covr"][0]
    assert isinstance(embedded, MP4Cover)
    assert bytes(embedded) == PNG
    assert embedded.imageformat == MP4Cover.FORMAT_PNG


def test_tags_omit_absent_atoms_and_keep_fractional_series_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ASIN means no atom, and a decimal position is not coerced to int."""
    standalone = tagged(monkeypatch, BookMetadata(title="Standalone"))
    assert standalone.tags is not None
    assert standalone.tags["\xa9alb"] == ["Standalone"]
    assert tagging._freeform("ASIN") not in standalone.tags
    fractional = tagged(
        monkeypatch,
        BookMetadata(title="Novella", series="Series", series_position="0.5"),
    )
    assert fractional.tags is not None
    assert fractional.tags["\xa9alb"] == ["Series, Book 0.5"]
    assert "\xa9mvi" not in fractional.tags


def test_tag_writer_keeps_mutagen_failure_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed MP4 is a real tagging failure for the pipeline to record."""
    monkeypatch.setattr(
        tagging,
        "MP4",
        lambda _: (_ for _ in ()).throw(MutagenError("bad container")),
    )
    with pytest.raises(MutagenError, match="bad container"):
        tagging.write_tags(Path("bad.m4b"), BookMetadata(title="Book"))


def test_cover_download_is_cached_then_reused_without_a_second_request(
    context: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Optional cover art is stored before tagging so a retry needs no network."""
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            stream=httpx.ByteStream(JPEG),
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    row = BookRow(book_hash="cover", source_path="/source", mode="convert")
    queries.upsert_book(context.conn, row)
    metadata = BookMetadata(title="Book", cover_url="https://covers.example/art")
    try:
        first = metadata_stage.cover_for_metadata(
            context.conn, client, row.book_hash, metadata
        )
        second = metadata_stage.cover_for_metadata(
            context.conn, client, row.book_hash, metadata
        )
    finally:
        client.close()
    assert first == CoverArt(content_type="image/jpeg", data=JPEG)
    assert second == first
    assert queries.get_cover(context.conn, row.book_hash) == JPEG
    assert calls == 1


def test_invalid_cached_cover_refetches_and_optional_failure_is_nonfatal(
    context: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bad cache bytes are replaced; a remote failure safely leaves cover absent."""
    row = BookRow(book_hash="cover", source_path="/source", mode="convert")
    queries.upsert_book(context.conn, row)
    queries.store_cover(context.conn, row.book_hash, b"not-an-image")
    art = CoverArt(content_type="image/jpeg", data=JPEG)
    monkeypatch.setattr(metadata_stage, "fetch_cover", lambda *_: art)
    metadata = BookMetadata(title="Book", cover_url="https://covers.example/art")
    assert (
        metadata_stage.cover_for_metadata(
            context.conn, context.client, row.book_hash, metadata
        )
        == art
    )
    assert queries.get_cover(context.conn, row.book_hash) == JPEG
    monkeypatch.setattr(metadata_stage, "fetch_cover", lambda *_: None)
    assert (
        metadata_stage.cover_for_metadata(
            context.conn,
            context.client,
            "missing",
            BookMetadata(title="Book", cover_url="https://covers.example/fail"),
        )
        is None
    )


def test_metadata_resume_round_trip_preserves_tag_and_cover_values() -> None:
    """A retry reconstructs all values the tag boundary needs from its row."""
    metadata = BookMetadata(
        title="Title",
        author="Author",
        narrator="Narrator",
        asin="ASIN",
        series="Series",
        series_position="2",
        release_year=2024,
        publisher="Publisher",
        summary="Summary",
        copyright="Copyright",
        genres=("Fiction", "Space Opera"),
        cover_url="https://covers.example/art",
    )
    row = BookRow(book_hash="hash", source_path="/source", mode="convert")
    completed = metadata_stage.completed_row(row, metadata, ChapterSet())
    assert metadata_stage.metadata_from_row(completed) == metadata


def test_direct_dry_run_skips_database_network_and_tagging(
    tmp_path: Path, context: RunContext
) -> None:
    """The direct current API has no metadata side effects in dry-run mode."""
    context.config.dry_run = True
    book = source_book(tmp_path)
    row = process_book(book, context)
    assert row.status == StageStatus.SKIPPED.value
    assert row.error_category is None
    assert queries.get_book(context.conn, book_hash(book)) is None
    assert not context.config.paths.work_dir.exists()


def test_organize_is_placement_only_after_the_rewrite() -> None:
    """Legacy organize no longer re-fetches metadata or rewrites tags."""
    assert stages_for(PipelineMode.ORGANIZE, PipelineLevel.NORMAL) == (Stage.ORGANIZE,)
