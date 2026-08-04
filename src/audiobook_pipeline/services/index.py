"""SQLite-backed library index with transactional destination reservations."""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from loguru import logger

from audiobook_pipeline.models.index import (
    ClaimState,
    DestinationIdentity,
    FolderIdentity,
    IndexCounts,
    IndexedFile,
    IndexOptions,
    Reservation,
    ReservationClaim,
    SourceIdentity,
    normalize_text_key,
)
from audiobook_pipeline.services.names import normalize_author
from audiobook_pipeline.utils.text import (
    fold_accents,
    strip_brackets,
    strip_punctuation,
    strip_year,
)

log = logger.bind(stage="library_index")


class IndexReservationError(ValueError):
    """A caller attempted an invalid reservation state transition."""


_DESTINATION_MISMATCH_MESSAGE = "reservation and file destination must match"
_TIMEZONE_MESSAGE = "reservation time must be timezone-aware"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexed_folders (
    parent_key TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (parent_key, name_key)
);
CREATE TABLE IF NOT EXISTS indexed_files (
    destination_key TEXT PRIMARY KEY,
    directory_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    claim_id TEXT
);
CREATE TABLE IF NOT EXISTS author_aliases (
    variant_key TEXT PRIMARY KEY,
    canonical TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS destination_reservations (
    destination_key TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    claim_id TEXT NOT NULL UNIQUE,
    claimed_at TEXT NOT NULL
);
"""

_INITIAL_RUN = re.compile(r"\b(?:[a-z]\s+){1,}[a-z]\b")
_ROLE_SUFFIX = re.compile(
    r",?\s+\w+\s*-\s*(editor|translator|narrator|foreword|introduction)\b.*$",
    re.IGNORECASE,
)


class IndexStore(Protocol):
    """Persistence boundary the coordinator needs, allowing injected stores."""

    def folder_names(self, parent: Path) -> tuple[str, ...]:
        """List registered direct child folder names."""
        ...

    def file_exists(self, destination: DestinationIdentity) -> bool:
        """Report whether a destination is registered."""
        ...

    def counts(self) -> IndexCounts:
        """Report the number of durable folder and file entries."""
        ...

    def register_folder(self, folder: FolderIdentity) -> None:
        """Idempotently record a folder."""
        ...

    def register_file(self, file: IndexedFile) -> None:
        """Idempotently record a finished file."""
        ...

    def get_alias(self, variant: str) -> str | None:
        """Retrieve an author spelling alias when one exists."""
        ...

    def save_alias(self, variant: str, canonical: str) -> None:
        """Persist a spelling alias."""
        ...

    def claim(
        self,
        destination: DestinationIdentity,
        source: SourceIdentity,
        now: datetime,
    ) -> ReservationClaim:
        """Atomically attempt to reserve one destination."""
        ...

    def release(self, reservation: Reservation) -> bool:
        """Release a reservation when its claim ID still owns it."""
        ...

    def commit(self, reservation: Reservation, file: IndexedFile) -> bool:
        """Atomically register a file from its owning reservation."""
        ...

    def register_scan(
        self, folders: Iterable[FolderIdentity], files: Iterable[IndexedFile]
    ) -> None:
        """Idempotently insert a library scan."""
        ...


def sqlite_connection_factory(
    database_path: Path, options: IndexOptions
) -> Callable[[], sqlite3.Connection]:
    """Return a factory that creates one configured SQLite connection per call."""

    def connect() -> sqlite3.Connection:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, isolation_level=None)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(f"PRAGMA busy_timeout={options.busy_timeout_ms}")
        return connection

    return connect


class SQLiteIndexStore:
    """A standard-library SQLite store that never retains a worker connection."""

    def __init__(
        self,
        connection_factory: Callable[[], sqlite3.Connection],
        options: IndexOptions,
    ) -> None:
        """Create the store and initialize its private index schema."""
        self._connection_factory = connection_factory
        self._options = options
        self._initialize()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection_factory()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.executescript(_SCHEMA)

    def folder_names(self, parent: Path) -> tuple[str, ...]:
        """List registered direct child folder names."""
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT name FROM indexed_folders WHERE parent_key = ? ORDER BY name",
                (FolderIdentity(parent=parent, name="index").parent_key,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def file_exists(self, destination: DestinationIdentity) -> bool:
        """Report whether a destination is registered."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT 1 FROM indexed_files WHERE destination_key = ?",
                (destination.key,),
            ).fetchone()
        return row is not None

    def counts(self) -> IndexCounts:
        """Report the number of durable folder and file entries."""
        with self._transaction() as connection:
            folders = connection.execute(
                "SELECT COUNT(*) FROM indexed_folders"
            ).fetchone()
            files = connection.execute("SELECT COUNT(*) FROM indexed_files").fetchone()
        return IndexCounts(folders=int(folders[0]), files=int(files[0]))

    def register_folder(self, folder: FolderIdentity) -> None:
        """Idempotently record a folder."""
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO indexed_folders (parent_key, name_key, name) VALUES (?, ?, ?)
                ON CONFLICT(parent_key, name_key) DO UPDATE SET name = excluded.name""",
                (folder.parent_key, folder.name_key, folder.name),
            )

    def register_file(self, file: IndexedFile) -> None:
        """Idempotently record a finished file."""
        self._insert_file(file)

    def _insert_file(self, file: IndexedFile) -> None:
        with self._transaction() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO indexed_files
                (destination_key, directory_key, filename) VALUES (?, ?, ?)""",
                (
                    file.destination.key,
                    file.destination.directory_key,
                    file.destination.filename,
                ),
            )

    def get_alias(self, variant: str) -> str | None:
        """Retrieve an author spelling alias when one exists."""
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT canonical FROM author_aliases WHERE variant_key = ?",
                (normalize_text_key(variant),),
            ).fetchone()
        return str(row[0]) if row is not None else None

    def save_alias(self, variant: str, canonical: str) -> None:
        """Persist a spelling alias."""
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO author_aliases (variant_key, canonical) VALUES (?, ?)
                ON CONFLICT(variant_key) DO UPDATE SET canonical = excluded.canonical""",
                (normalize_text_key(variant), canonical),
            )

    def claim(
        self, destination: DestinationIdentity, source: SourceIdentity, now: datetime
    ) -> ReservationClaim:
        """Atomically attempt to reserve one destination."""
        with self._transaction() as connection:
            if self._is_registered(connection, destination):
                return ReservationClaim(state=ClaimState.REGISTERED)
            self._release_stale(connection, now)
            claim_id = str(uuid.uuid4())
            cursor = connection.execute(
                """INSERT OR IGNORE INTO destination_reservations
                (destination_key, source_key, claim_id, claimed_at) VALUES (?, ?, ?, ?)""",
                (destination.key, source.key, claim_id, _format_time(now)),
            )
            if cursor.rowcount == 0:
                return ReservationClaim(state=ClaimState.RESERVED)
        reservation = Reservation(
            destination=destination,
            source=source,
            claim_id=claim_id,
            claimed_at=now,
        )
        log.debug("library_index.destination_claimed")
        return ReservationClaim(state=ClaimState.CLAIMED, reservation=reservation)

    def release(self, reservation: Reservation) -> bool:
        """Release a reservation when its claim ID still owns it."""
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM destination_reservations WHERE destination_key = ? AND claim_id = ?",
                (reservation.destination.key, reservation.claim_id),
            )
        return cursor.rowcount == 1

    def commit(self, reservation: Reservation, file: IndexedFile) -> bool:
        """Atomically register a file from its owning reservation."""
        if reservation.destination != file.destination:
            raise IndexReservationError(_DESTINATION_MISMATCH_MESSAGE)
        with self._transaction() as connection:
            if self._is_registered(connection, file.destination):
                return (
                    self._committed_claim_id(connection, file.destination)
                    == reservation.claim_id
                )
            cursor = connection.execute(
                """DELETE FROM destination_reservations
                WHERE destination_key = ? AND claim_id = ?""",
                (reservation.destination.key, reservation.claim_id),
            )
            if cursor.rowcount != 1:
                return False
            connection.execute(
                """INSERT INTO indexed_files
                (destination_key, directory_key, filename, claim_id)
                VALUES (?, ?, ?, ?)""",
                (
                    file.destination.key,
                    file.destination.directory_key,
                    file.destination.filename,
                    reservation.claim_id,
                ),
            )
        log.debug("library_index.destination_committed")
        return True

    def register_scan(
        self, folders: Iterable[FolderIdentity], files: Iterable[IndexedFile]
    ) -> None:
        """Idempotently insert a library scan."""
        folder_rows = [(item.parent_key, item.name_key, item.name) for item in folders]
        file_rows = [
            (
                item.destination.key,
                item.destination.directory_key,
                item.destination.filename,
            )
            for item in files
        ]
        with self._transaction() as connection:
            connection.executemany(
                """INSERT INTO indexed_folders (parent_key, name_key, name) VALUES (?, ?, ?)
                ON CONFLICT(parent_key, name_key) DO UPDATE SET name = excluded.name""",
                folder_rows,
            )
            connection.executemany(
                """INSERT OR IGNORE INTO indexed_files
                (destination_key, directory_key, filename) VALUES (?, ?, ?)""",
                file_rows,
            )

    def _is_registered(
        self, connection: sqlite3.Connection, destination: DestinationIdentity
    ) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM indexed_files WHERE destination_key = ?",
                (destination.key,),
            ).fetchone()
            is not None
        )

    def _committed_claim_id(
        self, connection: sqlite3.Connection, destination: DestinationIdentity
    ) -> str | None:
        row = connection.execute(
            "SELECT claim_id FROM indexed_files WHERE destination_key = ?",
            (destination.key,),
        ).fetchone()
        return None if row is None else row[0]

    def _release_stale(self, connection: sqlite3.Connection, now: datetime) -> None:
        age = self._options.stale_reservation_after_seconds
        if age is None:
            return
        cutoff = now - timedelta(seconds=age)
        connection.execute(
            "DELETE FROM destination_reservations WHERE claimed_at <= ?",
            (_format_time(cutoff),),
        )


class LibraryIndex:
    """Coordinator-facing durable index with narrow register and claim APIs."""

    def __init__(self, library_root: Path, store: IndexStore) -> None:
        """Create a coordinator scoped to one absolute library root."""
        self._library_root = library_root
        self._store = store
        self._processed: set[str] = set()

    def scan(self) -> None:
        """Index the current library tree without changing any library content."""
        folders: list[FolderIdentity] = []
        files: list[IndexedFile] = []
        if not self._library_root.is_dir():
            log.debug("library_index.scan_skipped reason=missing_root")
            return
        for directory, child_dirs, child_files in os.walk(self._library_root):
            parent = Path(directory)
            folders.extend(
                FolderIdentity(parent=parent, name=name) for name in child_dirs
            )
            files.extend(
                IndexedFile(
                    destination=DestinationIdentity(directory=parent, filename=name)
                )
                for name in child_files
            )
        self._store.register_scan(folders, files)
        log.info(
            "library_index.scan_completed folders={} files={}", len(folders), len(files)
        )

    def reuse_existing(self, parent: Path, desired: str) -> str:
        """Return an existing near-match sibling name or the requested name."""
        desired_key = _folder_key(desired)
        for existing in self._store.folder_names(parent):
            if _near_match(desired_key, _folder_key(existing)):
                log.debug("library_index.folder_reused")
                return existing
        return desired

    def file_exists(self, destination: DestinationIdentity) -> bool:
        """Return whether a finished destination has been registered."""
        return self._store.file_exists(destination)

    @property
    def folder_count(self) -> int:
        """The number of registered folders, matching the legacy index surface."""
        return self._store.counts().folders

    @property
    def file_count(self) -> int:
        """The number of registered files, matching the legacy index surface."""
        return self._store.counts().files

    def mark_processed(self, source: SourceIdentity) -> bool:
        """Mark a source once, returning true when it was already processed."""
        if source.key in self._processed:
            return True
        self._processed.add(source.key)
        return False

    def register_folder(self, folder: FolderIdentity) -> None:
        """Idempotently register a folder produced after the initial scan."""
        self._store.register_folder(folder)

    def register_file(self, file: IndexedFile) -> None:
        """Idempotently register a finished file produced without a reservation."""
        self._store.register_file(file)

    def register_destination(self, file: IndexedFile) -> None:
        """Register a committed file and every new folder below the library root."""
        parent = self._library_root
        try:
            parts = file.destination.directory.relative_to(parent).parts
        except ValueError as error:
            raise IndexReservationError(_DESTINATION_MISMATCH_MESSAGE) from error
        for name in parts:
            self.register_folder(FolderIdentity(parent=parent, name=name))
            parent /= name
        self.register_file(file)

    def is_correctly_placed(
        self, source: Path, destination: DestinationIdentity
    ) -> bool:
        """Return whether source and destination name the same filesystem object."""
        try:
            return source.samefile(destination.path)
        except OSError:
            log.warning(
                "library_index.placement_check_failed reason=not_found_or_os_error"
            )
            return False

    def claim(
        self, destination: DestinationIdentity, source: SourceIdentity, now: datetime
    ) -> ReservationClaim:
        """Atomically reserve a free output destination for one source."""
        return self._store.claim(destination, source, now)

    def release(self, reservation: Reservation) -> bool:
        """Release an uncommitted reservation held by its original claim ID."""
        return self._store.release(reservation)

    def commit(self, reservation: Reservation, file: IndexedFile) -> bool:
        """Atomically turn an owned reservation into a registered output file."""
        return self._store.commit(reservation, file)

    def match_author(self, desired: str) -> str:
        """Reuse a persisted alias or a compatible existing top-level author."""
        alias = self._store.get_alias(desired)
        if alias is not None:
            return alias
        cleaned = _clean_author(desired)
        for existing in self._store.folder_names(self._library_root):
            if _authors_compatible(cleaned, existing):
                self._store.save_alias(desired, existing)
                return existing
        return desired

    def register_author(self, author: str) -> None:
        """Register a top-level author folder for subsequent canonicalization."""
        self.register_folder(FolderIdentity(parent=self._library_root, name=author))


def _format_time(value: datetime) -> str:
    """Render a timezone-aware value in sortable UTC form."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise IndexReservationError(_TIMEZONE_MESSAGE)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _folder_key(value: str) -> str:
    """Mirror the legacy folder comparison key without importing private helpers."""
    normalized = strip_punctuation(strip_year(fold_accents(value).lower()))
    joined = _INITIAL_RUN.sub(lambda match: match.group(0).replace(" ", ""), normalized)
    return joined[:-1] if joined.endswith("s") else joined


