"""Poll a stable inbox using durable, exclusive claims before processing."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import httpx
from loguru import logger

from audiobook_pipeline.models.book import SOURCE_EXTENSIONS
from audiobook_pipeline.models.watch import (
    ClaimedCandidate,
    ClaimStatus,
    Clock,
    FileObservation,
    ObservationEntry,
    ProcessFactory,
    QuarantineMover,
    Sleeper,
    StopRequested,
    WatchCandidate,
    WatchClaim,
    WatchOptions,
    WatchPollCounters,
    WatchPollResult,
    WebhookNotifier,
    WebhookPayload,
)
from audiobook_pipeline.services.watch_recovery import (
    quarantine_recovery_state,
    safe_recovery_reservation,
)

log = logger.bind(component="watch")


class SqliteClaimStore:
    """Durable claim state with one SQLite connection per operation."""

    def __init__(self, state_db: Path) -> None:
        """Create schema storage without retaining a thread-shared connection."""
        self._state_db = state_db
        state_db.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(state_db)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_claims (
                    candidate_id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    quarantine_dir TEXT
                )
                """
            )
            connection.commit()

    def claim(self, candidate: WatchCandidate) -> ClaimedCandidate | None:
        """Atomically claim a new or retryable candidate, or return no claim."""
        with closing(sqlite3.connect(self._state_db)) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO watch_claims
                (candidate_id, source_path, attempt, status, quarantine_dir)
                VALUES (?, ?, 1, ?, NULL)
                """,
                (candidate.candidate_id, str(candidate.source), ClaimStatus.CLAIMED),
            )
            if cursor.rowcount == 1:
                connection.commit()
                return ClaimedCandidate(**candidate.model_dump(), attempt=1)
            cursor = connection.execute(
                """
                UPDATE watch_claims
                SET attempt = attempt + 1, status = ?, quarantine_dir = NULL
                WHERE candidate_id = ? AND status = ?
                """,
                (ClaimStatus.CLAIMED, candidate.candidate_id, ClaimStatus.RETRYABLE),
            )
            if cursor.rowcount != 1:
                connection.commit()
                return None
            row = connection.execute(
                "SELECT attempt FROM watch_claims WHERE candidate_id = ?",
                (candidate.candidate_id,),
            ).fetchone()
            connection.commit()
        if row is None:
            msg = "claimed candidate was missing from durable state"
            raise RuntimeError(msg)
        return ClaimedCandidate(**candidate.model_dump(), attempt=int(row[0]))

    def mark(self, claim: ClaimedCandidate, status: ClaimStatus) -> WatchClaim:
        """Persist a terminal or retryable state for the active claim attempt."""
        with closing(sqlite3.connect(self._state_db)) as connection:
            cursor = connection.execute(
                """
                UPDATE watch_claims
                SET status = ?, quarantine_dir = NULL
                WHERE candidate_id = ? AND attempt = ? AND status = ?
                """,
                (status, claim.candidate_id, claim.attempt, ClaimStatus.CLAIMED),
            )
            connection.commit()
        if cursor.rowcount != 1:
            msg = "active durable claim could not be updated"
            raise RuntimeError(msg)
        return WatchClaim(
            candidate_id=claim.candidate_id,
            source=claim.source,
            attempt=claim.attempt,
            status=status,
        )

    def begin_quarantine(
        self, claim: ClaimedCandidate, quarantine_dir: Path
    ) -> WatchClaim:
        """Persist the destination before moving a failed candidate into it."""
        with closing(sqlite3.connect(self._state_db)) as connection:
            cursor = connection.execute(
                """
                UPDATE watch_claims
                SET status = ?, quarantine_dir = ?
                WHERE candidate_id = ? AND attempt = ? AND status = ?
                """,
                (
                    ClaimStatus.QUARANTINING,
                    str(quarantine_dir),
                    claim.candidate_id,
                    claim.attempt,
                    ClaimStatus.CLAIMED,
                ),
            )
            connection.commit()
        if cursor.rowcount != 1:
            msg = "quarantine transition could not be persisted"
            raise RuntimeError(msg)
        return WatchClaim(
            candidate_id=claim.candidate_id,
            source=claim.source,
            attempt=claim.attempt,
            status=ClaimStatus.QUARANTINING,
            quarantine_dir=quarantine_dir,
        )

    def finish_quarantine(self, claim: WatchClaim) -> WatchClaim:
        """Finalize a durable transition after the source has moved safely."""
        with closing(sqlite3.connect(self._state_db)) as connection:
            cursor = connection.execute(
                """
                UPDATE watch_claims
                SET status = ?
                WHERE candidate_id = ? AND attempt = ? AND status = ?
                """,
                (
                    ClaimStatus.QUARANTINED,
                    claim.candidate_id,
                    claim.attempt,
                    ClaimStatus.QUARANTINING,
                ),
            )
            connection.commit()
        if cursor.rowcount != 1:
            msg = "quarantine finalization could not be persisted"
            raise RuntimeError(msg)
        return claim.model_copy(update={"status": ClaimStatus.QUARANTINED})

    def release_quarantine(self, claim: WatchClaim) -> WatchClaim:
        """Return an unmoved quarantining claim to the bounded retry queue."""
        with closing(sqlite3.connect(self._state_db)) as connection:
            cursor = connection.execute(
                """
                UPDATE watch_claims
                SET status = ?, quarantine_dir = NULL
                WHERE candidate_id = ? AND attempt = ? AND status = ?
                """,
                (
                    ClaimStatus.RETRYABLE,
                    claim.candidate_id,
                    claim.attempt,
                    ClaimStatus.QUARANTINING,
                ),
            )
            connection.commit()
        if cursor.rowcount != 1:
            msg = "quarantine rollback could not be persisted"
            raise RuntimeError(msg)
        return claim.model_copy(
            update={"status": ClaimStatus.RETRYABLE, "quarantine_dir": None}
        )

    def quarantining(self) -> tuple[WatchClaim, ...]:
        """Return persisted transitions requiring safe restart recovery."""
        with closing(sqlite3.connect(self._state_db)) as connection:
            rows = connection.execute(
                """
                SELECT candidate_id, source_path, attempt, quarantine_dir
                FROM watch_claims WHERE status = ?
                """,
                (ClaimStatus.QUARANTINING,),
            ).fetchall()
        return tuple(
            WatchClaim(
                candidate_id=str(row[0]),
                source=Path(str(row[1])),
                attempt=int(row[2]),
                status=ClaimStatus.QUARANTINING,
                quarantine_dir=Path(str(row[3])),
            )
            for row in rows
        )


class WatchRunner:
    """Run stable candidates only after a durable claim has been acquired."""

    def __init__(
        self,
        options: WatchOptions,
        process_factory: ProcessFactory,
        store: SqliteClaimStore | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        """Store dependencies; worker factories run only after exclusive claims."""
        self._options = options
        self._process_factory = process_factory
        self._store = store or SqliteClaimStore(options.state_db)
        self._clock = clock
        self._observations: dict[Path, FileObservation] = {}

    def poll(
        self,
        stop_requested: StopRequested | None = None,
        mover: QuarantineMover | None = None,
        notifier: WebhookNotifier | None = None,
    ) -> WatchPollResult:
        """Scan once without sleeping; callers control cadence through ``run``."""
        counters = WatchPollCounters()
        if stop_requested is not None and stop_requested():
            return counters.result(stopped=True)
        active_mover = mover or _move_to_quarantine
        active_notifier = notifier or _post_webhook
        self._recover_quarantines(counters, active_notifier)
        for source in self._inbox_sources():
            if stop_requested is not None and stop_requested():
                return counters.result(stopped=True)
            counters.observed += 1
            candidate = self._stable_candidate(source)
            if candidate is None:
                counters.unstable += 1
                continue
            claim = self._store.claim(candidate)
            if claim is None:
                continue
            counters.claimed += 1
            if stop_requested is not None and stop_requested():
                self._store.mark(claim, ClaimStatus.RETRYABLE)
                return counters.result(stopped=True)
            self._process_claim(claim, counters, active_mover, active_notifier)
        counters.unsupported = self._unsupported_count()
        return counters.result(stopped=False)

    def run(
        self,
        stop_requested: StopRequested,
        sleeper: Sleeper = time.sleep,
        mover: QuarantineMover | None = None,
        notifier: WebhookNotifier | None = None,
    ) -> WatchPollResult:
        """Poll until stopped, sleeping no longer than the configured interval."""
        result = WatchPollCounters().result(stopped=True)
        while not stop_requested():
            result = self.poll(stop_requested, mover, notifier)
            if result.stopped:
                return result
            sleeper(self._options.poll_interval_seconds)
        return result.model_copy(update={"stopped": True})

    def _inbox_sources(self) -> tuple[Path, ...]:
        try:
            entries = tuple(sorted(self._options.inbox_dir.iterdir()))
        except OSError:
            log.warning("Watch inbox scan failed: inbox_unavailable")
            return ()
        return tuple(path for path in entries if _candidate_entries(path) is not None)

    def _unsupported_count(self) -> int:
        try:
            entries = tuple(self._options.inbox_dir.iterdir())
        except OSError:
            log.warning("Watch inbox scan failed: inbox_unavailable")
            return 0
        return sum(
            not path.is_symlink()
            and path.is_file()
            and _candidate_entries(path) is None
            for path in entries
        )

    def _stable_candidate(self, source: Path) -> WatchCandidate | None:
        entries = _candidate_entries(source)
        if entries is None:
            return None
        current = FileObservation(
            source=source,
            entries=entries,
            first_seen_at=self._clock(),
        )
        previous = self._observations.get(source)
        if previous is None or previous.entries != current.entries:
            self._observations[source] = current
            return None
        if (
            current.first_seen_at - previous.first_seen_at
            < self._options.stability_seconds
        ):
            return None
        candidate_id = hashlib.sha256(str(source.resolve()).encode()).hexdigest()
        return WatchCandidate(candidate_id=candidate_id, source=source)

    def _recover_quarantines(
        self, counters: WatchPollCounters, notifier: WebhookNotifier
    ) -> None:
        """Finish or safely roll back durable transitions left by a prior run."""
        for claim in self._store.quarantining():
            if not safe_recovery_reservation(self._options.quarantine_dir, claim):
                log.warning("Watch quarantine recovery rejected: unsafe_reservation")
                continue
            state = quarantine_recovery_state(claim)
            if state == "moved":
                self._finish_recovery(claim, counters, notifier)
            elif state == "unmoved":
                self._release_quarantine(claim, counters)
            elif state == "partial":
                self._reconcile_partial_recovery(claim, counters, notifier)
            else:
                log.warning("Watch quarantine recovery deferred: partial_move")

    def _finish_recovery(
        self,
        claim: WatchClaim,
        counters: WatchPollCounters,
        notifier: WebhookNotifier,
    ) -> None:
        try:
            self._store.finish_quarantine(claim)
        except RuntimeError:
            log.warning("Watch quarantine recovery failed: finalize_unavailable")
            return
        counters.quarantined += 1
        self._notify(claim, notifier)

    def _reconcile_partial_recovery(
        self,
        claim: WatchClaim,
        counters: WatchPollCounters,
        notifier: WebhookNotifier,
    ) -> None:
        """Preserve a residual source under its reserved quarantine directory."""
        if not safe_recovery_reservation(self._options.quarantine_dir, claim):
            log.warning("Watch quarantine recovery rejected: unsafe_reservation")
            return
        try:
            assert claim.quarantine_dir is not None
            residual_dir = _reserve_quarantine_dir(claim.quarantine_dir, "residual")
            _move_to_quarantine(claim.source, residual_dir)
        except OSError:
            log.warning("Watch quarantine recovery failed: residual_unavailable")
            return
        self._finish_recovery(claim, counters, notifier)

    def _process_claim(
        self,
        claim: ClaimedCandidate,
        counters: WatchPollCounters,
        mover: QuarantineMover,
        notifier: WebhookNotifier,
    ) -> None:
        try:
            succeeded = self._process_factory(claim)()
        except Exception:  # noqa: BLE001 -- worker failures become durable state.
            log.warning("Watch processor failed: worker_exception")
            self._handle_failure(claim, counters, mover, notifier)
            return
        if succeeded:
            self._store.mark(claim, ClaimStatus.COMPLETED)
            counters.completed += 1
            return
        log.warning("Watch processor failed: worker_reported_failure")
        self._handle_failure(claim, counters, mover, notifier)

    def _handle_failure(
        self,
        claim: ClaimedCandidate,
        counters: WatchPollCounters,
        mover: QuarantineMover,
        notifier: WebhookNotifier,
    ) -> None:
        if claim.attempt <= self._options.max_retries:
            self._store.mark(claim, ClaimStatus.RETRYABLE)
            counters.retried += 1
            return
        transition = self._start_quarantine(claim, counters)
        if transition is None:
            return
        self._move_and_finalize(transition, counters, mover, notifier)

    def _start_quarantine(
        self, claim: ClaimedCandidate, counters: WatchPollCounters
    ) -> WatchClaim | None:
        try:
            quarantine_dir = _reserve_quarantine_dir(
                self._options.quarantine_dir, claim.candidate_id
            )
            return self._store.begin_quarantine(claim, quarantine_dir)
        except OSError:
            log.warning("Watch quarantine failed: destination_unavailable")
            self._store.mark(claim, ClaimStatus.RETRYABLE)
            counters.retried += 1
            return None
        except RuntimeError:
            log.warning("Watch quarantine failed: transition_unavailable")
            self._release_failed_transition(claim, quarantine_dir)
            return None

    def _move_and_finalize(
        self,
        transition: WatchClaim,
        counters: WatchPollCounters,
        mover: QuarantineMover,
        notifier: WebhookNotifier,
    ) -> None:
        try:
            assert transition.quarantine_dir is not None
            mover(transition.source, transition.quarantine_dir)
        except OSError:
            log.warning("Watch quarantine failed: move_unavailable")
            self._release_quarantine(transition, counters)
            return
        try:
            self._store.finish_quarantine(transition)
        except RuntimeError:
            log.warning("Watch quarantine deferred: finalize_unavailable")
            return
        counters.quarantined += 1
        self._notify(transition, notifier)

    def _release_quarantine(
        self, claim: WatchClaim, counters: WatchPollCounters
    ) -> None:
        try:
            assert claim.quarantine_dir is not None
            claim.quarantine_dir.rmdir()
            self._store.release_quarantine(claim)
        except (OSError, RuntimeError):
            log.warning("Watch quarantine rollback failed: retry_unavailable")
            return
        counters.retried += 1

    def _release_failed_transition(
        self, claim: ClaimedCandidate, quarantine_dir: Path | None
    ) -> None:
        """Release a claim when its pre-move durable transition could not start."""
        try:
            self._store.mark(claim, ClaimStatus.RETRYABLE)
        except RuntimeError:
            log.warning("Watch quarantine release failed: retry_unavailable")
            return
        if quarantine_dir is not None:
            try:
                quarantine_dir.rmdir()
            except OSError:
                log.warning("Watch quarantine cleanup failed: destination_unavailable")

    def _notify(
        self, claim: ClaimedCandidate | WatchClaim, notifier: WebhookNotifier
    ) -> None:
        if not self._options.failure_webhook_url:
            return
        payload = WebhookPayload(candidate_id=claim.candidate_id, attempt=claim.attempt)
        try:
            delivered = notifier(
                self._options.failure_webhook_url,
                payload,
                self._options.webhook_timeout_seconds,
            )
        except Exception:  # noqa: BLE001 -- notification must not undo quarantine.
            log.warning("Watch webhook failed: notifier_exception")
            return
        if not delivered:
            log.warning("Watch webhook failed: delivery_rejected")


def _candidate_entries(source: Path) -> tuple[ObservationEntry, ...] | None:
    """Return a non-recursive signature for one discovery-compatible candidate."""
    if source.name.startswith(".") or source.is_symlink():
        return None
    if source.is_file():
        return _file_entry(source)
    if not source.is_dir():
        return None
    try:
        children = tuple(sorted(source.iterdir(), key=lambda path: path.name.lower()))
    except OSError:
        log.warning("Watch candidate observation failed: directory_unavailable")
        return None
    if any(child.is_symlink() for child in children):
        return None
    entries = tuple(entry for child in children for entry in (_file_entry(child) or ()))
    return entries or None


def _file_entry(path: Path) -> tuple[ObservationEntry, ...] | None:
    """Build one direct source-audio signature entry without probing or recursion."""
    if path.name.startswith(".") or path.is_symlink() or not path.is_file():
        return None
    if path.suffix.lower() not in SOURCE_EXTENSIONS:
        return None
    try:
        stat = path.stat()
    except OSError:
        log.warning("Watch candidate observation failed: stat_unavailable")
        return None
    return (
        ObservationEntry(
            name=path.name, size_bytes=stat.st_size, modified_ns=stat.st_mtime_ns
        ),
    )


def _post_webhook(url: str, payload: WebhookPayload, timeout: float) -> bool:
    """Send the redacted payload with a caller-configured finite timeout."""
    try:
        response = httpx.post(
            url, json=payload.model_dump(mode="json"), timeout=timeout
        )
    except httpx.HTTPError:
        log.warning("Watch webhook failed: transport_error")
        return False
    return response.is_success


def _move_to_quarantine(source: Path, destination: Path) -> None:
    """Move a claimed source into its exclusively reserved directory."""
    shutil.move(source, destination)


def _reserve_quarantine_dir(quarantine_root: Path, candidate_id: str) -> Path:
    """Create an exclusive container without overwriting a user-owned path."""
    quarantine_root.mkdir(parents=True, exist_ok=True)
    suffix = 0
    while True:
        name = candidate_id if suffix == 0 else f"{candidate_id}-{suffix}"
        destination = quarantine_root / name
        try:
            destination.mkdir()
        except FileExistsError:
            log.warning("Watch quarantine destination collision")
            suffix += 1
            continue
        return destination
