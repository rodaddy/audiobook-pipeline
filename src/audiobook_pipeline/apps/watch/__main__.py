"""Run the durable inbox watcher with one fresh pipeline context per claim."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

import click
from loguru import logger

from audiobook_pipeline.config import Settings, load_settings
from audiobook_pipeline.models.book import BookDirectory
from audiobook_pipeline.models.scheduling import BatchScheduleResult, BookOutcome
from audiobook_pipeline.models.watch import (
    ClaimedCandidate,
    ProcessFactory,
    WatchOptions,
)
from audiobook_pipeline.services.admission import BatchAdmission
from audiobook_pipeline.services.batch import run_batch
from audiobook_pipeline.services.discovery import discover_books
from audiobook_pipeline.services.watch import SqliteClaimStore, WatchRunner

log = logger.bind(stage="watch-cli")


def watch_options(settings: Settings) -> WatchOptions:
    """Map root settings into the isolated durable-watch contract."""
    return WatchOptions(
        inbox_dir=settings.paths.incoming_dir,
        state_db=settings.paths.work_dir / "watch.db",
        quarantine_dir=settings.paths.failed_dir,
        stability_seconds=settings.automation.stability_threshold,
        max_retries=settings.automation.max_retries,
        poll_interval_seconds=settings.automation.poll_interval_seconds,
        failure_webhook_url=settings.automation.failure_webhook_url,
    )


def build_runner(settings: Settings) -> WatchRunner:
    """Create the one watcher and durable claim store owned by this process."""
    options = watch_options(settings)
    return WatchRunner(
        options, process_factory(settings), store=SqliteClaimStore(options.state_db)
    )


def process_factory(settings: Settings) -> ProcessFactory:
    """Build a processor factory that creates no resources until a claim runs."""

    def factory(claim: ClaimedCandidate) -> Callable[[], bool]:
        return partial(process_candidate, settings, claim.source)

    return factory


def process_candidate(settings: Settings, source: Path) -> bool:
    """Run one claimed source through discovery, admission, and batch scheduling."""
    try:
        books = _discover_claim(source)
        if not books:
            log.warning("Watch pipeline processing failed: no_discovered_books")
            return False
        with BatchAdmission(settings.paths).admit(source):
            result = run_batch(books, settings, source, settings.automation.watch_mode)
    except Exception:  # noqa: BLE001 -- WatchRunner needs a bool for durable retry.
        log.warning("Watch pipeline processing failed: candidate_unavailable")
        return False
    return _batch_succeeded(result)


def _discover_claim(source: Path) -> tuple[BookDirectory, ...]:
    """Discover a directory claim or isolate one file claim from its parent."""
    if source.is_dir():
        return tuple(discover_books(source))
    return tuple(
        book
        for book in discover_books(source.parent)
        if book.identity_path.resolve() == source.resolve()
    )


def _batch_succeeded(result: BatchScheduleResult) -> bool:
    """Require every discovered book to reach one successful terminal result."""
    return bool(result.results) and all(
        item.outcome is BookOutcome.SUCCESS for item in result.results
    )


def run(settings: Settings) -> None:
    """Run until Ctrl-C without letting an interrupt look like a failed claim."""
    try:
        build_runner(settings).run(lambda: False)
    except KeyboardInterrupt:
        log.warning("Watch stopped by operator interrupt")


@click.command()
@click.option(
    "--profile",
    default="default",
    help="Named config layer to load from config/config.{profile}.json.",
)
def main(profile: str) -> None:
    """Watch the configured inbox and process stable candidates."""
    run(load_settings(profile=profile))


if __name__ == "__main__":
    main()
