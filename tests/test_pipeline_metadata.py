"""Metadata lifecycle tests kept separate from the pipeline spine contract."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from mutagen import MutagenError

from audiobook_pipeline.config import Settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.db.rows import BookRow
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import ChapterSet
from audiobook_pipeline.models.lifecycle import (
    ArchivedSource,
    CleanupResult,
    ValidatedBook,
)
from audiobook_pipeline.models.metadata import BookMetadata, CoverArt
from audiobook_pipeline.models.stage import PipelineLevel, StageStatus
from audiobook_pipeline.services import (
    ai_selection,
    archive,
    audible,
    cleanup,
    concat,
    convert,
    metadata_stage,
    organize,
    pipeline,
    validate,
)
from audiobook_pipeline.services.pipeline import RunContext, book_hash, process_book


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[RunContext]:
    """Return a network-isolated context with a disposable initialized database."""
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


class MetadataFakes:
    """Minimal observable stage implementations for one-file lifecycle tests."""

    def __init__(self) -> None:
        """Initialize observable counters for each replaced lifecycle boundary."""
        self.calls = {
            "validate": 0,
            "convert": 0,
            "tag": 0,
            "place": 0,
            "archive": 0,
            "cleanup": 0,
        }

    def validate_book(
        self, book: BookDirectory, _: object, __: object, ___: str
    ) -> ValidatedBook:
        self.calls["validate"] += 1
        return ValidatedBook(
            book=book, file_list=book.files[0].path, target_bitrate_kbps=64
        )

    def convert_to_m4b(
        self, _: Path, output: Path, __: ChapterSet, ___: object, **_kwargs: object
    ) -> Path:
        self.calls["convert"] += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"converted")
        return output

    def write_tags(
        self, _: Path, __: BookMetadata, *, cover: CoverArt | None = None
    ) -> None:
        self.calls["tag"] += 1

    def place_book(self, source: Path, destination: Path, **_: object) -> Path:
        self.calls["place"] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return destination

    def archive_source(
        self, source: Path, _: Path, root: Path, **_kwargs: object
    ) -> ArchivedSource:
        self.calls["archive"] += 1
        return ArchivedSource(
            source_path=source, archive_path=root / source.name, original_count=1
        )

    def cleanup_work_dir(
        self, work_root: Path, book_hash: str, *, enabled: bool, dry_run: bool
    ) -> CleanupResult:
        self.calls["cleanup"] += 1
        return CleanupResult(
            work_dir=work_root / book_hash, removed=False, attempted=True
        )

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(validate, "validate_book", self.validate_book)
        monkeypatch.setattr(convert, "convert_to_m4b", self.convert_to_m4b)
        monkeypatch.setattr(pipeline, "write_tags", self.write_tags)
        monkeypatch.setattr(organize, "place_book", self.place_book)
        monkeypatch.setattr(archive, "archive_source", self.archive_source)
        monkeypatch.setattr(cleanup, "cleanup_work_dir", self.cleanup_work_dir)
        monkeypatch.setattr(
            concat, "build_chapters", lambda _: ChapterSet(source="files")
        )


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> MetadataFakes:
    """Install isolated stage fakes and expose their observable counters."""
    result = MetadataFakes()
    result.install(monkeypatch)
    return result


def book(tmp_path: Path) -> BookDirectory:
    """Create one on-disk source file with a declared audio duration."""
    source = tmp_path / "source" / "Some Book" / "solo.mp3"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"audio")
    return BookDirectory(
        path=source.parent,
        files=(AudioFile(path=source, duration_ms=60_000),),
    )


def cover_client(requests: list[httpx.Request]) -> httpx.Client:
    """Serve one valid JPEG and retain the only request made through the client."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            stream=httpx.ByteStream(b"\xff\xd8\xffcover"),
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_direct_dry_run_skips_every_side_effect(
    tmp_path: Path, context: RunContext, fakes: MetadataFakes
) -> None:
    """The direct API bypasses persistence, network, and every lifecycle stage."""
    context.config.dry_run = True
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("network was used"))
    )
    active = context.model_copy(update={"client": client})
    source = book(tmp_path)
    try:
        row = process_book(source, active)
    finally:
        client.close()
    assert row.status == StageStatus.SKIPPED.value and row.error_category is None
    assert queries.get_book(active.conn, row.book_hash) is None
    assert queries.get_stages(active.conn, row.book_hash) == []
    assert not any(fakes.calls.values())
    assert not context.config.paths.work_dir.exists()


