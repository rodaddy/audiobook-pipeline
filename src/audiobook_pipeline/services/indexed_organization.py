"""Claim, place, verify, and commit library outputs through a durable index."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from audiobook_pipeline.models.index import (
    ClaimState,
    DestinationIdentity,
    IndexedFile,
    Reservation,
    SourceIdentity,
)
from audiobook_pipeline.services.index import LibraryIndex
from audiobook_pipeline.services.organize import place_claimed_book


class IndexedPlacementError(OSError):
    """A destination could not be atomically claimed and committed."""


def place_indexed_book(
    index: LibraryIndex, source: Path, desired: Path, source_identity: SourceIdentity
) -> Path:
    """Place one file at a claimed destination and register it only after verification."""
    reservation = claim_destination(index, desired, source_identity, datetime.now(UTC))
    committed = False
    final: Path | None = None
    try:
        final = place_claimed_book(source, reservation.destination.path, move=False)
        _verify_placement(final, reservation)
        committed = index.commit(
            reservation, IndexedFile(destination=reservation.destination)
        )
        if not committed:
            raise IndexedPlacementError
        return _complete_committed_output(index, source, final, reservation)
    finally:
        if not committed:
            _remove_uncommitted_output(final, reservation)
            index.release(reservation)


def claim_destination(
    index: LibraryIndex,
    desired: Path,
    source_identity: SourceIdentity,
    now: datetime,
) -> Reservation:
    """Claim the desired path or one exact numeric-suffix alternative."""
    for suffix in range(1, 100):
        destination = DestinationIdentity(
            directory=desired.parent, filename=_candidate_filename(desired, suffix)
        )
        result = index.claim(destination, source_identity, now)
        if result.state is ClaimState.CLAIMED:
            return _claimed_reservation(result.reservation)
    raise IndexedPlacementError


def _candidate_filename(desired: Path, suffix: int) -> str:
    """Return the filename associated with one bounded collision attempt."""
    if suffix == 1:
        return desired.name
    return f"{desired.stem} ({suffix}){desired.suffix}"


def _claimed_reservation(reservation: Reservation | None) -> Reservation:
    """Turn the model invariant into a static type guarantee for callers."""
    if reservation is None:
        raise IndexedPlacementError
    return reservation


def _verify_placement(final: Path, reservation: Reservation) -> None:
    """Require the exact claimed path to exist as a regular output file."""
    if final != reservation.destination.path or not final.is_file():
        raise IndexedPlacementError


def _complete_committed_output(
    index: LibraryIndex, source: Path, final: Path, reservation: Reservation
) -> Path:
    """Refresh cache and consume source without converting a committed result to retry."""
    _register_committed_destination(index, reservation)
    try:
        _consume_source(source)
    except OSError:
        logger.warning("library_index.source_cleanup_degraded")
    return final


def _register_committed_destination(
    index: LibraryIndex, reservation: Reservation
) -> None:
    """Attempt cache registration after commit without invalidating the output."""
    try:
        index.register_destination(IndexedFile(destination=reservation.destination))
    except (OSError, sqlite3.Error, ValueError):
        logger.warning("library_index.cache_registration_degraded")


def _consume_source(source: Path) -> None:
    """Remove a converted work artifact only after durable index commit."""
    source.unlink()


def _remove_uncommitted_output(final: Path | None, reservation: Reservation) -> None:
    """Remove only the exact controller-created output after a failed commit."""
    if final != reservation.destination.path:
        return
    try:
        final.unlink(missing_ok=True)
    except OSError:
        logger.warning("library_index.uncommitted_cleanup_degraded")
