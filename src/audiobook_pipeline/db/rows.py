"""Row shapes. One model per table, fields matching columns exactly.

Purpose:
    The bridge that makes an ORM unnecessary. Each model declares a table's
    shape once; the schema is generated from it, rows are validated out of it,
    and parameters are serialized into it. Table and type cannot drift, because
    there is only one declaration.

WHY THESE ARE NOT THE DOMAIN MODELS
    A row is not a book. ``BookRow`` carries ``retry_count``, ``error_stage``,
    and a flattened ``parsed_*`` prefix because that is what persistence needs;
    ``models.metadata.BookMetadata`` carries what a tag writer needs. Merging
    them would drag database concerns into the tagging path and vice versa --
    which is precisely how ORMs earn their reputation.

    The conversion between them is explicit, in one place, and visible.

Example:
    >>> BookRow(book_hash="abc123", source_path="/x", mode="convert").retry_count
    0

See Also:
    - audiobook_pipeline.db.connection: builds the schema from these
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> str:
    """Current UTC time as an ISO-8601 string.

    Timezone-AWARE, always. A naive datetime written to the database is
    ambiguous the moment the machine's timezone changes or the file is read
    somewhere else, and SQLite stores whatever string it is handed without
    complaint.
    """
    return datetime.now(UTC).isoformat()


class BookRow(BaseModel):
    """One book being processed, and everything known about its progress.

    Wide and flat because SQLite is: the ``parsed_*`` fields are the metadata
    as extracted, kept separate from the resolved values so a re-run can tell
    what was derived from the filename apart from what an API confirmed.
    """

    model_config = ConfigDict(extra="forbid")

    #: Content-derived identity, so the same book found at a different path is
    #: recognised as the same book rather than converted twice.
    book_hash: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    mode: str = Field(min_length=1)

    status: str = "pending"
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=0)

    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    # Failure detail. All optional: a book that has not failed has none of it,
    # and defaulting them to empty strings would make "no error" and "an error
    # with no message" indistinguishable.
    error_timestamp: str | None = None
    error_stage: str | None = None
    error_exit_code: int | None = None
    error_category: str | None = None
    error_message: str | None = None

    # What the audio turned out to be.
    target_bitrate: int | None = None
    file_count: int | None = None
    total_duration: float | None = None
    chapter_count: int | None = None

    #: Where the chapter table came from: embedded, audnexus, or file-per-
    #: chapter. Recorded because "0 chapters" and "chapters we generated"
    #: are different outcomes and the audit needs to tell them apart.
    chapter_source: str | None = None

    codec: str | None = None
    bitrate: str | None = None

    # Metadata as parsed, before resolution.
    parsed_author: str | None = None
    parsed_title: str | None = None
    parsed_series: str | None = None
    parsed_position: str | None = None
    parsed_asin: str | None = None
    parsed_narrator: str | None = None
    parsed_year: str | None = None
    parsed_subtitle: str | None = None
    parsed_description: str | None = None
    parsed_publisher: str | None = None
    parsed_copyright: str | None = None
    parsed_language: str | None = None
    parsed_genre: str | None = None

    cover_url: str | None = None
    cover_art_size: int | None = None

    # NOTE: the cover_art BLOB itself is deliberately NOT a field here, and
    # connection.EXTRA_COLUMNS declares it instead. It is a multi-megabyte
    # image, and a field would load it on every read -- so listing 700 books to
    # print a status table would pull the whole library's cover art through
    # sqlite3 in order to display none of it. It is reached only through
    # queries.store_cover / queries.get_cover, the two callers that want bytes.
    #
    # cover_art_size stays: "does this book have cover art, and how big" is
    # exactly what a status read asks, and it is 8 bytes instead of 8 MB.


class StageRow(BaseModel):
    """One stage's outcome for one book.

    Composite-keyed on (book_hash, stage): a book has at most one record per
    stage, and re-running a stage replaces its row rather than appending. An
    append-only log would grow without bound across retries and make "what is
    the current state" a query rather than a lookup.
    """

    model_config = ConfigDict(extra="forbid")

    book_hash: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    status: str = "pending"
    completed_at: str | None = None
    output_file: str | None = None
    dest_dir: str | None = None


class AuthorAliasRow(BaseModel):
    """A known author-name variant and what it should canonicalise to.

    Exists because sole-surname matching filed ``Michael Williams`` under
    ``Tad Williams``. The fix is not a cleverer matcher -- it is a recorded
    decision that survives the run that made it.
    """

    model_config = ConfigDict(extra="forbid")

    variant: str = Field(min_length=1)
    canonical: str = Field(min_length=1)


class LockRow(BaseModel):
    """An advisory lock held by a running pipeline process.

    Carries the PID so a stale lock left by a killed process can be identified
    as stale rather than blocking every future run forever.
    """

    model_config = ConfigDict(extra="forbid")

    lock_name: str = Field(min_length=1)
    acquired_at: str = Field(min_length=1)
    pid: int = Field(gt=0)
