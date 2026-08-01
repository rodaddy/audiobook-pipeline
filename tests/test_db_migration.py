"""Tests for the additive column migration in pipeline_db.

There was no migration path at all: CREATE TABLE IF NOT EXISTS does nothing to
a table that already exists, so a database created before a column was added
kept its old shape and every write to the new column was silently dropped.
_AGENTS.md documented this as "a schema change applies to newly created
databases only" -- which in practice means the pipeline loses data on any
machine that has run it before.
"""

from __future__ import annotations

import sqlite3

from audiobook_pipeline.pipeline_db import _BOOKS_COLUMN_TYPES, PipelineDB

_OLD_SCHEMA = """
CREATE TABLE books (
    book_hash   TEXT PRIMARY KEY,
    source_path TEXT,
    mode        TEXT,
    status      TEXT,
    created_at  TEXT,
    updated_at  TEXT,
    retry_count INTEGER,
    max_retries INTEGER
);
"""


def _old_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(_OLD_SCHEMA)
    conn.commit()
    conn.close()


def _columns(path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(books)")}
    finally:
        conn.close()


class TestSchemaParsing:
    def test_column_types_parsed_from_schema(self):
        """The migration derives its column list from _SCHEMA, not a copy."""
        assert _BOOKS_COLUMN_TYPES["chapter_count"] == "INTEGER"
        assert _BOOKS_COLUMN_TYPES["chapter_source"] == "TEXT"
        assert "book_hash" in _BOOKS_COLUMN_TYPES

    def test_constraint_lines_are_not_columns(self):
        for junk in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", ""):
            assert junk not in _BOOKS_COLUMN_TYPES


class TestAdditiveMigration:
    def test_missing_columns_are_added(self, tmp_path):
        db_path = tmp_path / "old.db"
        _old_db(db_path)
        assert "chapter_source" not in _columns(db_path)

        PipelineDB(db_path)

        after = _columns(db_path)
        assert "chapter_source" in after
        assert "chapter_count" in after

    def test_existing_rows_survive(self, tmp_path):
        """The migration must never destroy data."""
        db_path = tmp_path / "old.db"
        _old_db(db_path)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO books (book_hash, source_path, mode, status) "
            "VALUES ('abc', '/src/book', 'convert', 'completed')"
        )
        conn.commit()
        conn.close()

        PipelineDB(db_path)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT source_path, status FROM books WHERE book_hash = 'abc'"
        ).fetchone()
        conn.close()
        assert row == ("/src/book", "completed")

    def test_migration_is_idempotent(self, tmp_path):
        db_path = tmp_path / "old.db"
        _old_db(db_path)
        PipelineDB(db_path)
        first = _columns(db_path)
        PipelineDB(db_path)
        assert _columns(db_path) == first

    def test_fresh_database_needs_no_migration(self, tmp_path):
        db_path = tmp_path / "new.db"
        PipelineDB(db_path)
        assert "chapter_source" in _columns(db_path)


class TestChapterSourceRoundTrip:
    """A column must be in _SCHEMA, _BOOKS_COLUMNS AND the read path.

    chapter_source was written correctly and read back as None until the third
    list was updated -- the value reached the database and vanished on the way
    out.
    """

    def test_chapter_source_survives_write_and_read(self, tmp_path):
        db = PipelineDB(tmp_path / "p.db")
        h = "h" * 16
        db.create(h, "/src/book", "convert")
        db.update(h, {"metadata": {"chapter_source": "audnexus", "chapter_count": 42}})
        meta = (db.read(h) or {}).get("metadata", {})
        assert meta["chapter_source"] == "audnexus"
        assert meta["chapter_count"] == 42

    def test_each_provenance_value_round_trips(self, tmp_path):
        for i, source in enumerate(("embedded", "audnexus", "file-boundary")):
            db = PipelineDB(tmp_path / f"p{i}.db")
            h = f"{i}" * 16
            db.create(h, "/src/book", "convert")
            db.update(h, {"metadata": {"chapter_source": source}})
            assert (db.read(h) or {})["metadata"]["chapter_source"] == source
