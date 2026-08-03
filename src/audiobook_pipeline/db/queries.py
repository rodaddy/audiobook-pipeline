"""Every SQL statement the pipeline runs. One named function each.

Purpose:
    The SQL lives here and nowhere else. A stage that wants a book calls
    ``get_book``; it never holds a cursor, never sees a dict, and never writes a
    ``SELECT``. That is the whole boundary: models out, models in, SQL confined
    to one module small enough to read in full.

WHY EVERY SELECT NAMES ITS COLUMNS
    ``SELECT *`` looks harmless and is not. The tables carry columns the models
    deliberately do not -- ``books.cover_art`` is a multi-megabyte BLOB -- so a
    star-select would both drag the image into memory and hand
    ``model_validate`` a key that ``extra="forbid"`` refuses. The column list
    comes from the model itself via ``model_columns``, so it cannot drift.

Example:
    >>> from audiobook_pipeline.db.rows import BookRow
    >>> _select_list(BookRow).startswith('book_hash, source_path')
    True

See Also:
    - audiobook_pipeline.db.connection: opens the connection these take
"""

from __future__ import annotations

import os
import sqlite3
from typing import TypeVar

from loguru import logger
from pydantic import BaseModel

from audiobook_pipeline.db.connection import model_columns
from audiobook_pipeline.db.rows import BookRow, LockRow, StageRow, utc_now

log = logger.bind(stage="db")

ModelT = TypeVar("ModelT", bound=BaseModel)

#: The one lock the pipeline takes. Named rather than parameterised because
#: there is exactly one section that must not run twice -- reorganising the
#: library -- and inventing a general lock manager for one caller is the
#: speculative generality the standard warns about.
REORGANIZE_LOCK = "reorganize"


def _select_list(model: type[BaseModel]) -> str:
    """Render a model's columns as a SELECT list.

    Args:
        model: The row model.

    Returns:
        Comma-separated column names.
    """
    return ", ".join(model_columns(model))


def _one(cursor: sqlite3.Cursor, model: type[ModelT]) -> ModelT | None:
    """Validate at most one row into a model.

    Args:
        cursor: A cursor positioned on a completed query.
        model: The model to validate into.

    Returns:
        The model, or None when the query matched nothing.
    """
    row = cursor.fetchone()
    return model.model_validate(dict(row)) if row is not None else None


