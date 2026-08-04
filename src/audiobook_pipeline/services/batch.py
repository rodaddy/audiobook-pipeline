"""Run one discovered conversion batch with isolated worker resources."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from pathlib import Path

from loguru import logger

from audiobook_pipeline.config import Settings
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.models.book import BookDirectory
from audiobook_pipeline.models.index import IndexOptions, SourceIdentity
from audiobook_pipeline.models.scheduling import (
    BatchScheduleResult,
    BookOutcome,
    SchedulingOptions,
    WorkerFactory,
)
from audiobook_pipeline.models.stage import PipelineMode
from audiobook_pipeline.services.index import (
    LibraryIndex,
    SQLiteIndexStore,
    sqlite_connection_factory,
)
from audiobook_pipeline.services.pipeline import RunContext, process_book
from audiobook_pipeline.services.scheduler import BatchScheduler
from audiobook_pipeline.utils.http import build_client


class DuplicateBatchIdentityError(ValueError):
    """The caller supplied multiple books with one scheduler identity."""

    def __init__(self) -> None:
        """Build a stable error without exposing a source path."""
        super().__init__("batch contains duplicate book identities")


def scheduler_options(settings: Settings) -> SchedulingOptions:
    """Map explicit conversion settings onto the scheduler's resource contract."""
    return SchedulingOptions(
        max_workers=settings.encoding.max_parallel_converts,
        cpu_ceiling_pct=settings.encoding.cpu_ceiling,
    )


def _worker_settings(settings: Settings, threads: int) -> Settings:
    """Copy settings for one worker without mutating shared batch configuration."""
    encoding = settings.encoding.model_copy(update={"threads": threads})
    return settings.model_copy(update={"encoding": encoding})


def _run_book(
    book: BookDirectory,
    threads: int,
    settings: Settings,
    source_root: Path,
    mode: PipelineMode,
) -> bool:
    """Create thread-local resources and process one scheduled book."""
    worker_settings = _worker_settings(settings, threads)
    with connect(worker_settings.paths.db_path) as conn, build_client() as client:
        index = _worker_index(worker_settings)
        context = RunContext(
            config=worker_settings,
            conn=conn,
            client=client,
            source_root=source_root,
            library_index=index,
        )
        return process_book(book, context, mode=mode).status == "completed"


def _worker_factory(
    books: dict[Path, BookDirectory],
    settings: Settings,
    source_root: Path,
    mode: PipelineMode,
) -> WorkerFactory:
    """Resolve a scheduler identity into a thread-local pipeline worker."""
    return lambda source, threads: partial(
        _run_book,
        books[source],
        threads,
        settings,
        source_root,
        mode,
    )


def _books_by_identity(books: Sequence[BookDirectory]) -> dict[Path, BookDirectory]:
    """Build a scheduler lookup only when every input identity is unique."""
    identities = {book.identity_path.resolve(): book for book in books}
    if len(identities) != len(books):
        raise DuplicateBatchIdentityError
    return identities


def _index_database_path(settings: Settings) -> Path:
    """Name the separate durable library index beside the pipeline database."""
    return settings.paths.db_path.resolve().with_name("library-index.sqlite3")


def _new_index(settings: Settings) -> LibraryIndex:
    """Create one non-shared index coordinator over the batch's shared database."""
    options = IndexOptions()
    library_root = settings.paths.library_dir.resolve()
    store = SQLiteIndexStore(
        sqlite_connection_factory(_index_database_path(settings), options), options
    )
    return LibraryIndex(library_root, store)


def _worker_index(settings: Settings) -> LibraryIndex:
    """Create fresh worker-local index objects with no shared SQLite connection."""
    return _new_index(settings)


def _deduplicate_sources(
    books: Sequence[BookDirectory], index: LibraryIndex
) -> tuple[BookDirectory, ...]:
    """Keep resolved-source deduplication at the sequential batch coordinator."""
    return tuple(
        book
        for book in books
        if not index.mark_processed(
            SourceIdentity(stem=str(book.identity_path.resolve()))
        )
    )


def run_batch(
    books: Sequence[BookDirectory],
    settings: Settings,
    source_root: Path,
    mode: PipelineMode,
) -> BatchScheduleResult:
    """Schedule all books and account deterministically for every terminal result."""
    _books_by_identity(books)
    index = _new_index(settings)
    index.scan()
    unique_books = _deduplicate_sources(books, index)
    identities = _books_by_identity(unique_books)
    scheduler = BatchScheduler(scheduler_options(settings))
    coordinator = scheduler.start(
        tuple(book.identity_path.resolve() for book in unique_books),
        _worker_factory(identities, settings, source_root, mode),
    )
    try:
        return coordinator.run()
    except KeyboardInterrupt:
        logger.warning("batch_scheduler_interrupted")
        return coordinator.shutdown()


def statuses(result: BatchScheduleResult) -> list[str]:
    """Translate scheduler terminal outcomes into the CLI's stable summary states."""
    return [
        "completed" if item.outcome is BookOutcome.SUCCESS else "failed"
        for item in result.results
    ]
