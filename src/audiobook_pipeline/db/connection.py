"""Opening the database, creating its schema, and migrating it forward.

THE SCHEMA IS DERIVED FROM THE MODELS
    Column names and types come from the Pydantic models in ``rows``, not from
    a hand-maintained ``CREATE TABLE`` string. That is what makes "the table
    and the type cannot drift" true rather than aspirational: adding a field to
    ``BookRow`` adds the column, and there is no second place to forget.

MIGRATION IS ADDITIVE AND DRIVEN BY THE LIVE SCHEMA
    ``CREATE TABLE IF NOT EXISTS`` does NOTHING to a table that already exists,
    so a new field would be silently absent on every database created before it
    -- and the failure surfaces as an ``OperationalError: no such column`` in
    whatever query happens to touch it first, which may be weeks later.

    So the live columns are read back with ``PRAGMA table_info`` and anything
    missing is added. Additive only: no column is ever dropped or retyped,
    because either would destroy data in a file the user cannot easily
    reconstruct -- it holds the record of which of 700 books already converted.

Example:
    >>> _sql_type(int | None)
    'INTEGER'

See Also:
    - audiobook_pipeline.db.rows: the models this schema is generated from
"""

from __future__ import annotations

import sqlite3
import types
import typing
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from audiobook_pipeline.db.rows import AuthorAliasRow, BookRow, LockRow, StageRow

log = logger.bind(stage="db")

#: Python type -> SQLite column type. SQLite is dynamically typed, so these are
#: affinities rather than constraints, but declaring them correctly is what
#: makes `ORDER BY retry_count` sort numerically instead of lexically -- where
#: "10" sorts before "9".
_SQL_TYPES: dict[type, str] = {
    str: "TEXT",
    int: "INTEGER",
    float: "REAL",
    bool: "INTEGER",
    bytes: "BLOB",
}

#: Primary keys per table. Composite where the natural identity is a pair.
_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "books": ("book_hash",),
    "stages": ("book_hash", "stage"),
    "author_aliases": ("variant",),
    "pipeline_locks": ("lock_name",),
}

#: Table name -> the model that defines its shape.
TABLES: dict[str, type[BaseModel]] = {
    "books": BookRow,
    "stages": StageRow,
    "author_aliases": AuthorAliasRow,
    "pipeline_locks": LockRow,
}

#: Columns that exist in the schema but deliberately have no model field.
#:
#: There is exactly one, and it is here rather than on ``BookRow`` because a
#: model field would load a multi-megabyte cover image on every read. Declaring
#: it here keeps the schema complete and the migration honest -- without this
#: the migration would still not drop the column (it is additive only), but a
#: fresh database would never create it, so the same code would work on an old
#: file and fail on a new one.
EXTRA_COLUMNS: dict[str, dict[str, str]] = {
    "books": {"cover_art": "BLOB"},
}

#: Table constraints that are not columns. ON DELETE CASCADE is what makes
#: ``reset_book`` one statement instead of two that can half-succeed: deleting
#: a book takes its stage rows with it, so a reset can never leave stage records
#: pointing at a book that no longer exists.
_FOREIGN_KEYS: dict[str, tuple[str, ...]] = {
    "stages": (
        "FOREIGN KEY (book_hash) REFERENCES books(book_hash) ON DELETE CASCADE",
    ),
}


def _sql_type(annotation: object) -> str:
    """Map a model field's annotation to a SQLite column type.

    Unwraps ``X | None``, since nullability is expressed by the absence of
    ``NOT NULL`` rather than by the column type.

    Args:
        annotation: The field's type annotation.

    Returns:
        A SQLite type name. Falls back to TEXT for anything unrecognised --
        SQLite accepts any affinity, and TEXT round-trips a string form of
        almost anything rather than failing the write.
    """
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        annotation = args[0] if args else str

    return (
        _SQL_TYPES.get(annotation, "TEXT") if isinstance(annotation, type) else "TEXT"
    )


def model_columns(model: type[BaseModel]) -> tuple[str, ...]:
    """The column names a model maps to, in declaration order.

    Used by ``queries`` to build an explicit SELECT list. Explicit rather than
    ``SELECT *`` because the tables carry columns the models deliberately do not
    (see EXTRA_COLUMNS), and under ``extra="forbid"`` a star-select would hand
    ``model_validate`` a key it refuses.

    Args:
        model: The row model.

    Returns:
        Column names in model field order.
    """
    return tuple(model.model_fields)