def _near_match(left: str, right: str) -> bool:
    """Apply the legacy token-overlap rule for folder-name reuse."""
    if left == right:
        return True
    left_tokens, right_tokens = set(left.split()), set(right.split())
    smaller, larger = sorted((left_tokens, right_tokens), key=len)
    stop_words = frozenset({"a", "an", "and", "the", "of", "for", "to"})
    if smaller <= larger and smaller - stop_words and larger - smaller <= stop_words:
        return True
    union = left_tokens | right_tokens
    return bool(union and len(left_tokens & right_tokens) / len(union) >= 0.8)


def _clean_author(value: str) -> str:
    """Remove legacy role suffixes and parenthesized role labels."""
    cleaned = _ROLE_SUFFIX.sub("", value)
    return strip_brackets(cleaned).strip()


def _authors_compatible(desired: str, existing: str) -> bool:
    """Return whether two author spellings are safely canonicalized together."""
    if normalize_author(desired) == normalize_author(existing):
        return True
    desired_parts, existing_parts = _author_parts(desired), _author_parts(existing)
    if (
        not desired_parts
        or not existing_parts
        or desired_parts[-1] != existing_parts[-1]
    ):
        return False
    desired_given, existing_given = desired_parts[:-1], existing_parts[:-1]
    if not desired_given or not existing_given:
        return False
    shorter, longer = sorted((desired_given, existing_given), key=len)
    return all(
        short[0] == long[0] and (len(short) == 1 or len(long) == 1 or short == long)
        for short, long in zip(shorter, longer, strict=False)
    )


def _author_parts(value: str) -> list[str]:
    """Return a normalized primary-author token sequence."""
    primary = re.split(r",\s*|\s+and\s+", _clean_author(value), maxsplit=1)[0]
    return re.sub(r"[^A-Za-z ]", " ", primary).lower().split()