def _all(cursor: sqlite3.Cursor, model: type[ModelT]) -> list[ModelT]:
    """Validate every row into models.

    Args:
        cursor: A cursor positioned on a completed query.
        model: The model to validate into.

    Returns:
        One model per row, in query order.
    """
    return [model.model_validate(dict(row)) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# books
# ---------------------------------------------------------------------------


def upsert_book(conn: sqlite3.Connection, book: BookRow) -> None:
    """Insert a book, or update the record if its hash is already known.

    ON CONFLICT DO UPDATE, **never** INSERT OR REPLACE.

    REPLACE is implemented as DELETE-then-INSERT, and ``stages`` references
    ``books`` with ON DELETE CASCADE -- so re-upserting a known book silently
    destroys every stage row recorded against it. Both halves are individually
    correct (a reset should take its stages with it; a re-run should pick up a
    corrected parse) and together they erase the pipeline's own progress
    record.

    Measured 2026-08-02 on the live end-to-end re-run: an already-completed
    book re-ran all eight stages and wrote a SECOND copy into the library,
    because ``process_book`` upserts the row before reading which stages are
    done. Over a 700-book library that duplicates the whole thing. Only the
    real re-run surfaced it -- every stage was individually correct, and the
    stage rows were present right up until the next upsert.

    Args:
        conn: Open connection.
        book: The record to write.
    """
    columns = model_columns(BookRow)
    placeholders = ", ".join("?" for _ in columns)
    values = tuple(getattr(book, name) for name in columns)
    updates = ", ".join(
        f"{name} = excluded.{name}" for name in columns if name != "book_hash"
    )
    conn.execute(
        f"INSERT INTO books ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(book_hash) DO UPDATE SET {updates}",
        values,
    )
    conn.commit()


def get_book(conn: sqlite3.Connection, book_hash: str) -> BookRow | None:
    """Read one book.

    Args:
        conn: Open connection.
        book_hash: Content hash identifying the book.

    Returns:
        The record, or None if this book has never been seen.
    """
    cursor = conn.execute(
        f"SELECT {_select_list(BookRow)} FROM books WHERE book_hash = ?",
        (book_hash,),
    )
    return _one(cursor, BookRow)


def update_book(conn: sqlite3.Connection, book: BookRow) -> None:
    """Write a modified book record back, stamping ``updated_at``.

    Takes the whole model rather than a field/value pair. A partial update API
    accepts a column name as a string, which is how a typo becomes a silent
    no-op -- ``UPDATE books SET parsd_title = ?`` is a syntax error, but
    ``update(hash, {"parsd_title": x})`` in the old code just built one.

    Args:
        conn: Open connection.
        book: The record to persist. Its ``updated_at`` is overwritten.
    """
    stamped = book.model_copy(update={"updated_at": utc_now()})
    columns = [name for name in model_columns(BookRow) if name != "book_hash"]
    assignments = ", ".join(f"{name} = ?" for name in columns)
    values = tuple(getattr(stamped, name) for name in columns)
    conn.execute(
        f"UPDATE books SET {assignments} WHERE book_hash = ?",
        (*values, stamped.book_hash),
    )
    conn.commit()


def list_books(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    mode: str | None = None,
) -> list[BookRow]:
    """List books, optionally filtered.

    Args:
        conn: Open connection.
        status: Restrict to one status when given.
        mode: Restrict to one pipeline mode when given.

    Returns:
        Matching records, oldest first.
    """
    clauses: list[str] = []
    params: list[str] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if mode is not None:
        clauses.append("mode = ?")
        params.append(mode)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    cursor = conn.execute(
        f"SELECT {_select_list(BookRow)} FROM books{where} ORDER BY created_at",
        tuple(params),
    )
    return _all(cursor, BookRow)


def delete_book(conn: sqlite3.Connection, book_hash: str) -> None:
    """Delete a book and, by cascade, its stage rows.

    Args:
        conn: Open connection.
        book_hash: The book to remove.
    """
    conn.execute("DELETE FROM books WHERE book_hash = ?", (book_hash,))
    conn.commit()
    log.info("reset book {}", book_hash)


def increment_retry(conn: sqlite3.Connection, book_hash: str) -> int:
    """Record one more attempt against a book.

    Args:
        conn: Open connection.
        book_hash: The book being retried.

    Returns:
        The new retry count. Returned rather than written and forgotten,
        because the caller's next decision is whether it has hit max_retries.
    """
    conn.execute(
        "UPDATE books SET retry_count = retry_count + 1, updated_at = ? "
        "WHERE book_hash = ?",
        (utc_now(), book_hash),
    )
    conn.commit()
    row = conn.execute(
        "SELECT retry_count FROM books WHERE book_hash = ?", (book_hash,)
    ).fetchone()
    return int(row["retry_count"]) if row is not None else 0


# ---------------------------------------------------------------------------
# cover art -- the BLOB kept off BookRow
# ---------------------------------------------------------------------------


def store_cover(conn: sqlite3.Connection, book_hash: str, image: bytes) -> None:
    """Store cover art against a book.

    Args:
        conn: Open connection.
        book_hash: The book the art belongs to.
        image: Encoded image bytes.
    """
    conn.execute(
        "UPDATE books SET cover_art = ?, cover_art_size = ?, updated_at = ? "
        "WHERE book_hash = ?",
        (image, len(image), utc_now(), book_hash),
    )
    conn.commit()
    log.debug("stored {} bytes of cover art for {}", len(image), book_hash)


def get_cover(conn: sqlite3.Connection, book_hash: str) -> bytes | None:
    """Read cover art.

    Args:
        conn: Open connection.
        book_hash: The book to read art for.

    Returns:
        The image bytes, or None when the book has no cover stored.
    """
    row = conn.execute(
        "SELECT cover_art FROM books WHERE book_hash = ?", (book_hash,)
    ).fetchone()
    if row is None or row["cover_art"] is None:
        return None
    return bytes(row["cover_art"])


# ---------------------------------------------------------------------------
# stages
# ---------------------------------------------------------------------------


def set_stage(conn: sqlite3.Connection, stage_row: StageRow) -> None:
    """Record a stage's outcome, replacing any previous record for it.

    Args:
        conn: Open connection.
        stage_row: The stage record to write.
    """
    columns = model_columns(StageRow)
    placeholders = ", ".join("?" for _ in columns)
    values = tuple(getattr(stage_row, name) for name in columns)
    conn.execute(
        f"INSERT OR REPLACE INTO stages ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()


def get_stages(conn: sqlite3.Connection, book_hash: str) -> list[StageRow]:
    """Read every recorded stage for one book.

    Args:
        conn: Open connection.
        book_hash: The book to read stages for.

    Returns:
        Stage records. Absence means never run, not failed.
    """
    cursor = conn.execute(
        f"SELECT {_select_list(StageRow)} FROM stages WHERE book_hash = ?",
        (book_hash,),
    )
    return _all(cursor, StageRow)


def completed_stages(conn: sqlite3.Connection, book_hash: str) -> set[str]:
    """The names of stages this book has finished.

    Args:
        conn: Open connection.
        book_hash: The book to check.

    Returns:
        Stage names with status ``completed``. A set, because the only question
        asked of it is membership -- "has convert run yet".
    """
    rows = conn.execute(
        "SELECT stage FROM stages WHERE book_hash = ? AND status = 'completed'",
        (book_hash,),
    ).fetchall()
    return {row["stage"] for row in rows}


# ---------------------------------------------------------------------------
# author aliases
# ---------------------------------------------------------------------------


def save_alias(conn: sqlite3.Connection, variant: str, canonical: str) -> None:
    """Record that an author-name variant means a canonical name.

    A variant equal to its canonical is not stored: it is the identity mapping,
    it teaches nothing, and storing it would grow a row per author seen.

    Args:
        conn: Open connection.
        variant: The name as encountered.
        canonical: The name it should be filed under.
    """
    if variant == canonical:
        return
    conn.execute(
        "INSERT OR REPLACE INTO author_aliases (variant, canonical) VALUES (?, ?)",
        (variant, canonical),
    )
    conn.commit()
    log.info("author alias {!r} -> {!r}", variant, canonical)


def get_alias(conn: sqlite3.Connection, variant: str) -> str | None:
    """Resolve an author-name variant to its canonical name.

    Args:
        conn: Open connection.
        variant: The name as encountered.

    Returns:
        The canonical name, or None when this variant is unknown -- which means
        "use the name as given", not "error".
    """
    row = conn.execute(
        "SELECT canonical FROM author_aliases WHERE variant = ?", (variant,)
    ).fetchone()
    return str(row["canonical"]) if row is not None else None


def get_variants(conn: sqlite3.Connection, canonical: str) -> list[str]:
    """Every recorded variant of one canonical author name.

    Args:
        conn: Open connection.
        canonical: The canonical name.

    Returns:
        Known variants, alphabetically.
    """
    rows = conn.execute(
        "SELECT variant FROM author_aliases WHERE canonical = ? ORDER BY variant",
        (canonical,),
    ).fetchall()
    return [str(row["variant"]) for row in rows]


# ---------------------------------------------------------------------------
# locking
# ---------------------------------------------------------------------------


def _holder_is_alive(pid: int) -> bool:
    """Whether a process is still running.

    Args:
        pid: Process id recorded by the lock holder.

    Returns:
        True if the process exists. ``signal 0`` performs the permission and
        existence checks without delivering anything.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists, owned by someone else. Alive as far as this matters -- and
        # NOT stealable, which is why this is distinguished from not-found
        # rather than folded into a bare `except OSError`.
        return True
    return True


def acquire_lock(conn: sqlite3.Connection, name: str = REORGANIZE_LOCK) -> bool:
    """Take the named lock, stealing it from a dead holder.

    Stealing is the point. Without it a pipeline killed mid-reorganise leaves a
    row that blocks every future run forever, and the fix is manual SQL against
    a database the user should never have to open.

    Args:
        conn: Open connection.
        name: Lock name.

    Returns:
        True when the lock is held by this process.
    """
    try:
        conn.execute(
            "INSERT INTO pipeline_locks (lock_name, acquired_at, pid) VALUES (?, ?, ?)",
            (name, utc_now(), os.getpid()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return _steal_if_dead(conn, name)
    else:
        return True


def _steal_if_dead(conn: sqlite3.Connection, name: str) -> bool:
    """Take over a lock whose holder is gone.

    Args:
        conn: Open connection.
        name: Lock name.

    Returns:
        True when the lock was stolen; False when the holder is alive.
    """
    row = conn.execute(
        "SELECT pid FROM pipeline_locks WHERE lock_name = ?", (name,)
    ).fetchone()
    if row is None or _holder_is_alive(int(row["pid"])):
        log.warning("lock {!r} held by a live process", name)
        return False

    conn.execute(
        "UPDATE pipeline_locks SET acquired_at = ?, pid = ? WHERE lock_name = ?",
        (utc_now(), os.getpid(), name),
    )
    conn.commit()
    log.warning("stole lock {!r} from dead pid {}", name, row["pid"])
    return True


def release_lock(conn: sqlite3.Connection, name: str = REORGANIZE_LOCK) -> None:
    """Release the named lock.

    Args:
        conn: Open connection.
        name: Lock name.
    """
    conn.execute("DELETE FROM pipeline_locks WHERE lock_name = ?", (name,))
    conn.commit()


def get_lock(conn: sqlite3.Connection, name: str = REORGANIZE_LOCK) -> LockRow | None:
    """Read the current holder of a lock.

    Args:
        conn: Open connection.
        name: Lock name.

    Returns:
        The lock record, or None when nobody holds it.
    """
    cursor = conn.execute(
        f"SELECT {_select_list(LockRow)} FROM pipeline_locks WHERE lock_name = ?",
        (name,),
    )
    return _one(cursor, LockRow)