def test_cover_is_cached_before_a_tag_failure_and_reused_on_retry(
    tmp_path: Path,
    context: RunContext,
    fakes: MetadataFakes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tagging retries reuse cache rather than fetching optional art twice."""
    requests: list[httpx.Request] = []
    covers: list[CoverArt | None] = []
    candidate = BookMetadata(title="Some Book", cover_url="https://covers.example/art")

    def fail_once(_: Path, __: BookMetadata, *, cover: CoverArt | None) -> None:
        covers.append(cover)
        if len(covers) == 1:
            raise MutagenError("tag failed")

    client = cover_client(requests)
    active = context.model_copy(update={"client": client})
    monkeypatch.setattr(audible, "search", lambda *_: [candidate])
    monkeypatch.setattr(
        concat, "build_chapters", lambda _: ChapterSet(source=concat.SOURCE_EMBEDDED)
    )
    monkeypatch.setattr(pipeline, "write_tags", fail_once)
    source = book(tmp_path)
    try:
        assert process_book(source, active).status == "failed"
        assert queries.get_cover(active.conn, book_hash(source)) == b"\xff\xd8\xffcover"
        assert process_book(source, active).status == "completed"
        assert len(requests) == 1 and all(cover is not None for cover in covers)
        assert not client.is_closed
    finally:
        client.close()


def test_invalid_cached_cover_is_refetched_before_tagging(
    tmp_path: Path, context: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invalid cache bytes are replaced before they can reach Mutagen."""
    source = book(tmp_path)
    row = BookRow(
        book_hash=book_hash(source),
        source_path=str(source.identity_path),
        mode="convert",
    )
    queries.upsert_book(context.conn, row)
    queries.store_cover(context.conn, row.book_hash, b"not-an-image")
    fetched = CoverArt(content_type="image/png", data=b"\x89PNG\r\n\x1a\ncover")
    monkeypatch.setattr(metadata_stage, "fetch_cover", lambda *_: fetched)
    result = metadata_stage.cover_for_metadata(
        context.conn,
        context.client,
        row.book_hash,
        BookMetadata(title="Book", cover_url="https://covers.example/art"),
    )
    assert result == fetched
    assert queries.get_cover(context.conn, row.book_hash) == fetched.data


def test_level_change_reidentifies_an_incomplete_book_with_ai(
    tmp_path: Path,
    context: RunContext,
    fakes: MetadataFakes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changing NORMAL to AI reopens ASIN and conversion after a failed tag."""
    calls = 0
    tags = 0
    candidate = BookMetadata(title="Some Book", asin="ASIN")

    def fail_once(_: Path, __: BookMetadata, *, cover: CoverArt | None) -> None:
        nonlocal tags
        tags += 1
        if tags == 1:
            raise MutagenError("tag failed")

    def selected(resolver: object | None, *_: object) -> None:
        nonlocal calls
        if resolver is not None:
            calls += 1

    monkeypatch.setattr(audible, "search", lambda *_: [candidate])
    monkeypatch.setattr(
        concat, "build_chapters", lambda _: ChapterSet(source=concat.SOURCE_EMBEDDED)
    )
    monkeypatch.setattr(pipeline, "write_tags", fail_once)
    monkeypatch.setattr(
        ai_selection,
        "resolver_for",
        lambda config, *_: (
            (object(), False) if config.level is PipelineLevel.AI else (None, False)
        ),
    )
    monkeypatch.setattr(ai_selection, "selected_match", selected)
    source = book(tmp_path)
    assert process_book(source, context).status == "failed"
    context.config.ai.base_url = "https://provider.test"
    context.config.level = PipelineLevel.AI
    assert process_book(source, context).status == "completed"
    assert calls == 1 and fakes.calls["convert"] == 2


def test_unchanged_level_retry_reuses_its_asin_decision(
    tmp_path: Path,
    context: RunContext,
    fakes: MetadataFakes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same AI level resumes metadata work without calling its resolver."""
    calls = 0
    tags = 0
    context.config.ai.base_url = "https://provider.test"
    context.config.level = PipelineLevel.AI
    candidate = BookMetadata(title="Some Book", asin="ASIN")

    def fail_once(_: Path, __: BookMetadata, *, cover: CoverArt | None) -> None:
        nonlocal tags
        tags += 1
        if tags == 1:
            raise MutagenError("tag failed")

    def selected(*_: object) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(audible, "search", lambda *_: [candidate])
    monkeypatch.setattr(
        concat, "build_chapters", lambda _: ChapterSet(source=concat.SOURCE_EMBEDDED)
    )
    monkeypatch.setattr(pipeline, "write_tags", fail_once)
    monkeypatch.setattr(ai_selection, "resolver_for", lambda *_: (object(), False))
    monkeypatch.setattr(ai_selection, "selected_match", selected)
    source = book(tmp_path)
    assert process_book(source, context).status == "failed"
    calls = 0
    assert process_book(source, context).status == "completed"
    assert calls == 0 and fakes.calls["convert"] == 1


def test_level_change_does_not_reset_an_archive_only_retry(
    tmp_path: Path,
    context: RunContext,
    fakes: MetadataFakes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Archive-only recovery stays terminal even when the requested level changes."""
    source = book(tmp_path)

    def refuse(*_: object, **__: object) -> ArchivedSource:
        raise archive.ArchiveError(context.config.paths.archive_dir)

    with monkeypatch.context() as patch:
        patch.setattr(archive, "archive_source", refuse)
        assert process_book(source, context).status == "failed"
    context.config.ai.base_url = "https://provider.test"
    context.config.level = PipelineLevel.AI
    assert process_book(source, context).status == "completed"
    assert fakes.calls["convert"] == 1 and fakes.calls["tag"] == 1
