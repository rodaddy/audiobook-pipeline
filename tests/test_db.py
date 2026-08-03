"""Tests for the sqlite3 + Pydantic row-factory database layer."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError

from audiobook_pipeline.db.connection import (
    EXTRA_COLUMNS,
    TABLES,
    _sql_type,
    connect,
    initialize,
    model_columns,
)
from audiobook_pipeline.db.queries import (
    REORGANIZE_LOCK,
    acquire_lock,
    completed_stages,
    delete_book,
    get_alias,
    get_book,
    get_cover,
    get_lock,
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


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialized database, closed on teardown."""
    with connect(tmp_path / "pipeline.db") as conn:
        yield conn


def make_book(book_hash: str = "hash1", **overrides: object) -> BookRow:
    """A minimal valid book record."""
    fields: dict[str, object] = {
        "book_hash": book_hash,
        "source_path": f"/src/{book_hash}",
        "mode": "convert",
    }
    fields.update(overrides)
    return BookRow.model_validate(fields)


# ---------------------------------------------------------------------------
# schema generation
# ---------------------------------------------------------------------------


def test_sql_type_unwraps_optional() -> None:
    assert _sql_type(int | None) == "INTEGER"
    assert _sql_type(str | None) == "TEXT"
    assert _sql_type(float) == "REAL"


def test_every_model_field_becomes_a_column(db: sqlite3.Connection) -> None:
    for table, model in TABLES.items():
        live = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        assert set(model_columns(model)) <= live, table


def test_cover_art_column_exists_but_is_not_a_model_field(
    db: sqlite3.Connection,
) -> None:
    live = {row[1] for row in db.execute("PRAGMA table_info(books)")}
    assert "cover_art" in live
    assert "cover_art" not in model_columns(BookRow)
    assert EXTRA_COLUMNS["books"]["cover_art"] == "BLOB"


def test_pragmas_are_applied(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_initialize_is_idempotent(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book())
    initialize(db)
    initialize(db)
    assert get_book(db, "hash1") is not None


# ---------------------------------------------------------------------------
# migration -- the defect this layer exists to prevent
# ---------------------------------------------------------------------------


def test_missing_column_is_added_and_rows_survive(tmp_path: Path) -> None:
    """A database created before a field existed gains the column, keeping data."""
    db_path = tmp_path / "old.db"

    # Build a books table that predates chapter_source, the way a real old
    # database would look. Only the columns needed to insert a row.
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE books (book_hash TEXT PRIMARY KEY, source_path TEXT, "
        "mode TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
    )
    legacy.execute(
        "INSERT INTO books (book_hash, source_path, mode, status, created_at, "
        "updated_at) VALUES ('old1', '/src/old1', 'convert', 'completed', ?, ?)",
        (utc_now(), utc_now()),
    )
    legacy.commit()
    legacy.close()

    with connect(db_path) as conn:
        live = {row[1] for row in conn.execute("PRAGMA table_info(books)")}
        assert "chapter_source" in live
        assert "cover_art" in live

        survivor = get_book(conn, "old1")
        assert survivor is not None
        assert survivor.status == "completed"
        assert survivor.chapter_source is None


def test_migration_does_not_drop_unknown_columns(tmp_path: Path) -> None:
    """An extra column from a future version is left alone, not destroyed."""
    db_path = tmp_path / "future.db"
    legacy = sqlite3.connect(db_path)
    legacy.execute(
        "CREATE TABLE books (book_hash TEXT PRIMARY KEY, source_path TEXT, "
        "mode TEXT, invented_later TEXT)"
    )
    legacy.execute(
        "INSERT INTO books (book_hash, source_path, mode, invented_later) "
        "VALUES ('f1', '/src/f1', 'convert', 'keepme')"
    )
    legacy.commit()
    legacy.close()

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT invented_later FROM books WHERE book_hash = 'f1'"
        ).fetchone()
        assert row["invented_later"] == "keepme"


def test_fresh_database_needs_no_migration(tmp_path: Path) -> None:
    """Creating from scratch produces the full shape in one pass."""
    with connect(tmp_path / "fresh.db") as conn:
        for table, model in TABLES.items():
            live = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            expected = set(model_columns(model)) | set(EXTRA_COLUMNS.get(table, {}))
            assert live == expected, table


# ---------------------------------------------------------------------------
# books
# ---------------------------------------------------------------------------


def test_book_round_trips_as_a_model(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book(chapter_source="audnexus", chapter_count=12))
    book = get_book(db, "hash1")
    assert isinstance(book, BookRow)
    assert book.chapter_source == "audnexus"
    assert book.chapter_count == 12


def test_get_book_returns_none_for_unknown_hash(db: sqlite3.Connection) -> None:
    assert get_book(db, "nope") is None


def test_upsert_replaces_rather_than_ignoring(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book(parsed_title="Wrong"))
    upsert_book(db, make_book(parsed_title="Right"))
    book = get_book(db, "hash1")
    assert book is not None
    assert book.parsed_title == "Right"


def test_update_stamps_updated_at(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book(updated_at="2020-01-01T00:00:00+00:00"))
    book = get_book(db, "hash1")
    assert book is not None

    update_book(db, book.model_copy(update={"status": "completed"}))

    refreshed = get_book(db, "hash1")
    assert refreshed is not None
    assert refreshed.status == "completed"
    assert refreshed.updated_at != "2020-01-01T00:00:00+00:00"


