"""Historical state-machine guards at the typed SQLite public boundary."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.db.queries import (
    acquire_lock,
    completed_stages,
    delete_book,
    get_alias,
    get_book,
    get_cover,
    get_stages,
    get_variants,
    increment_retry,
    list_books,
    release_lock,
    save_alias,
    set_stage,
    store_cover,
    update_book,
    upsert_book,
)
from audiobook_pipeline.db.rows import BookRow, StageRow, utc_now
from audiobook_pipeline.errors import ManifestError
from audiobook_pipeline.models.stage import (
    PRE_COMPLETED_STAGES,
    ErrorCategory,
    PipelineLevel,
    PipelineMode,
    Stage,
    StageStatus,
    stages_for,
)


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with connect(tmp_path / "pipeline.db") as conn:
        yield conn


def _book(book_hash: str = "book-1", **changes: object) -> BookRow:
    values: dict[str, object] = {
        "book_hash": book_hash,
        "source_path": f"/source/{book_hash}",
        "mode": PipelineMode.CONVERT.value,
    }
    values.update(changes)
    return BookRow.model_validate(values)


def test_typed_book_create_read_update_list_and_delete(
    db: sqlite3.Connection,
) -> None:
    upsert_book(db, _book("a"))
    upsert_book(db, _book("b", mode=PipelineMode.ORGANIZE.value))
    first = get_book(db, "a")
    assert first is not None
    assert first.status == "pending"
    assert first.retry_count == 0

    update_book(db, first.model_copy(update={"status": "processing"}))
    assert get_book(db, "a").status == "processing"  # type: ignore[union-attr]
    assert [row.book_hash for row in list_books(db, mode="organize")] == ["b"]

    delete_book(db, "a")
    assert get_book(db, "a") is None


def test_upsert_updates_book_without_erasing_stage_progress(
    db: sqlite3.Connection,
) -> None:
    upsert_book(db, _book())
    set_stage(
        db,
        StageRow(book_hash="book-1", stage="convert", status="completed"),
    )
    upsert_book(db, _book(status="running", parsed_title="Corrected"))

    assert completed_stages(db, "book-1") == {"convert"}
    assert get_book(db, "book-1").parsed_title == "Corrected"  # type: ignore[union-attr]


def test_stage_result_replaces_previous_state_and_keeps_handoff(
    db: sqlite3.Connection,
) -> None:
    upsert_book(db, _book())
    set_stage(db, StageRow(book_hash="book-1", stage="convert", status="running"))
    set_stage(
        db,
        StageRow(
            book_hash="book-1",
            stage="convert",
            status=StageStatus.COMPLETED.value,
            completed_at=utc_now(),
            output_file="/output/book.m4b",
        ),
    )

    rows = get_stages(db, "book-1")
    assert len(rows) == 1
    assert rows[0].completed_at is not None
    assert rows[0].output_file == "/output/book.m4b"


def test_deleting_book_cascades_to_stage_rows(db: sqlite3.Connection) -> None:
    upsert_book(db, _book())
    set_stage(db, StageRow(book_hash="book-1", stage="validate"))
    delete_book(db, "book-1")
    assert get_stages(db, "book-1") == []


def test_mode_planning_preserves_precompleted_and_next_stage_semantics() -> None:
    assert PRE_COMPLETED_STAGES[PipelineMode.ENRICH] == (
        Stage.VALIDATE,
        Stage.CONCAT,
        Stage.CONVERT,
    )
    assert stages_for(PipelineMode.ENRICH, PipelineLevel.NORMAL)[0] is Stage.ASIN
    assert stages_for(PipelineMode.ORGANIZE, PipelineLevel.NORMAL) == (Stage.ORGANIZE,)


def test_retry_and_error_fields_round_trip(db: sqlite3.Connection) -> None:
    upsert_book(db, _book())
    assert increment_retry(db, "book-1") == 1
    current = get_book(db, "book-1")
    assert current is not None
    failed = current.model_copy(
        update={
            "error_timestamp": utc_now(),
            "error_stage": "validate",
            "error_exit_code": 2,
            "error_category": ErrorCategory.PERMANENT.value,
            "error_message": "bad input",
        }
    )
    update_book(db, failed)

    actual = get_book(db, "book-1")
    assert actual is not None
    assert (
        actual.error_stage,
        actual.error_exit_code,
        actual.error_category,
        actual.error_message,
    ) == ("validate", 2, "permanent", "bad input")


def test_cover_bytes_and_size_round_trip(db: sqlite3.Connection) -> None:
    upsert_book(db, _book())
    image = b"\x89PNG" + b"\x00" * 20
    store_cover(db, "book-1", image)
    assert get_cover(db, "book-1") == image
    assert get_book(db, "book-1").cover_art_size == len(image)  # type: ignore[union-attr]


def test_alias_identity_upsert_and_reverse_lookup(db: sqlite3.Connection) -> None:
    save_alias(db, "Same", "Same")
    assert get_alias(db, "Same") is None
    save_alias(db, "variant", "canonical-1")
    save_alias(db, "variant", "canonical-2")
    save_alias(db, "other", "canonical-2")
    assert get_alias(db, "variant") == "canonical-2"
    assert get_variants(db, "canonical-2") == ["other", "variant"]


def test_lock_is_exclusive_and_reusable(db: sqlite3.Connection) -> None:
    assert acquire_lock(db) is True
    assert acquire_lock(db) is False
    release_lock(db)
    assert acquire_lock(db) is True


def test_independent_thread_connections_do_not_corrupt_state(tmp_path: Path) -> None:
    db_path = tmp_path / "threads.db"
    with connect(db_path):
        pass

    def write(index: int) -> None:
        with connect(db_path) as conn:
            upsert_book(conn, _book(f"book-{index}"))

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(write, index) for index in range(10)]
        for future in futures:
            future.result()

    with connect(db_path) as conn:
        assert len(list_books(conn)) == 10


def test_update_of_missing_book_is_rejected(db: sqlite3.Connection) -> None:
    with pytest.raises(ManifestError) as caught:
        update_book(db, _book("missing"))
    assert caught.value.message == "Cannot update book: book 'missing' was not found"


def test_retry_of_missing_book_is_rejected(db: sqlite3.Connection) -> None:
    with pytest.raises(ManifestError) as caught:
        increment_retry(db, "missing")
    assert (
        caught.value.message
        == "Cannot increment retry count: book 'missing' was not found"
    )


def test_cover_for_missing_book_is_rejected(db: sqlite3.Connection) -> None:
    with pytest.raises(ManifestError) as caught:
        store_cover(db, "missing", b"image")
    assert (
        caught.value.message == "Cannot store cover art: book 'missing' was not found"
    )
