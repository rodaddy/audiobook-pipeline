"""SQLite state, through stdlib ``sqlite3`` and a Pydantic row factory. No ORM.

Purpose:
    A conversion run is resumable. Which books exist, which stage each reached,
    what failed and whether it is worth retrying -- all of it survives the
    process, because a batch of 700 books takes days and something will
    interrupt it.

WHY THERE IS NO ORM HERE
    The problem with the pre-rewrite ``pipeline_db.py`` was never that it used
    SQL. It was that rows crossed the boundary as unvalidated dicts -- 10 of
    the 68 ``dict[str, Any]`` sites in the codebase were in that one file, so a
    caller reading a book record knew only what the query happened to select.

    An ORM fixes that as a side effect of session management, identity maps,
    lazy loading, and a migration graph. A row factory fixes exactly that and
    nothing else: ``model_validate`` on the way out, ``model_dump`` on the way
    in, one declaration of shape. SQLAlchemy's surface -- detached instances,
    flush-versus-commit timing, N+1 from lazy loads -- is real cost paid for
    capability this application does not use. It is a single-writer local file
    with no relationships to traverse.

    This is NOT licence to grow a hand-rolled ORM. The factory validates and
    serializes. The moment it wants a query builder or relationship handling,
    the data model has outgrown SQLite and the answer is a real database.

Key Components:
    - connection: ``connect`` (pragmas WAL/busy_timeout/foreign_keys),
      ``initialize``, and the additive migration. The schema is GENERATED from
      the row models, so a new field becomes a new column with no second place
      to update. ``EXTRA_COLUMNS`` declares the one column with no model field
      (``books.cover_art``, a BLOB kept off the model so a status read does not
      load it); ``model_columns`` gives ``queries`` its explicit SELECT list.
    - rows: ``BookRow``, ``StageRow``, ``AuthorAliasRow``, ``LockRow`` --
      fields matching columns exactly, all ``extra="forbid"``. ``utc_now``
      stamps timezone-aware ISO-8601.
    - queries: the SQL, one named function per statement. Book CRUD plus
      ``increment_retry``; ``store_cover``/``get_cover`` for the BLOB;
      ``set_stage``/``completed_stages``; alias save/lookup; and the
      ``acquire_lock``/``release_lock`` pair that STEALS a lock from a dead
      holder rather than blocking every future run forever.

Pattern/Convention:
    Callers receive models, never cursors or dicts::

        with connect(config.paths.db_path) as conn:
            book = get_book(conn, book_hash)   # BookRow | None

    No SELECT is written outside this package, and no ``SELECT *`` inside it.

Example:
    >>> from audiobook_pipeline.db.rows import BookRow
    >>> BookRow(book_hash="abc", source_path="/x", mode="convert").status
    'pending'

See Also:
    - _plans/python-rewrite-sequence.md ## Why no ORM
"""

from __future__ import annotations
