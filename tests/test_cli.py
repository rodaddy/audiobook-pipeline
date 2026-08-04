"""Regression coverage for the current ``audiobook-convert`` CLI."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from click.testing import CliRunner

from audiobook_pipeline.apps.convert import __main__ as convert_app
from audiobook_pipeline.config import PathSettings, Settings
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.scheduling import (
    BatchScheduleResult,
    BookOutcome,
    BookScheduleResult,
)
from audiobook_pipeline.models.stage import PipelineMode


def configured_settings(root: Path) -> Settings:
    """Build isolated typed settings without reading project configuration."""
    return Settings(
        paths=PathSettings(
            work_dir=root / "work",
            library_dir=root / "library",
            lock_dir=root / "locks",
        )
    )


def discovered_book(root: Path, name: str) -> BookDirectory:
    """Create a current discovery result without media decoding."""
    source = root / name
    return BookDirectory(
        path=root,
        files=(AudioFile(path=source, duration_ms=60_000),),
    )


def completed_batch(books: list[BookDirectory]) -> BatchScheduleResult:
    """Build a typed successful scheduler receipt for CLI tests."""
    return BatchScheduleResult(
        total=len(books),
        succeeded=len(books),
        failed=0,
        cancelled=0,
        results=tuple(
            BookScheduleResult(
                index=index,
                source=book.identity_path,
                outcome=BookOutcome.SUCCESS,
            )
            for index, book in enumerate(books)
        ),
    )


class Lease:
    """Minimal held lease for the application boundary."""

    def __enter__(self) -> Lease:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class Admission:
    """Return a lease without touching the filesystem lock service."""

    def __init__(self, _: PathSettings) -> None:
        pass

    def admit(self, _: Path) -> Lease:
        return Lease()


@contextmanager
def connection(_: Path) -> Iterator[sqlite3.Connection]:
    """Yield a disposable database for the real-run CLI boundary."""
    conn = sqlite3.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()


class RunProbe:
    """Capture submitted books and parsed mode without scheduling work."""

    def __init__(self) -> None:
        self.submitted: list[BookDirectory] = []
        self.mode: PipelineMode | None = None

    def run(
        self, books: list[BookDirectory], _: Settings, __: Path, mode: PipelineMode
    ) -> BatchScheduleResult:
        self.submitted.extend(books)
        self.mode = mode
        return completed_batch(books)


def test_help_exposes_current_convert_options() -> None:
    result = CliRunner().invoke(convert_app.main, ["--help"])

    assert result.exit_code == 0
    assert "--dry-run" in result.output
    assert "--mode" in result.output
    assert "--limit" in result.output


def test_source_must_be_an_existing_directory() -> None:
    result = CliRunner().invoke(convert_app.main, ["/missing/source"])

    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_dry_run_reports_limited_books_without_batch_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    books = [
        discovered_book(source, "first.m4b"),
        discovered_book(source, "second.m4b"),
    ]
    called = 0
    monkeypatch.setattr(
        convert_app, "load_settings", lambda **_: configured_settings(tmp_path)
    )
    monkeypatch.setattr(convert_app, "_dry_run_books", lambda *_: books)

    def run_batch(*_: object) -> BatchScheduleResult:
        nonlocal called
        called += 1
        return completed_batch([])

    monkeypatch.setattr(convert_app, "run_batch", run_batch)
    monkeypatch.setattr(
        convert_app,
        "BatchAdmission",
        lambda *_: (_ for _ in ()).throw(AssertionError("dry run took admission")),
    )

    result = CliRunner().invoke(
        convert_app.main, [str(source), "--dry-run", "--limit", "1"]
    )

    assert result.exit_code == 0
    assert "Limiting to 1 of 2 discovered book(s)." in result.output
    assert "1 book(s) under" in result.output
    assert called == 0


def test_real_run_bounds_merged_work_and_parses_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    visible = [
        discovered_book(source, "visible.m4b"),
        discovered_book(source, "second.m4b"),
    ]
    expected_visible = tuple(visible)
    recovered = [discovered_book(source, "recovered.m4b")]
    probe = RunProbe()

    monkeypatch.setattr(
        convert_app, "load_settings", lambda **_: configured_settings(tmp_path)
    )
    monkeypatch.setattr(convert_app, "BatchAdmission", Admission)
    monkeypatch.setattr(convert_app, "connect", connection)
    monkeypatch.setattr(
        convert_app, "discover_books", lambda *_args, **_kwargs: visible
    )
    monkeypatch.setattr(convert_app, "_simple_outputs", lambda *_: frozenset())
    monkeypatch.setattr(convert_app, "_recovery_books", lambda *_: recovered)
    monkeypatch.setattr(convert_app, "run_batch", probe.run)

    result = CliRunner().invoke(
        convert_app.main, [str(source), "--mode", "metadata", "--limit", "2"]
    )

    assert result.exit_code == 0
    assert probe.submitted == list(expected_visible)
    assert probe.mode is PipelineMode.METADATA
    assert "Limiting to 2 of 3 book(s)." in result.output


def test_a_first_run_creates_its_own_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing exists yet on a first run, and that is not an error.

    Admission checks free space against work_dir. When work_dir did not exist
    the check raised an unhandled OSError that reached the user as a Python
    traceback -- the state of every first run against a fresh configuration.
    """
    source = tmp_path / "source"
    source.mkdir()
    settings = configured_settings(tmp_path / "fresh")
    monkeypatch.setattr(convert_app, "load_settings", lambda **_: settings)
    monkeypatch.setattr(convert_app, "discover_books", lambda *_a, **_k: [])
    monkeypatch.setattr(convert_app, "run_batch", lambda *_a, **_k: None)
    monkeypatch.setattr(convert_app, "statuses", lambda _: ())

    result = CliRunner().invoke(convert_app.main, [str(source)])

    assert "Traceback" not in result.output
    assert settings.paths.work_dir.is_dir()


def test_an_unwritable_directory_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path the user can fix must not arrive as a traceback.

    The message names the directory that actually failed rather than
    data_dir, because each path is configured separately and naming the
    wrong one sends the reader to the wrong setting.
    """
    source = tmp_path / "source"
    source.mkdir()
    blocked = tmp_path / "blocked"
    blocked.mkdir(mode=0o500)
    settings = configured_settings(blocked / "denied")
    monkeypatch.setattr(convert_app, "load_settings", lambda **_: settings)

    result = CliRunner().invoke(convert_app.main, [str(source)])

    assert result.exit_code == 1
    assert "Traceback" not in result.output
    assert "Could not create the pipeline directory" in result.output
    assert str(blocked / "denied") in result.output
