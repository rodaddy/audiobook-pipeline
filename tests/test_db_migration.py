"""Regression tests for additive, model-driven database migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from audiobook_pipeline.db.connection import EXTRA_COLUMNS, connect, model_columns
from audiobook_pipeline.db.queries import get_book, upsert_book
from audiobook_pipeline.db.rows import BookRow


def _legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE books (book_hash TEXT PRIMARY KEY, source_path TEXT, "
            "mode TEXT, status TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO books VALUES "
            "('abc', '/src/book', 'convert', 'completed', 'then', 'then')"
        )


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(books)")}


def test_missing_model_columns_are_added_and_existing_rows_survive(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "old.db"
    _legacy_database(db_path)

    with connect(db_path) as conn:
        assert set(model_columns(BookRow)) | set(EXTRA_COLUMNS["books"]) <= _columns(
            conn
        )
        survivor = get_book(conn, "abc")

    assert survivor is not None
    assert survivor.source_path == "/src/book"
    assert survivor.status == "completed"


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "old.db"
    _legacy_database(db_path)
    with connect(db_path) as conn:
        first = _columns(conn)
    with connect(db_path) as conn:
        assert _columns(conn) == first


def test_fresh_database_has_complete_model_shape(tmp_path: Path) -> None:
    with connect(tmp_path / "fresh.db") as conn:
        expected = set(model_columns(BookRow)) | set(EXTRA_COLUMNS["books"])
        assert _columns(conn) == expected


def test_chapter_provenance_round_trips(tmp_path: Path) -> None:
    with connect(tmp_path / "pipeline.db") as conn:
        for index, source in enumerate(("embedded", "audnexus", "file-boundary")):
            expected = BookRow(
                book_hash=f"book-{index}",
                source_path=f"/src/{index}",
                mode="convert",
                chapter_source=source,
                chapter_count=42,
            )
            upsert_book(conn, expected)
            actual = get_book(conn, expected.book_hash)
            assert actual is not None
            assert actual.chapter_source == source
            assert actual.chapter_count == 42