def _sql_default(field: FieldInfo) -> str:
    """Render a model field's default as a SQL DEFAULT clause, if it has one.

    THIS IS WHAT MAKES THE MIGRATION SAFE FOR NON-OPTIONAL FIELDS.
    ``ALTER TABLE ADD COLUMN`` fills every existing row with NULL. For a field
    the model declares as ``int`` rather than ``int | None``, that NULL fails
    validation on the next read -- so adding ``retry_count`` to a database
    holding 700 books would make every one of them unreadable.

    Measured 2026-08-02: without this, ``test_missing_column_is_added_and_rows
    _survive`` fails with ``retry_count: Input should be a valid integer
    [input_value=None]``.

    Args:
        field: The model field.

    Returns:
        A ``DEFAULT <literal>`` clause, or an empty string when the field has
        no static default. ``default_factory`` fields (the timestamps) are
        deliberately excluded: their value is computed per row, and freezing
        one migration's clock into the schema would stamp every future insert
        with the moment the column was added.
    """
    default = field.default
    if field.default_factory is not None or default is PydanticUndefined:
        return ""
    if default is None:
        return ""
    if isinstance(default, bool):
        return f" DEFAULT {int(default)}"
    if isinstance(default, (int, float)):
        return f" DEFAULT {default}"
    return " DEFAULT " + "'" + str(default).replace("'", "''") + "'"


def _columns_for(table: str, model: type[BaseModel]) -> dict[str, str]:
    """Build the full column name -> SQL type mapping for one table.

    Args:
        table: Table name, used to look up any non-model columns.
        model: The row model.

    Returns:
        Ordered mapping of every column in the table to its SQLite type,
        including a DEFAULT clause where the model declares one.
    """
    columns = {
        name: _sql_type(field.annotation) + _sql_default(field)
        for name, field in model.model_fields.items()
    }
    columns.update(EXTRA_COLUMNS.get(table, {}))
    return columns


def _create_statement(table: str, model: type[BaseModel]) -> str:
    """Render the CREATE TABLE statement for one model.

    Args:
        table: Table name.
        model: The model defining its columns.

    Returns:
        A complete ``CREATE TABLE IF NOT EXISTS`` statement.
    """
    keys = _PRIMARY_KEYS[table]
    columns = [
        f"    {name} {sql_type}"
        for name, sql_type in _columns_for(table, model).items()
    ]
    columns.append(f"    PRIMARY KEY ({', '.join(keys)})")
    columns.extend(f"    {clause}" for clause in _FOREIGN_KEYS.get(table, ()))
    return f"CREATE TABLE IF NOT EXISTS {table} (\n" + ",\n".join(columns) + "\n)"


def _migrate_table(conn: sqlite3.Connection, table: str, model: type[BaseModel]) -> int:
    """Add any columns the model declares that the live table lacks.

    Args:
        conn: Open connection.
        table: Table to migrate.
        model: The model defining the target shape.

    Returns:
        How many columns were added. Returned rather than logged only, so a
        caller can assert a migration did something -- a migration that
        silently adds nothing looks identical to one that was not needed.
    """
    live = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    added = 0
    for name, sql_type in _columns_for(table, model).items():
        if name in live:
            continue
        # No DEFAULT and no NOT NULL: SQLite requires a constant default when
        # adding a NOT NULL column, and inventing one would write a value
        # nobody chose into every existing row.
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")
        log.info("added column {}.{} {}", table, name, sql_type)
        added += 1
    return added


def initialize(conn: sqlite3.Connection) -> None:
    """Create every table and bring existing ones up to the current shape.

    Safe to call on every open: creation is ``IF NOT EXISTS`` and migration
    only adds what is missing.

    Args:
        conn: Open connection.
    """
    for table, model in TABLES.items():
        conn.execute(_create_statement(table, model))
        _migrate_table(conn, table, model)
    conn.commit()


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open the pipeline database, initialized and configured.

    Args:
        db_path: Path to the SQLite file. Its parent is created if absent.

    Yields:
        A connection with the schema present and pragmas applied.

    Example:
        >>> import tempfile, pathlib
        >>> with tempfile.TemporaryDirectory() as d:
        ...     with connect(pathlib.Path(d) / "p.db") as conn:
        ...         _ = conn.execute("SELECT 1").fetchone()
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    # Rows come back addressable by column name, which is what lets the row
    # factory hand a dict straight to model_validate. Without it a row is a
    # positional tuple and every read depends on SELECT column order.
    conn.row_factory = sqlite3.Row

    # WAL: a reader does not block the writer. The audit command reads the
    # database while a conversion is running, and under the default journal
    # mode that is a "database is locked" error rather than a report.
    conn.execute("PRAGMA journal_mode=WAL")
    # Wait rather than fail immediately when the write lock is briefly held.
    conn.execute("PRAGMA busy_timeout=5000")
    # OFF by default in SQLite, which makes the stages->books foreign key
    # decorative: deleting a book would leave its stage rows behind forever.
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        initialize(conn)
        yield conn
    finally:
        conn.close()
