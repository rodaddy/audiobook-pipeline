"""Focused application-boundary tests for ``audiobook-watch``."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import audiobook_pipeline.apps.watch.__main__ as watch_app
from audiobook_pipeline.config import AutomationSettings, PathSettings, Settings
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.scheduling import (
    BatchScheduleResult,
    BookOutcome,
    BookScheduleResult,
)
from audiobook_pipeline.models.stage import PipelineLevel, PipelineMode


def _settings(tmp_path: Path, mode: PipelineMode = PipelineMode.CONVERT) -> Settings:
    paths = PathSettings(
        work_dir=tmp_path / "work",
        library_dir=tmp_path / "library",
        incoming_dir=tmp_path / "incoming",
        failed_dir=tmp_path / "failed",
    )
    return Settings(
        level=PipelineLevel.SIMPLE,
        paths=paths,
        automation=AutomationSettings(
            stability_threshold=7,
            poll_interval_seconds=2.5,
            max_retries=4,
            failure_webhook_url="https://watch.example.invalid/hook",
            watch_mode=mode,
        ),
    )


def _book(source: Path) -> BookDirectory:
    return BookDirectory(
        path=source.parent,
        files=(AudioFile(path=source, duration_ms=1),),
    )


def _result(*outcomes: BookOutcome) -> BatchScheduleResult:
    results = tuple(
        BookScheduleResult(index=index, source=Path(f"book-{index}"), outcome=outcome)
        for index, outcome in enumerate(outcomes)
    )
    return BatchScheduleResult(
        total=len(results),
        succeeded=outcomes.count(BookOutcome.SUCCESS),
        failed=outcomes.count(BookOutcome.FAILURE),
        cancelled=outcomes.count(BookOutcome.CANCELLED),
        results=results,
    )


class _AdmissionProbe:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def admit(self, source: Path) -> _AdmissionProbe:
        assert source.name == "claim"
        return self

    def __enter__(self) -> None:
        self._events.append("admission-enter")

    def __exit__(self, *_args: object) -> None:
        self._events.append("admission-exit")


def test_watch_options_maps_typed_settings(tmp_path: Path) -> None:
    settings = _settings(tmp_path, PipelineMode.METADATA)

    options = watch_app.watch_options(settings)

    assert options.inbox_dir == settings.paths.incoming_dir
    assert options.state_db == settings.paths.work_dir / "watch.db"
    assert options.quarantine_dir == settings.paths.failed_dir
    assert options.stability_seconds == 7
    assert options.poll_interval_seconds == pytest.approx(2.5)
    assert options.max_retries == 4
    assert options.failure_webhook_url == "https://watch.example.invalid/hook"
    assert settings.automation.watch_mode is PipelineMode.METADATA


def test_candidate_discovers_all_books_and_holds_admission_through_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, PipelineMode.ORGANIZE)
    source = tmp_path / "claim"
    source.mkdir()
    books = (_book(source / "one.mp3"), _book(source / "two.mp3"))
    events: list[str] = []
    admission = _AdmissionProbe(events)

    def scheduled(
        received: tuple[BookDirectory, ...],
        received_settings: Settings,
        received_source: Path,
        mode: PipelineMode,
    ) -> BatchScheduleResult:
        assert events == ["admission-enter"]
        assert received == books
        assert received_settings is settings
        assert received_source == source
        assert mode is PipelineMode.ORGANIZE
        events.append("batch")
        return _result(BookOutcome.SUCCESS, BookOutcome.SUCCESS)

    monkeypatch.setattr(watch_app, "discover_books", lambda _source: books)
    monkeypatch.setattr(watch_app, "BatchAdmission", lambda _paths: admission)
    monkeypatch.setattr(watch_app, "run_batch", scheduled)

    assert watch_app.process_candidate(settings, source)
    assert events == ["admission-enter", "batch", "admission-exit"]


def test_candidate_without_discovered_books_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "claim"
    source.mkdir()

    def unexpected(*_args: object, **_kwargs: object) -> object:
        msg = "zero books must not start admission or scheduling"
        raise AssertionError(msg)

    monkeypatch.setattr(watch_app, "discover_books", lambda _source: ())
    monkeypatch.setattr(watch_app, "BatchAdmission", unexpected)
    monkeypatch.setattr(watch_app, "run_batch", unexpected)

    assert not watch_app.process_candidate(settings, source)


@pytest.mark.parametrize("outcome", [BookOutcome.FAILURE, BookOutcome.CANCELLED])
def test_candidate_failure_or_cancellation_is_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: BookOutcome
) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "claim"
    source.mkdir()
    admission = _AdmissionProbe([])

    monkeypatch.setattr(watch_app, "discover_books", lambda _source: (_book(source),))
    monkeypatch.setattr(watch_app, "BatchAdmission", lambda _paths: admission)
    monkeypatch.setattr(watch_app, "run_batch", lambda *_args: _result(outcome))

    assert not watch_app.process_candidate(settings, source)


def test_run_absorbs_keyboard_interrupt_without_a_failed_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    called = False

    class InterruptingRunner:
        def run(self, stop_requested: object) -> None:
            nonlocal called
            called = True
            assert callable(stop_requested)
            raise KeyboardInterrupt

    monkeypatch.setattr(
        watch_app, "build_runner", lambda _settings: InterruptingRunner()
    )

    watch_app.run(settings)

    assert called


def test_build_runner_processes_one_stable_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    settings.automation.stability_threshold = 0
    settings.paths.incoming_dir.mkdir()
    source = settings.paths.incoming_dir / "book.mp3"
    source.write_bytes(b"audio")
    processed: list[Path] = []

    def completed(_settings: Settings, candidate: Path) -> bool:
        processed.append(candidate)
        return True

    monkeypatch.setattr(watch_app, "process_candidate", completed)
    runner = watch_app.build_runner(settings)
    runner.poll()
    result = runner.poll()

    assert result.completed == 1
    assert not result.stopped
    assert processed == [source]


def test_import_creates_no_watch_database_or_batch_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_args: object, **_kwargs: object) -> object:
        msg = "import must not create resources"
        raise AssertionError(msg)

    with monkeypatch.context() as scoped:
        scoped.setattr("audiobook_pipeline.services.watch.SqliteClaimStore", unexpected)
        scoped.setattr("audiobook_pipeline.services.batch.run_batch", unexpected)
        importlib.reload(watch_app)
    importlib.reload(watch_app)
