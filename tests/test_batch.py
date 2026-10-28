"""Focused behavior tests for scheduled conversion batches."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import httpx
import pytest

from audiobook_pipeline.config import Settings
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.scheduling import (
    BatchScheduleResult,
    BookOutcome,
    BookScheduleResult,
    SchedulingOptions,
)
from audiobook_pipeline.models.stage import PipelineMode
from audiobook_pipeline.services import batch
from audiobook_pipeline.services.pipeline import RunContext


def _book(root: Path, name: str) -> BookDirectory:
    """Build a small discovered book without involving filesystem discovery."""
    source = root / name
    return BookDirectory(
        path=root,
        files=(AudioFile(path=source, duration_ms=60_000),),
    )


def _result(outcome: BookOutcome = BookOutcome.SUCCESS) -> BatchScheduleResult:
    """Build one scheduler result for fake coordinator tests."""
    return BatchScheduleResult(
        total=1,
        succeeded=outcome is BookOutcome.SUCCESS,
        failed=outcome is BookOutcome.FAILURE,
        cancelled=outcome is BookOutcome.CANCELLED,
        results=(BookScheduleResult(index=0, source=Path("source"), outcome=outcome),),
    )


class WorkerProbe:
    """Record one worker's resource ownership and thread settings."""

    def __init__(self) -> None:
        """Initialize isolated mutable recording collections."""
        self.opened: list[sqlite3.Connection] = []
        self.closed: list[sqlite3.Connection] = []
        self.clients: list[httpx.Client] = []
        self.seen_threads: list[int] = []

    @contextmanager
    def connect(self, _: Path) -> Iterator[sqlite3.Connection]:
        """Yield a fresh temporary SQLite connection."""
        conn = sqlite3.connect(":memory:")
        self.opened.append(conn)
        try:
            yield conn
        finally:
            conn.close()
            self.closed.append(conn)

    @contextmanager
    def client(self) -> Iterator[httpx.Client]:
        """Yield a fresh HTTP client and close it after work."""
        client = httpx.Client()
        self.clients.append(client)
        try:
            yield client
        finally:
            client.close()

    def complete(self, _: BookDirectory, context: RunContext, **__: object) -> object:
        """Capture worker-local settings and report a completed row."""
        self.seen_threads.append(context.config.encoding.threads)
        return type("Row", (), {"status": "completed"})()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace worker resource and pipeline boundaries with recorders."""
        monkeypatch.setattr(batch, "connect", self.connect)
        monkeypatch.setattr(batch, "build_client", self.client)
        monkeypatch.setattr(batch, "process_book", self.complete)


def test_worker_opens_and_closes_fresh_resources_with_thread_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker owns its DB and HTTP client and receives its scheduler budget."""
    book = _book(tmp_path, "book.m4b")
    settings = Settings()
    probe = WorkerProbe()
    probe.install(monkeypatch)
    factory = batch._worker_factory(
        {book.identity_path: book}, settings, tmp_path, PipelineMode.CONVERT
    )

    assert factory(book.identity_path, 3)() is True
    assert factory(book.identity_path, 5)() is True
    assert probe.seen_threads == [3, 5]
    assert len({id(conn) for conn in probe.opened}) == 2
    assert probe.closed == probe.opened
    assert all(client.is_closed for client in probe.clients)


def test_run_batch_uses_configured_scheduler_options_and_input_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The adapter preserves discovery order while mapping the two CPU settings."""
    books = (_book(tmp_path, "first.m4b"), _book(tmp_path, "second.m4b"))
    settings = Settings()
    settings.encoding.max_parallel_converts = 2
    settings.encoding.cpu_ceiling = 65
    seen: dict[str, object] = {}

    class Coordinator:
        """Return a deterministic result without starting executor workers."""

        def run(self) -> BatchScheduleResult:
            """Return a complete two-book scheduler receipt."""
            return BatchScheduleResult(
                total=2,
                succeeded=2,
                failed=0,
                cancelled=0,
                results=tuple(
                    BookScheduleResult(
                        index=index,
                        source=book.identity_path,
                        outcome=BookOutcome.SUCCESS,
                    )
                    for index, book in enumerate(books)
                ),
            )

        def shutdown(self) -> BatchScheduleResult:
            """Return the same receipt when an interrupt is not involved."""
            return self.run()

    class Scheduler:
        """Capture construction and inputs for the adapter boundary."""

        def __init__(self, options: SchedulingOptions) -> None:
            """Keep the resolved scheduler options for assertions."""
            seen["options"] = options

        def start(self, sources: tuple[Path, ...], factory: object) -> Coordinator:
            """Capture deterministic source order and return the fake coordinator."""
            seen["sources"] = sources
            seen["factory"] = factory
            return Coordinator()

    monkeypatch.setattr(batch, "BatchScheduler", Scheduler)

    result = batch.run_batch(books, settings, tmp_path, PipelineMode.CONVERT)

    options = cast(SchedulingOptions, seen["options"])
    assert options.max_workers == 2
    assert options.cpu_ceiling_pct == 65
    assert seen["sources"] == tuple(book.identity_path for book in books)
    assert result.succeeded == 2


def test_interrupt_cancels_queued_work_and_waits_for_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KeyboardInterrupt delegates to the scheduler's bounded shutdown contract."""
    shutdown = _result(BookOutcome.CANCELLED)

    class Coordinator:
        """Raise on run and expose the shutdown receipt."""

        def run(self) -> BatchScheduleResult:
            """Model an interrupt while a batch is active."""
            raise KeyboardInterrupt

        def shutdown(self) -> BatchScheduleResult:
            """Return the terminal accounting after cancellation."""
            return shutdown

    class Scheduler:
        """Provide the interrupting coordinator."""

        def __init__(self, _: object) -> None:
            """Accept scheduler options."""

        def start(self, _: tuple[Path, ...], __: object) -> Coordinator:
            """Return the interrupting coordinator."""
            return Coordinator()

    monkeypatch.setattr(batch, "BatchScheduler", Scheduler)

    result = batch.run_batch(
        (_book(tmp_path, "book.m4b"),), Settings(), tmp_path, PipelineMode.CONVERT
    )

    assert result == shutdown


def test_duplicate_identity_fails_before_scheduler_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The batch boundary refuses map-overwrite/process-twice input explicitly."""
    book = _book(tmp_path, "book.m4b")
    changed_duration = book.model_copy(
        update={"files": (AudioFile(path=book.identity_path, duration_ms=60_001),)}
    )
    monkeypatch.setattr(
        batch,
        "BatchScheduler",
        lambda *_: (_ for _ in ()).throw(AssertionError("scheduler constructed")),
    )

    with pytest.raises(batch.DuplicateBatchIdentityError):
        batch.run_batch(
            (book, changed_duration), Settings(), tmp_path, PipelineMode.CONVERT
        )


def test_resolved_aliases_are_duplicate_batch_identities(tmp_path: Path) -> None:
    """Lexical aliases cannot bypass duplicate detection and process twice."""
    book = _book(tmp_path, "book.m4b")
    alias = BookDirectory(
        path=tmp_path / "alias" / "..",
        files=(
            AudioFile(
                path=tmp_path / "alias" / ".." / "book.m4b",
                duration_ms=60_001,
            ),
        ),
    )

    with pytest.raises(batch.DuplicateBatchIdentityError):
        batch.run_batch((book, alias), Settings(), tmp_path, PipelineMode.CONVERT)
