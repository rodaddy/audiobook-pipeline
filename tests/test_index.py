"""Focused behavior and SQLite-concurrency tests for the durable library index."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from audiobook_pipeline.models.index import (
    ClaimState,
    DestinationIdentity,
    FolderIdentity,
    IndexedFile,
    IndexOptions,
    ReservationClaim,
    SourceIdentity,
)
from audiobook_pipeline.services.index import (
    LibraryIndex,
    SQLiteIndexStore,
    sqlite_connection_factory,
)


def _index(tmp_path: Path, *, stale_after: int | None = None) -> LibraryIndex:
    root = tmp_path / "library"
    options = IndexOptions(stale_reservation_after_seconds=stale_after)
    factory = sqlite_connection_factory(tmp_path / "state" / "index.sqlite3", options)
    return LibraryIndex(root, SQLiteIndexStore(factory, options))


def _destination(tmp_path: Path) -> DestinationIdentity:
    return DestinationIdentity(
        directory=tmp_path / "library" / "Author" / "Book", filename="Book.m4b"
    )


def _now() -> datetime:
    return datetime(2026, 8, 4, 15, 0, tzinfo=UTC)


def test_scan_reuses_folder_and_finds_existing_file(tmp_path: Path) -> None:
    index = _index(tmp_path)
    destination = _destination(tmp_path)
    destination.directory.mkdir(parents=True)
    destination.path.write_bytes(b"m4b")
    index.scan()
    assert index.reuse_existing(tmp_path / "library", "Author") == "Author"
    assert index.file_exists(destination)
    assert index.folder_count == 2
    assert index.file_count == 1


def test_registration_and_source_deduplication_are_idempotent(tmp_path: Path) -> None:
    index = _index(tmp_path)
    destination = _destination(tmp_path)
    source = SourceIdentity(stem="The Book")
    index.register_folder(FolderIdentity(parent=tmp_path / "library", name="Author"))
    index.register_folder(FolderIdentity(parent=tmp_path / "library", name="Author"))
    index.register_file(IndexedFile(destination=destination))
    index.register_file(IndexedFile(destination=destination))
    assert index.reuse_existing(tmp_path / "library", "author") == "Author"
    assert index.file_exists(destination)
    assert not index.mark_processed(source)
    assert index.mark_processed(source)
    assert not _index(tmp_path).mark_processed(source)


def test_author_alias_is_reused_across_index_instances(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index.register_author("R. A. Salvatore")
    assert index.match_author("R.A. Salvatore") == "R. A. Salvatore"
    assert _index(tmp_path).match_author("R.A. Salvatore") == "R. A. Salvatore"


def test_author_matching_allows_extra_middle_initial_but_not_shared_surnames(
    tmp_path: Path,
) -> None:
    index = _index(tmp_path)
    index.register_author("Richard K. Morgan")
    index.register_author("Tad Williams")
    assert index.match_author("Richard Morgan") == "Richard K. Morgan"
    assert index.match_author("Michael Williams") == "Michael Williams"


def test_claim_commit_is_idempotent_and_blocks_future_claims(tmp_path: Path) -> None:
    index = _index(tmp_path)
    destination = _destination(tmp_path)
    claim = index.claim(destination, SourceIdentity(stem="Book"), _now())
    assert claim.state is ClaimState.CLAIMED
    assert claim.reservation is not None
    entry = IndexedFile(destination=destination)
    assert index.commit(claim.reservation, entry)
    assert index.commit(claim.reservation, entry)
    assert (
        index.claim(destination, SourceIdentity(stem="Other"), _now()).state
        is ClaimState.REGISTERED
    )


def test_release_requires_the_issued_reservation_claim_id(tmp_path: Path) -> None:
    index = _index(tmp_path)
    claim = index.claim(_destination(tmp_path), SourceIdentity(stem="Book"), _now())
    assert claim.reservation is not None
    assert index.release(claim.reservation)
    assert not index.release(claim.reservation)


def test_displaced_reservation_cannot_commit_another_workers_file(
    tmp_path: Path,
) -> None:
    index = _index(tmp_path)
    destination = _destination(tmp_path)
    first = index.claim(destination, SourceIdentity(stem="First"), _now())
    assert first.reservation is not None
    assert index.release(first.reservation)
    second = index.claim(destination, SourceIdentity(stem="Second"), _now())
    assert second.reservation is not None
    entry = IndexedFile(destination=destination)
    assert index.commit(second.reservation, entry)
    assert not index.commit(first.reservation, entry)


def test_two_sqlite_workers_cannot_both_reserve_one_destination(tmp_path: Path) -> None:
    options = IndexOptions(busy_timeout_ms=2_000)
    database = tmp_path / "state" / "index.sqlite3"
    first = LibraryIndex(
        tmp_path / "library",
        SQLiteIndexStore(sqlite_connection_factory(database, options), options),
    )
    second = LibraryIndex(
        tmp_path / "library",
        SQLiteIndexStore(sqlite_connection_factory(database, options), options),
    )
    destination = _destination(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda index: (
                    index.claim(destination, SourceIdentity(stem="Book"), _now()).state
                ),
                (first, second),
            )
        )
    assert results.count(ClaimState.CLAIMED) == 1
    assert results.count(ClaimState.RESERVED) == 1


def test_explicit_stale_policy_can_reclaim_or_keep_a_reservation(
    tmp_path: Path,
) -> None:
    destination = _destination(tmp_path)
    first = _index(tmp_path, stale_after=60)
    initial = first.claim(destination, SourceIdentity(stem="Book"), _now())
    assert initial.state is ClaimState.CLAIMED
    reclaimed = first.claim(
        destination, SourceIdentity(stem="Replacement"), _now() + timedelta(seconds=60)
    )
    assert reclaimed.state is ClaimState.CLAIMED
    retained = _index(tmp_path / "other")
    retained_destination = _destination(tmp_path / "other")
    assert (
        retained.claim(retained_destination, SourceIdentity(stem="Book"), _now()).state
        is ClaimState.CLAIMED
    )
    assert (
        retained.claim(
            retained_destination,
            SourceIdentity(stem="Other"),
            _now() + timedelta(days=1),
        ).state
        is ClaimState.RESERVED
    )


def test_identity_models_reject_ambiguous_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        DestinationIdentity(directory=Path("relative"), filename="book.m4b")
    with pytest.raises(ValueError, match="single path component"):
        FolderIdentity(parent=tmp_path, name="Author/Book")


def test_claim_state_requires_matching_reservation_shape(tmp_path: Path) -> None:
    destination = _destination(tmp_path)
    reservation = (
        _index(tmp_path)
        .claim(destination, SourceIdentity(stem="Book"), _now())
        .reservation
    )
    assert reservation is not None
    with pytest.raises(ValueError, match="claimed state requires"):
        ReservationClaim(state=ClaimState.CLAIMED)
    with pytest.raises(ValueError, match="non-claimed state forbids"):
        ReservationClaim(state=ClaimState.RESERVED, reservation=reservation)
