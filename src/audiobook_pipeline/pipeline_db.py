"""SQLite-backed pipeline state -- replaces JSON manifests and author alias files.

Single WAL-mode database stores book state, per-stage progress, cover art blobs,
author name aliases, and concurrency locks. Thread-safe via per-thread connections
and SQLite's built-in locking. Eliminates NFS temp file issues from the JSON
manifest approach.

PipelineDB is a drop-in replacement for Manifest with the same method signatures
(create, read, read_field, update, set_stage, check_status, get_next_stage,
increment_retry, set_error) plus new APIs for cover art, author aliases, and
pipeline locking.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from .errors import ManifestError
from .models import (
    PRE_COMPLETED_STAGES,
    STAGE_ORDER,
    ErrorCategory,
    PipelineMode,
    Stage,
    StageStatus,
)

log = logger.bind(stage="db")

_SCHEMA = """\
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS books (
    book_hash         TEXT PRIMARY KEY,
    source_path       TEXT NOT NULL,
    mode              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    retry_count       INTEGER NOT NULL DEFAULT 0,
    max_retries       INTEGER NOT NULL DEFAULT 3,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    error_timestamp   TEXT,
    error_stage       TEXT,
    error_exit_code   INTEGER,
    error_category    TEXT,
    error_message     TEXT,
    target_bitrate    INTEGER,
    file_count        INTEGER,
    total_duration    REAL,
    chapter_count     INTEGER,
    codec             TEXT,
    bitrate           TEXT,
    parsed_author     TEXT,
    parsed_title      TEXT,
    parsed_series     TEXT,
    parsed_position   TEXT,
    parsed_asin       TEXT,
    parsed_narrator   TEXT,
    parsed_year       TEXT,
    parsed_subtitle   TEXT,
    parsed_description TEXT,
    parsed_publisher  TEXT,
    parsed_copyright  TEXT,
    parsed_language   TEXT,
    parsed_genre      TEXT,
    cover_url         TEXT,
    cover_art         BLOB,
    cover_art_size    INTEGER
);