def test_list_books_filters_by_status_and_mode(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book("a", status="pending", mode="convert"))
    upsert_book(db, make_book("b", status="completed", mode="convert"))
    upsert_book(db, make_book("c", status="completed", mode="organize"))

    assert {b.book_hash for b in list_books(db)} == {"a", "b", "c"}
    assert {b.book_hash for b in list_books(db, status="completed")} == {"b", "c"}
    assert {b.book_hash for b in list_books(db, mode="organize")} == {"c"}
    assert [
        b.book_hash for b in list_books(db, status="completed", mode="convert")
    ] == ["b"]


def test_increment_retry_returns_the_new_count(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book())
    assert increment_retry(db, "hash1") == 1
    assert increment_retry(db, "hash1") == 2


def test_extra_column_is_refused_by_the_model() -> None:
    with pytest.raises(ValidationError):
        BookRow.model_validate({
            "book_hash": "x",
            "source_path": "/x",
            "mode": "convert",
            "typoed_field": "value",
        })


# ---------------------------------------------------------------------------
# cover art
# ---------------------------------------------------------------------------


def test_cover_round_trips_without_entering_the_model(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book())
    store_cover(db, "hash1", b"\xff\xd8jpegbytes")

    assert get_cover(db, "hash1") == b"\xff\xd8jpegbytes"

    book = get_book(db, "hash1")
    assert book is not None
    assert book.cover_art_size == len(b"\xff\xd8jpegbytes")
    assert not hasattr(book, "cover_art")


def test_get_cover_returns_none_when_none_stored(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book())
    assert get_cover(db, "hash1") is None


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def test_stage_replaces_rather_than_appending(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book())
    set_stage(db, StageRow(book_hash="hash1", stage="convert", status="running"))
    set_stage(db, StageRow(book_hash="hash1", stage="convert", status="completed"))

    stages = get_stages(db, "hash1")
    assert len(stages) == 1
    assert stages[0].status == "completed"


def test_completed_stages_excludes_unfinished(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book())
    set_stage(db, StageRow(book_hash="hash1", stage="validate", status="completed"))
    set_stage(db, StageRow(book_hash="hash1", stage="convert", status="failed"))

    assert completed_stages(db, "hash1") == {"validate"}


def test_deleting_a_book_cascades_to_its_stages(db: sqlite3.Connection) -> None:
    upsert_book(db, make_book())
    set_stage(db, StageRow(book_hash="hash1", stage="validate", status="completed"))

    delete_book(db, "hash1")

    assert get_book(db, "hash1") is None
    assert get_stages(db, "hash1") == []


# ---------------------------------------------------------------------------
# author aliases
# ---------------------------------------------------------------------------


def test_alias_round_trips(db: sqlite3.Connection) -> None:
    save_alias(db, "Michael Williams", "Tad Williams")
    assert get_alias(db, "Michael Williams") == "Tad Williams"


def test_identity_alias_is_not_stored(db: sqlite3.Connection) -> None:
    save_alias(db, "Tad Williams", "Tad Williams")
    assert get_alias(db, "Tad Williams") is None


def test_get_variants_lists_every_alias_of_a_canonical(db: sqlite3.Connection) -> None:
    save_alias(db, "T. Williams", "Tad Williams")
    save_alias(db, "Tad Wiliams", "Tad Williams")
    assert get_variants(db, "Tad Williams") == ["T. Williams", "Tad Wiliams"]


# ---------------------------------------------------------------------------
# locking
# ---------------------------------------------------------------------------


def test_lock_is_exclusive_while_this_process_holds_it(db: sqlite3.Connection) -> None:
    assert acquire_lock(db) is True

    holder = get_lock(db)
    assert holder is not None
    assert holder.pid == os.getpid()

    # This process is alive, so the second attempt must not steal from itself.
    assert acquire_lock(db) is False


def test_lock_is_stolen_from_a_dead_holder(db: sqlite3.Connection) -> None:
    # PID 0x7FFFFFFF is above any real pid on Linux or macOS, so it names a
    # process that cannot exist rather than one that might.
    dead_pid = 0x7FFFFFFF
    db.execute(
        "INSERT INTO pipeline_locks (lock_name, acquired_at, pid) VALUES (?, ?, ?)",
        (REORGANIZE_LOCK, utc_now(), dead_pid),
    )
    db.commit()

    assert acquire_lock(db) is True

    holder = get_lock(db)
    assert holder is not None
    assert holder.pid == os.getpid()


def test_release_frees_the_lock(db: sqlite3.Connection) -> None:
    acquire_lock(db)
    release_lock(db)

    assert get_lock(db) is None
    assert acquire_lock(db) is True


def test_upsert_does_not_destroy_stage_rows(db: sqlite3.Connection) -> None:
    """INSERT OR REPLACE is DELETE+INSERT, and stages cascade off books.

    Measured 2026-08-02 on the live re-run: this erased the pipeline's own
    progress record, so an already-converted book re-ran every stage and wrote
    a second copy into the library.
    """
    upsert_book(db, make_book())
    set_stage(db, StageRow(book_hash="hash1", stage="convert", status="completed"))

    upsert_book(db, make_book(status="running"))

    assert completed_stages(db, "hash1") == {"convert"}


def test_upsert_still_updates_a_known_book(db: sqlite3.Connection) -> None:
    """The fix must not turn the upsert into an INSERT OR IGNORE."""
    upsert_book(db, make_book(parsed_title="Wrong"))
    upsert_book(db, make_book(parsed_title="Right"))

    book = get_book(db, "hash1")
    assert book is not None
    assert book.parsed_title == "Right"
