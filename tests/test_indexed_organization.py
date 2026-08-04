"""Integration behavior for durable-index library organization."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from audiobook_pipeline.config import Settings
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.index import (
    ClaimState,
    DestinationIdentity,
    IndexOptions,
    SourceIdentity,
)
from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.scheduling import (
    BatchScheduleResult,
    BookOutcome,
    BookScheduleResult,
    SchedulingOptions,
)
from audiobook_pipeline.models.stage import PipelineMode
from audiobook_pipeline.services import batch, indexed_organization, organize
from audiobook_pipeline.services.index import (
    LibraryIndex,
    SQLiteIndexStore,
    sqlite_connection_factory,
)
from audiobook_pipeline.services.indexed_organization import place_indexed_book


def _index(root: Path, database: Path) -> LibraryIndex:
    options = IndexOptions(busy_timeout_ms=2_000)
    return LibraryIndex(
        root, SQLiteIndexStore(sqlite_connection_factory(database, options), options)
    )


def _source(path: Path, content: bytes = b"m4b") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _destination(root: Path) -> Path:
    return root / "R. A. Salvatore" / "Homeland" / "Homeland.m4b"


def _settings(tmp_path: Path) -> Settings:
    settings = Settings()
    settings.paths.work_dir = tmp_path / "work"
    settings.paths.library_dir = tmp_path / "library"
    return settings


def test_claimed_placement_verifies_and_commits(tmp_path: Path) -> None:
    library = tmp_path / "library"
    index = _index(library, tmp_path / "state" / "index.sqlite3")
    index.scan()
    source = _source(tmp_path / "work" / "book.m4b")
    final = place_indexed_book(
        index, source, _destination(library), SourceIdentity(stem="book")
    )
    assert final == _destination(library)
    assert final.read_bytes() == b"m4b"
    assert not source.exists()
    assert index.file_exists(
        DestinationIdentity(directory=final.parent, filename=final.name)
    )


def test_registered_collision_claims_and_writes_the_same_suffix(tmp_path: Path) -> None:
    library = tmp_path / "library"
    desired = _destination(library)
    _source(desired, b"original")
    index = _index(library, tmp_path / "state" / "index.sqlite3")
    index.scan()
    source = _source(tmp_path / "work" / "book.m4b", b"new")
    final = place_indexed_book(index, source, desired, SourceIdentity(stem="book"))
    assert final.name == "Homeland (2).m4b"
    assert desired.read_bytes() == b"original"
    assert final.read_bytes() == b"new"


def test_concurrent_indexes_claim_distinct_exact_destinations(tmp_path: Path) -> None:
    library = tmp_path / "library"
    database = tmp_path / "state" / "index.sqlite3"
    first, second = _index(library, database), _index(library, database)
    first.scan()
    desired = _destination(library)
    sources = (
        _source(tmp_path / "work" / "one.m4b"),
        _source(tmp_path / "work" / "two.m4b"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        finals = tuple(
            pool.map(
                lambda values: place_indexed_book(
                    values[0], values[1], desired, SourceIdentity(stem=values[2])
                ),
                ((first, sources[0], "one"), (second, sources[1], "two")),
            )
        )
    assert {final.name for final in finals} == {"Homeland.m4b", "Homeland (2).m4b"}
    assert all(final.is_file() for final in finals)


def test_placement_failure_releases_the_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    index = _index(library, tmp_path / "state" / "index.sqlite3")
    index.scan()
    desired = _destination(library)
    monkeypatch.setattr(indexed_organization, "place_claimed_book", _raise_placement)
    with pytest.raises(OSError):
        place_indexed_book(
            index,
            _source(tmp_path / "work" / "book.m4b"),
            desired,
            SourceIdentity(stem="book"),
        )
    assert (
        index.claim(_identity(desired), SourceIdentity(stem="retry"), _now()).state
        is ClaimState.CLAIMED
    )


def test_commit_failure_releases_the_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    index = _index(library, tmp_path / "state" / "index.sqlite3")
    index.scan()
    desired = _destination(library)
    source = _source(tmp_path / "work" / "book.m4b")
    monkeypatch.setattr(index, "commit", _commit_failure)
    with pytest.raises(OSError):
        place_indexed_book(
            index,
            source,
            desired,
            SourceIdentity(stem="book"),
        )
    assert source.exists()
    assert not desired.exists()
    assert (
        index.claim(_identity(desired), SourceIdentity(stem="retry"), _now()).state
        is ClaimState.CLAIMED
    )


def test_post_commit_source_cleanup_failure_keeps_committed_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    index = _index(library, tmp_path / "state" / "index.sqlite3")
    index.scan()
    source = _source(tmp_path / "work" / "book.m4b")
    monkeypatch.setattr(indexed_organization, "_consume_source", _raise_cleanup)
    final = place_indexed_book(
        index, source, _destination(library), SourceIdentity(stem="book")
    )
    assert final.is_file()
    assert source.exists()
    assert index.file_exists(_identity(final))


def test_index_build_path_reuses_existing_author_folder(tmp_path: Path) -> None:
    library = tmp_path / "library"
    (library / "R. A. Salvatore").mkdir(parents=True)
    index = _index(library, tmp_path / "state" / "index.sqlite3")
    index.scan()
    path = organize.build_library_path(
        library,
        BookMetadata(title="Homeland", author="R.A. Salvatore"),
        index=index,
    )
    assert path.parts[-3] == "R. A. Salvatore"


def test_batch_scans_once_and_worker_indexes_are_fresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    scans: list[LibraryIndex] = []
    original_scan = LibraryIndex.scan

    def scan_once(index: LibraryIndex) -> None:
        scans.append(index)
        original_scan(index)

    class Coordinator:
        def run(self) -> BatchScheduleResult:
            return _batch_result()

        def shutdown(self) -> BatchScheduleResult:
            return self.run()

    class Scheduler:
        def __init__(self, _: SchedulingOptions) -> None:
            pass

        def start(self, _: tuple[Path, ...], __: object) -> Coordinator:
            return Coordinator()

    monkeypatch.setattr(LibraryIndex, "scan", scan_once)
    monkeypatch.setattr(batch, "BatchScheduler", Scheduler)
    batch.run_batch((_book(tmp_path),), settings, tmp_path, PipelineMode.CONVERT)
    first, second = batch._worker_index(settings), batch._worker_index(settings)
    assert len(scans) == 1
    assert first is not second
    assert first._store is not second._store


def test_batch_schedules_distinct_resolved_paths_with_the_same_stem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    scheduled: list[Path] = []
    books = (_book(tmp_path / "first"), _book(tmp_path / "second"))

    class Coordinator:
        def run(self) -> BatchScheduleResult:
            return _batch_result()

        def shutdown(self) -> BatchScheduleResult:
            return self.run()

    class Scheduler:
        def __init__(self, _: SchedulingOptions) -> None:
            pass

        def start(self, sources: tuple[Path, ...], __: object) -> Coordinator:
            scheduled.extend(sources)
            return Coordinator()

    monkeypatch.setattr(batch, "BatchScheduler", Scheduler)
    batch.run_batch(books, settings, tmp_path, PipelineMode.CONVERT)
    assert scheduled == [book.identity_path.resolve() for book in books]


def _identity(path: Path) -> DestinationIdentity:
    return DestinationIdentity(directory=path.parent, filename=path.name)


def _now() -> datetime:
    return datetime(2026, 8, 4, 15, 0, tzinfo=UTC)


def _raise_placement(_: Path, __: Path, *, move: bool) -> Path:
    assert not move
    raise OSError


def _commit_failure(_: object, __: object) -> bool:
    return False


def _raise_cleanup(_: Path) -> None:
    raise OSError


def _book(root: Path) -> BookDirectory:
    return BookDirectory(
        path=root, files=(AudioFile(path=root / "book.m4b", duration_ms=1),)
    )


def _batch_result() -> BatchScheduleResult:
    return BatchScheduleResult(
        total=1,
        succeeded=1,
        failed=0,
        cancelled=0,
        results=(
            BookScheduleResult(
                index=0, source=Path("book"), outcome=BookOutcome.SUCCESS
            ),
        ),
    )