CREATE TABLE IF NOT EXISTS stages (
    book_hash    TEXT NOT NULL,
    stage        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    completed_at TEXT,
    output_file  TEXT,
    dest_dir     TEXT,
    PRIMARY KEY (book_hash, stage),
    FOREIGN KEY (book_hash) REFERENCES books(book_hash) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS author_aliases (
    variant   TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_locks (
    lock_name   TEXT PRIMARY KEY,
    acquired_at TEXT NOT NULL,
    pid         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_books_status ON books(status);
CREATE INDEX IF NOT EXISTS idx_author_canonical ON author_aliases(canonical);
"""

# Columns that live in the books table (for flattened update mapping)
_BOOKS_COLUMNS = {
    "source_path",
    "mode",
    "status",
    "retry_count",
    "max_retries",
    "created_at",
    "updated_at",
    "error_timestamp",
    "error_stage",
    "error_exit_code",
    "error_category",
    "error_message",
    "target_bitrate",
    "file_count",
    "total_duration",
    "chapter_count",
    "codec",
    "bitrate",
    "parsed_author",
    "parsed_title",
    "parsed_series",
    "parsed_position",
    "parsed_asin",
    "parsed_narrator",
    "parsed_year",
    "parsed_subtitle",
    "parsed_description",
    "parsed_publisher",
    "parsed_copyright",
    "parsed_language",
    "parsed_genre",
    "cover_url",
    "cover_art",
    "cover_art_size",
}

# Stage columns that can be updated via set_stage / update
_STAGE_COLUMNS = {"status", "completed_at", "output_file", "dest_dir"}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PipelineDB:
    """SQLite-backed pipeline state manager.

    Thread-safe: each thread gets its own connection via threading.local().
    The database uses WAL mode for concurrent readers + single writer.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._local = threading.local()
        # Initialize schema on the main thread's connection
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a per-thread SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=10.0,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    def close(self) -> None:
        """Close the current thread's connection."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- Manifest-compatible API --

    def create(
        self,
        book_hash: str,
        source_path: str,
        mode: PipelineMode,
    ) -> dict:
        """Create a new book record with stage rows. Returns legacy dict shape."""
        now = _utcnow()
        conn = self._get_conn()

        conn.execute(
            """INSERT OR REPLACE INTO books
               (book_hash, source_path, mode, status, retry_count, max_retries,
                created_at, updated_at)
               VALUES (?, ?, ?, 'pending', 0, 3, ?, ?)""",
            (book_hash, source_path, str(mode), now, now),
        )

        # Create stage rows for all stages
        pre = PRE_COMPLETED_STAGES.get(mode, [])
        for stage in Stage:
            if stage in pre:
                status = "completed"
                completed_at = now
                # For convert stage in non-convert modes, record source as output
                output_file = source_path if stage == Stage.CONVERT else None
            else:
                status = "pending"
                completed_at = None
                output_file = None
            conn.execute(
                """INSERT OR REPLACE INTO stages
                   (book_hash, stage, status, completed_at, output_file)
                   VALUES (?, ?, ?, ?, ?)""",
                (book_hash, stage.value, status, completed_at, output_file),
            )

        conn.commit()
        log.info(f"Created book record {book_hash} mode={mode}")
        return self.read(book_hash)  # type: ignore[return-value]

    def read(self, book_hash: str) -> dict | None:
        """Read a book record as a legacy-compatible dict."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM books WHERE book_hash = ?", (book_hash,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row, book_hash, conn)

    def _row_to_dict(
        self,
        row: sqlite3.Row,
        book_hash: str,
        conn: sqlite3.Connection,
    ) -> dict:
        """Convert a books row + stages rows into legacy dict format."""
        data: dict[str, Any] = {
            "book_hash": row["book_hash"],
            "source_path": row["source_path"],
            "created_at": row["created_at"],
            "mode": row["mode"],
            "status": row["status"],
            "retry_count": row["retry_count"],
            "max_retries": row["max_retries"],
            "last_error": {},
            "stages": {},
            "metadata": {},
        }

        # Populate last_error if present
        if row["error_timestamp"]:
            data["last_error"] = {
                "timestamp": row["error_timestamp"],
                "stage": row["error_stage"],
                "exit_code": row["error_exit_code"],
                "category": row["error_category"],
                "message": row["error_message"],
            }

        # Populate metadata from flattened columns
        meta_keys = [
            "target_bitrate",
            "file_count",
            "total_duration",
            "chapter_count",
            "codec",
            "bitrate",
            "parsed_author",
            "parsed_title",
            "parsed_series",
            "parsed_position",
            "parsed_asin",
            "parsed_narrator",
            "parsed_year",
            "parsed_subtitle",
            "parsed_description",
            "parsed_publisher",
            "parsed_copyright",
            "parsed_language",
            "parsed_genre",
            "cover_url",
        ]
        for key in meta_keys:
            val = row[key]
            if val is not None:
                data["metadata"][key] = val

        # Populate stages
        stage_rows = conn.execute(
            "SELECT * FROM stages WHERE book_hash = ?", (book_hash,)
        ).fetchall()
        for sr in stage_rows:
            stage_data: dict[str, Any] = {"status": sr["status"]}
            if sr["completed_at"]:
                stage_data["completed_at"] = sr["completed_at"]
            if sr["output_file"]:
                stage_data["output_file"] = sr["output_file"]
            if sr["dest_dir"]:
                stage_data["dest_dir"] = sr["dest_dir"]
            data["stages"][sr["stage"]] = stage_data

        return data

    def read_field(self, book_hash: str, field: str) -> Any:
        """Read a single field, supporting dotted paths for backward compat.

        Short-circuits common paths to direct SQL instead of full dict build.
        """
        parts = field.split(".")

        # Fast path: stages.X.status or stages.X.output_file
        if len(parts) == 3 and parts[0] == "stages":
            stage_name, col = parts[1], parts[2]
            if col in _STAGE_COLUMNS:
                conn = self._get_conn()
                row = conn.execute(
                    f"SELECT {col} FROM stages WHERE book_hash = ? AND stage = ?",
                    (book_hash, stage_name),
                ).fetchone()
                return row[col] if row else None

        # Fast path: metadata.X
        if len(parts) == 2 and parts[0] == "metadata":
            col = parts[1]
            if col in _BOOKS_COLUMNS:
                conn = self._get_conn()
                row = conn.execute(
                    f"SELECT {col} FROM books WHERE book_hash = ?",
                    (book_hash,),
                ).fetchone()
                return row[col] if row else None

        # Fallback: full dict traversal
        data = self.read(book_hash)
        if data is None:
            return None
        current: Any = data
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current

    def update(self, book_hash: str, updates: dict) -> None:
        """Update a book record with a dict of changes.

        Handles nested dicts: {"metadata": {"parsed_author": "X"}} is flattened
        to UPDATE books SET parsed_author = 'X'. Stage updates go to the stages table.
        """
        conn = self._get_conn()

        # Verify book exists
        exists = conn.execute(
            "SELECT 1 FROM books WHERE book_hash = ?", (book_hash,)
        ).fetchone()
        if not exists:
            raise ManifestError(f"Book not found: {book_hash}")

        book_updates: dict[str, Any] = {}
        stage_updates: dict[str, dict[str, Any]] = {}

        for key, value in updates.items():
            if key == "metadata" and isinstance(value, dict):
                # Flatten metadata dict to book columns
                for mk, mv in value.items():
                    if mk in _BOOKS_COLUMNS:
                        book_updates[mk] = mv
            elif key == "stages" and isinstance(value, dict):
                # Stage updates: {"stages": {"convert": {"output_file": "..."}}}
                for stage_name, stage_dict in value.items():
                    if isinstance(stage_dict, dict):
                        stage_updates[stage_name] = stage_dict
            elif key in _BOOKS_COLUMNS:
                book_updates[key] = value

        # Apply book column updates
        if book_updates:
            book_updates["updated_at"] = _utcnow()
            set_clause = ", ".join(f"{k} = ?" for k in book_updates)
            values = list(book_updates.values()) + [book_hash]
            conn.execute(
                f"UPDATE books SET {set_clause} WHERE book_hash = ?",
                values,
            )

        # Apply stage updates
        for stage_name, stage_dict in stage_updates.items():
            valid = {k: v for k, v in stage_dict.items() if k in _STAGE_COLUMNS}
            if valid:
                set_clause = ", ".join(f"{k} = ?" for k in valid)
                values = list(valid.values()) + [book_hash, stage_name]
                conn.execute(
                    f"UPDATE stages SET {set_clause} "
                    f"WHERE book_hash = ? AND stage = ?",
                    values,
                )

        conn.commit()
        log.debug(f"Updated book {book_hash}")

    def set_stage(
        self,
        book_hash: str,
        stage: Stage,
        status: StageStatus,
    ) -> None:
        """Set stage status. Adds completed_at timestamp for COMPLETED."""
        conn = self._get_conn()
        completed_at = _utcnow() if status == StageStatus.COMPLETED else None
        log.debug(f"Stage {stage.value} -> {status} for {book_hash}")
        conn.execute(
            """UPDATE stages SET status = ?, completed_at = ?
               WHERE book_hash = ? AND stage = ?""",
            (str(status), completed_at, book_hash, stage.value),
        )
        conn.execute(
            "UPDATE books SET updated_at = ? WHERE book_hash = ?",
            (_utcnow(), book_hash),
        )
        conn.commit()

    def check_status(self, book_hash: str) -> str:
        """Return book processing status. Returns 'new' if no record exists."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT status FROM books WHERE book_hash = ?", (book_hash,)
        ).fetchone()
        if row is None:
            return "new"
        return row["status"]

    def get_next_stage(
        self,
        book_hash: str,
        mode: PipelineMode,
    ) -> Stage | None:
        """Find the next incomplete stage for this mode."""
        conn = self._get_conn()
        exists = conn.execute(
            "SELECT 1 FROM books WHERE book_hash = ?", (book_hash,)
        ).fetchone()
        if not exists:
            raise ManifestError(f"Book not found: {book_hash}")

        stages = STAGE_ORDER.get(mode, [])
        for stage in stages:
            row = conn.execute(
                "SELECT status FROM stages WHERE book_hash = ? AND stage = ?",
                (book_hash, stage.value),
            ).fetchone()
            stage_status = row["status"] if row else "pending"
            if stage_status != "completed":
                return stage
        return None

    def increment_retry(self, book_hash: str) -> None:
        """Increment the retry counter."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT retry_count FROM books WHERE book_hash = ?", (book_hash,)
        ).fetchone()
        if row is None:
            raise ManifestError(f"Book not found: {book_hash}")
        new_count = row["retry_count"] + 1
        log.warning(f"increment_retry book_hash={book_hash} new_count={new_count}")
        conn.execute(
            "UPDATE books SET retry_count = ?, updated_at = ? WHERE book_hash = ?",
            (new_count, _utcnow(), book_hash),
        )
        conn.commit()

    def set_error(
        self,
        book_hash: str,
        stage: str,
        exit_code: int,
        category: ErrorCategory,
        message: str,
    ) -> None:
        """Record an error for a book."""
        conn = self._get_conn()
        exists = conn.execute(
            "SELECT 1 FROM books WHERE book_hash = ?", (book_hash,)
        ).fetchone()
        if not exists:
            raise ManifestError(f"Book not found: {book_hash}")

        log.error(
            f"set_error book_hash={book_hash} stage={stage} "
            f"category={category} message={message}"
        )
        conn.execute(
            """UPDATE books SET
               error_timestamp = ?, error_stage = ?, error_exit_code = ?,
               error_category = ?, error_message = ?, updated_at = ?
               WHERE book_hash = ?""",
            (_utcnow(), stage, exit_code, str(category), message, _utcnow(), book_hash),
        )
        conn.commit()

    # -- Cover art API --

    def store_cover(self, book_hash: str, image_bytes: bytes) -> None:
        """Store cover art blob in the books table."""
        conn = self._get_conn()
        conn.execute(
            """UPDATE books SET cover_art = ?, cover_art_size = ?, updated_at = ?
               WHERE book_hash = ?""",
            (image_bytes, len(image_bytes), _utcnow(), book_hash),
        )
        conn.commit()
        log.info(f"Stored cover art for {book_hash}: {len(image_bytes)} bytes")

    def get_cover(self, book_hash: str) -> bytes | None:
        """Read cover art blob. Returns None if no cover stored."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT cover_art FROM books WHERE book_hash = ?", (book_hash,)
        ).fetchone()
        if row is None or row["cover_art"] is None:
            return None
        return bytes(row["cover_art"])

    def extract_cover_to_file(self, book_hash: str, dest_dir: Path) -> Path | None:
        """Write cover art blob to a temp file for ffmpeg. Returns path or None."""
        cover_bytes = self.get_cover(book_hash)
        if cover_bytes is None:
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        cover_path = dest_dir / f"_cover_{book_hash[:8]}.jpg"
        cover_path.write_bytes(cover_bytes)
        log.debug(f"Extracted cover to {cover_path} ({len(cover_bytes)} bytes)")
        return cover_path

    # -- Author alias API --

    def get_alias(self, variant: str) -> str | None:
        """Look up canonical name for an author variant."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT canonical FROM author_aliases WHERE variant = ?", (variant,)
        ).fetchone()
        return row["canonical"] if row else None

    def save_alias(self, variant: str, canonical: str) -> None:
        """Save an author alias mapping."""
        if variant == canonical:
            return
        conn = self._get_conn()
        conn.execute(
            "INSERT OR REPLACE INTO author_aliases (variant, canonical) VALUES (?, ?)",
            (variant, canonical),
        )
        conn.commit()
        log.info(f"Author alias saved: '{variant}' -> '{canonical}'")

    def get_aliases_for(self, canonical: str) -> list[str]:
        """Get all variant names that map to a canonical author name."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT variant FROM author_aliases WHERE canonical = ?", (canonical,)
        ).fetchall()
        return [r["variant"] for r in rows]

    # -- Locking API --

    def acquire_reorganize_lock(self) -> bool:
        """Try to acquire the reorganize lock. Returns True if acquired."""
        conn = self._get_conn()
        try:
            conn.execute(
                """INSERT INTO pipeline_locks (lock_name, acquired_at, pid)
                   VALUES ('reorganize', ?, ?)""",
                (_utcnow(), os.getpid()),
            )
            conn.commit()
            log.info("Acquired reorganize lock")
            return True
        except sqlite3.IntegrityError:
            # Lock already held -- check if the holder is still alive
            row = conn.execute(
                "SELECT pid FROM pipeline_locks WHERE lock_name = 'reorganize'"
            ).fetchone()
            if row:
                try:
                    os.kill(row["pid"], 0)  # Check if process exists
                except OSError:
                    # Process is dead -- steal the lock
                    conn.execute(
                        """UPDATE pipeline_locks
                           SET acquired_at = ?, pid = ?
                           WHERE lock_name = 'reorganize'""",
                        (_utcnow(), os.getpid()),
                    )
                    conn.commit()
                    log.warning(f"Stole reorganize lock from dead pid {row['pid']}")
                    return True
            log.warning("Reorganize lock already held by active process")
            return False

    def release_reorganize_lock(self) -> None:
        """Release the reorganize lock."""
        conn = self._get_conn()
        conn.execute("DELETE FROM pipeline_locks WHERE lock_name = 'reorganize'")
        conn.commit()
        log.info("Released reorganize lock")

    # -- Batch operations --

    def reset_book(self, book_hash: str) -> None:
        """Delete a book and all its stage data (CASCADE)."""
        conn = self._get_conn()
        conn.execute("DELETE FROM books WHERE book_hash = ?", (book_hash,))
        conn.commit()

    def list_books(
        self, status: str | None = None, mode: str | None = None
    ) -> list[dict]:
        """List books, optionally filtered by status and/or mode."""
        conn = self._get_conn()
        query = "SELECT book_hash, source_path, mode, status FROM books"
        params: list[str] = []
        clauses: list[str] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if mode:
            clauses.append("mode = ?")
            params.append(mode)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
