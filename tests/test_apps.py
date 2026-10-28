"""Tests for the two command-line entry points.

Separate from ``test_cli.py``, which covers the pre-rewrite ``cli.py`` and
stays untouched until that module is retired.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self

import pytest
from click.testing import CliRunner

from audiobook_pipeline.apps.audit.__main__ import main as audit_main
from audiobook_pipeline.apps.convert.__main__ import (
    _recovery_books,
    _simple_outputs,
)
from audiobook_pipeline.apps.convert.__main__ import (
    main as convert_main,
)
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.db.rows import BookRow, StageRow
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.scheduling import (
    BatchScheduleResult,
    BookOutcome,
    BookScheduleResult,
)
from audiobook_pipeline.models.stage import PipelineMode, Stage, StageStatus
from audiobook_pipeline.services import discovery
from audiobook_pipeline.services.pipeline import book_hash

MINUTE_MS = 60 * 1000


def _scheduled(outcome: BookOutcome) -> BatchScheduleResult:
    """Build a deterministic one-book batch receipt for CLI boundary tests."""
    return BatchScheduleResult(
        total=1,
        succeeded=outcome is BookOutcome.SUCCESS,
        failed=outcome is BookOutcome.FAILURE,
        cancelled=outcome is BookOutcome.CANCELLED,
        results=(BookScheduleResult(index=0, source=Path("source"), outcome=outcome),),
    )


class _Lease:
    """Record the CLI's admission lifetime without using a real file lock."""

    def __init__(self, events: list[str]) -> None:
        """Store the observable close event list."""
        self._events = events

    def __enter__(self) -> _Lease:
        """Return the held lease."""
        return self

    def __exit__(self, *_: object) -> None:
        """Record that the batch completed before the lease released."""
        self._events.append("released")


class _OrderingProbe:
    """Record real-run orchestration boundaries without running external work."""

    def __init__(
        self, books: list[BookDirectory], recovery: list[BookDirectory] | None = None
    ) -> None:
        """Store deterministic discovered and recovery books."""
        self.books = books
        self.recovery = recovery or []
        self.events: list[str] = []
        self.submitted: list[BookDirectory] = []

    def __call__(self, _: object) -> Self:
        """Act as the patched BatchAdmission constructor."""
        return self

    def admit(self, _: Path) -> _Lease:
        """Record lease acquisition and return an observable lease."""
        self.events.append("acquired")
        return _Lease(self.events)

    @contextmanager
    def connect(self, _: Path) -> Iterator[sqlite3.Connection]:
        """Record database entry while yielding a disposable connection."""
        self.events.append("database")
        conn = sqlite3.connect(":memory:")
        try:
            yield conn
        finally:
            conn.close()

    def discover(self, _: Path, **__: object) -> list[BookDirectory]:
        """Record discovery and return configured visible books."""
        self.events.append("discovery")
        return list(self.books)

    def recover(self, *_: object) -> list[BookDirectory]:
        """Return deterministic database-only recovery books."""
        return self.recovery

    def run(self, books: list[BookDirectory], *_: object) -> BatchScheduleResult:
        """Record scheduler work and return one completed result."""
        self.events.append("batch")
        self.submitted = books
        return _scheduled(BookOutcome.SUCCESS)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch all real-run dependencies with ordered, deterministic boundaries."""
        monkeypatch.setattr(
            "audiobook_pipeline.apps.convert.__main__.connect", self.connect
        )
        monkeypatch.setattr(
            "audiobook_pipeline.apps.convert.__main__.BatchAdmission", self
        )
        monkeypatch.setattr(
            "audiobook_pipeline.apps.convert.__main__.discover_books", self.discover
        )
        monkeypatch.setattr(
            "audiobook_pipeline.apps.convert.__main__._simple_outputs",
            lambda *_: frozenset(),
        )
        monkeypatch.setattr(
            "audiobook_pipeline.apps.convert.__main__._recovery_books", self.recover
        )
        monkeypatch.setattr(
            "audiobook_pipeline.apps.convert.__main__.run_batch", self.run
        )


@pytest.fixture
def runner() -> CliRunner:
    """A Click test runner."""
    return CliRunner()


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every configured path at the test's own directory.

    Set through the ENVIRONMENT rather than by mutating a Settings object,
    because that is how a real user configures the tool -- so this exercises
    the precedence chain instead of bypassing it.
    """
    for key in list(os.environ):
        if key.startswith("AUDIOBOOK_"):
            monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("AUDIOBOOK_PATHS__WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setenv("AUDIOBOOK_PATHS__LIBRARY_DIR", str(tmp_path / "library"))
    monkeypatch.setenv("AUDIOBOOK_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AUDIOBOOK_PATHS__LOG_DIR", str(tmp_path / "logs"))
    return tmp_path


@pytest.fixture
def durations(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace ffprobe with a filename lookup."""
    table: dict[str, int] = {}

    def fake_probe(path: Path, **_: object) -> object:
        return type("Probed", (), {"duration_ms": table[path.name]})()

    monkeypatch.setattr(discovery, "probe", fake_probe)
    return table


def make_source(root: Path, durations: dict[str, int], *names: str) -> Path:
    """Create a source tree with declared durations."""
    folder = root / "source" / "A Book"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"audio")
        durations[name] = 45 * MINUTE_MS
    return root / "source"


def _single_book(source: Path) -> BookDirectory:
    """Build one discovered book for CLI orchestration tests."""
    return BookDirectory(
        path=source,
        files=(AudioFile(path=source / "book.m4b", duration_ms=60_000),),
    )


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------


def test_convert_help_lists_the_flags(runner: CliRunner) -> None:
    result = runner.invoke(convert_main, ["--help"])

    assert result.exit_code == 0
    assert "--dry-run" in result.output
    assert "--limit" in result.output


def test_convert_rejects_a_missing_source(runner: CliRunner) -> None:
    result = runner.invoke(convert_main, ["/nope/does/not/exist"])

    assert result.exit_code != 0


def test_dry_run_reports_the_classification_and_converts_nothing(
    runner: CliRunner,
    isolated: Path,
    durations: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The classification is the one thing worth checking before hours of CPU."""
    source = make_source(isolated, durations, "01.mp3", "02.mp3")
    called = {"n": 0}

    def record(*_: object, **__: object) -> BatchScheduleResult:
        called["n"] += 1
        return _scheduled(BookOutcome.SUCCESS)

    monkeypatch.setattr("audiobook_pipeline.apps.convert.__main__.run_batch", record)
    monkeypatch.setattr(
        "audiobook_pipeline.apps.convert.__main__.BatchAdmission",
        lambda *_: (_ for _ in ()).throw(AssertionError("admission on dry run")),
    )

    result = runner.invoke(convert_main, [str(source), "--dry-run"])

    assert result.exit_code == 0
    assert "concat" in result.output
    assert called["n"] == 0


def test_dry_run_names_a_single_file_book_as_single(
    runner: CliRunner, isolated: Path, durations: dict[str, int]
) -> None:
    source = make_source(isolated, durations, "solo.mp3")

    result = runner.invoke(convert_main, [str(source), "--dry-run"])

    assert "single" in result.output


def test_dry_run_with_no_database_creates_no_pipeline_directories(
    runner: CliRunner,
    isolated: Path,
    durations: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run discovery must not initialize the pipeline's mutable boundaries."""
    source = make_source(isolated, durations, "solo.mp3")
    work_dir = isolated / "new-work"
    lock_dir = isolated / "new-locks"
    monkeypatch.setenv("AUDIOBOOK_PATHS__WORK_DIR", str(work_dir))
    monkeypatch.setenv("AUDIOBOOK_PATHS__LOCK_DIR", str(lock_dir))

    result = runner.invoke(convert_main, [str(source), "--dry-run"])

    assert result.exit_code == 0
    assert not work_dir.exists()
    assert not lock_dir.exists()


def test_real_run_acquires_admission_before_database_and_discovery(
    runner: CliRunner,
    isolated: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lease spans every mutable batch boundary through terminal results."""
    source = isolated / "source"
    source.mkdir()
    probe = _OrderingProbe([_single_book(source)])
    probe.install(monkeypatch)

    result = runner.invoke(convert_main, [str(source)])

    assert result.exit_code == 0
    assert probe.events == ["acquired", "database", "discovery", "batch", "released"]


def test_real_limit_bounds_the_merged_discovery_and_recovery_batch(
    runner: CliRunner, isolated: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery rows cannot bypass the CLI's stated total batch limit."""
    source = isolated / "source"
    source.mkdir()
    discovered = [_single_book(source), _single_book(source / "other")]
    recovery = _single_book(source / "recovery")
    probe = _OrderingProbe(discovered, [recovery])
    probe.install(monkeypatch)

    result = runner.invoke(convert_main, [str(source), "--limit", "2"])

    assert result.exit_code == 0
    assert probe.submitted == discovered
    assert "Limiting to 2 of 3 book(s)." in result.output


def test_limit_is_reported_not_silent(
    runner: CliRunner, isolated: Path, durations: dict[str, int]
) -> None:
    """A truncated run that reads as a complete one is the failure here."""
    root = isolated / "source"
    for index in range(4):
        folder = root / f"Book {index}"
        folder.mkdir(parents=True)
        (folder / f"a{index}.mp3").write_bytes(b"audio")
        durations[f"a{index}.mp3"] = 45 * MINUTE_MS

    result = runner.invoke(convert_main, [str(root), "--dry-run", "--limit", "2"])

    assert "Limiting to 2 of 4" in result.output


def test_a_failed_book_makes_the_command_exit_non_zero(
    runner: CliRunner,
    isolated: Path,
    durations: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A half-working batch must not exit 0."""
    source = make_source(isolated, durations, "01.mp3")

    def fail(*_: object) -> BatchScheduleResult:
        return _scheduled(BookOutcome.FAILURE)

    monkeypatch.setattr("audiobook_pipeline.apps.convert.__main__.run_batch", fail)
    monkeypatch.setattr(
        "audiobook_pipeline.apps.convert.__main__.BatchAdmission",
        lambda *_: type("Admission", (), {"admit": lambda *_: _Lease([])})(),
    )

    result = runner.invoke(convert_main, [str(source)])

    assert result.exit_code == 1
    assert "1 failed" in result.output


def test_a_cancelled_book_makes_the_command_exit_non_zero(
    runner: CliRunner,
    isolated: Path,
    durations: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation is terminal accounting, not a successful partial batch."""
    source = make_source(isolated, durations, "01.mp3")
    monkeypatch.setattr(
        "audiobook_pipeline.apps.convert.__main__.run_batch",
        lambda *_: _scheduled(BookOutcome.CANCELLED),
    )
    monkeypatch.setattr(
        "audiobook_pipeline.apps.convert.__main__.BatchAdmission",
        lambda *_: type("Admission", (), {"admit": lambda *_: _Lease([])})(),
    )

    result = runner.invoke(convert_main, [str(source)])

    assert result.exit_code == 1
    assert "1 failed" in result.output


def test_a_successful_run_exits_zero(
    runner: CliRunner,
    isolated: Path,
    durations: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_source(isolated, durations, "01.mp3")

    events: list[str] = []

    class Admission:
        """Expose a lease whose release can be ordered against batch execution."""

        def __init__(self, _: object) -> None:
            """Accept configured path settings."""

        def admit(self, _: Path) -> _Lease:
            """Record acquire and return the active lease."""
            events.append("acquired")
            return _Lease(events)

    def succeed(*_: object) -> BatchScheduleResult:
        events.append("batch")
        return _scheduled(BookOutcome.SUCCESS)

    monkeypatch.setattr("audiobook_pipeline.apps.convert.__main__.run_batch", succeed)
    monkeypatch.setattr(
        "audiobook_pipeline.apps.convert.__main__.BatchAdmission", Admission
    )

    result = runner.invoke(convert_main, [str(source)])

    assert result.exit_code == 0
    assert "1 completed, 0 failed" in result.output
    assert events == ["acquired", "batch", "released"]


def test_recovery_includes_a_cleanup_retry_after_its_source_moved(
    tmp_path: Path,
) -> None:
    """CLI recovery comes from lifecycle rows, not filesystem rediscovery."""
    organized = tmp_path / "library" / "Known Book.m4b"
    organized.parent.mkdir()
    organized.write_bytes(b"finished")
    source = tmp_path / "source" / "Known Book"
    original = BookDirectory(
        path=source,
        files=(
            AudioFile(path=source / "01.mp3", duration_ms=30_000),
            AudioFile(path=source / "02.mp3", duration_ms=30_000),
        ),
    )
    source_hash = book_hash(original)
    with connect(tmp_path / "pipeline.db") as conn:
        queries.upsert_book(
            conn,
            BookRow(
                book_hash=source_hash,
                source_path=str(source),
                mode=PipelineMode.CONVERT.value,
                status="failed",
                total_duration=60,
            ),
        )
        queries.set_stage(
            conn,
            StageRow(
                book_hash=source_hash,
                stage=Stage.ORGANIZE.value,
                status=StageStatus.COMPLETED.value,
                output_file=str(organized),
            ),
        )
        queries.set_stage(
            conn,
            StageRow(
                book_hash=source_hash,
                stage=Stage.ARCHIVE.value,
                status=StageStatus.COMPLETED.value,
            ),
        )

        recovered = _recovery_books(conn, [], PipelineMode.CONVERT)

    assert [book.files[0].path for book in recovered] == [source]
    assert [book_hash(book) for book in recovered] == [source_hash]


def test_recovery_omits_an_old_hash_when_the_source_identity_is_visible(
    tmp_path: Path,
) -> None:
    """A changed duration cannot cause old and visible rows to submit together."""
    source = tmp_path / "source" / "book.m4b"
    source.parent.mkdir()
    source.write_bytes(b"audio")
    visible = BookDirectory(
        path=source.parent,
        files=(AudioFile(path=source, duration_ms=60_001),),
    )
    old = BookDirectory(
        path=source.parent,
        files=(AudioFile(path=source, duration_ms=60_000),),
    )
    with connect(tmp_path / "pipeline.db") as conn:
        queries.upsert_book(
            conn,
            BookRow(
                book_hash=book_hash(old),
                source_path=str(source),
                mode=PipelineMode.CONVERT.value,
                status="failed",
                total_duration=60,
            ),
        )

        recovered = _recovery_books(conn, [visible], PipelineMode.CONVERT)

    submitted = [visible, *recovered]
    assert len(submitted) == 1
    assert [book.identity_path for book in submitted] == [source]


def test_recovery_deduplicates_old_rows_by_source_identity_in_row_order(
    tmp_path: Path,
) -> None:
    """Multiple failed rows for a moved source yield the deterministic oldest row."""
    source = tmp_path / "source" / "moved-book"
    with connect(tmp_path / "pipeline.db") as conn:
        for hash_, duration, created_at in (
            ("old-row", 60, "2026-01-01T00:00:00+00:00"),
            ("new-row", 90, "2026-02-01T00:00:00+00:00"),
        ):
            queries.upsert_book(
                conn,
                BookRow(
                    book_hash=hash_,
                    source_path=str(source),
                    mode=PipelineMode.CONVERT.value,
                    status="failed",
                    total_duration=duration,
                    created_at=created_at,
                ),
            )

        recovered = _recovery_books(conn, [], PipelineMode.CONVERT)

    assert len(recovered) == 1
    assert recovered[0].total_duration_ms == 60_000


def test_simple_output_exclusion_is_limited_to_recorded_completed_outputs(
    tmp_path: Path,
) -> None:
    """An ordinary source M4B is not hidden just because it has that suffix."""
    generated = tmp_path / "source" / "generated.m4b"
    original = tmp_path / "source" / "original.m4b"
    generated.parent.mkdir()
    generated.write_bytes(b"generated")
    original.write_bytes(b"original")
    with connect(tmp_path / "pipeline.db") as conn:
        queries.upsert_book(
            conn,
            BookRow(
                book_hash="simple-hash",
                source_path=str(original),
                mode=PipelineMode.CONVERT.value,
            ),
        )
        queries.set_stage(
            conn,
            StageRow(
                book_hash="simple-hash",
                stage=Stage.CONVERT.value,
                status=StageStatus.COMPLETED.value,
                output_file=str(generated),
            ),
        )

        excluded = _simple_outputs(conn, tmp_path / "source")

    assert excluded == frozenset({generated.resolve()})
    assert original.resolve() not in excluded


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def test_audit_help_lists_the_flags(runner: CliRunner) -> None:
    result = runner.invoke(audit_main, ["--help"])

    assert result.exit_code == 0
    assert "--failures" in result.output


def test_audit_reports_an_empty_library_without_failing(
    runner: CliRunner, isolated: Path
) -> None:
    result = runner.invoke(audit_main, [])

    assert result.exit_code == 0
    assert "0 m4b file(s)" in result.output
    assert "0 book(s) recorded" in result.output


def test_audit_counts_what_is_actually_on_disk(
    runner: CliRunner, isolated: Path
) -> None:
    """Counted from the filesystem: the two disagreeing is the useful signal."""
    library = isolated / "library" / "An Author"
    library.mkdir(parents=True)
    (library / "A Book.m4b").write_bytes(b"x")
    (library / "Another.m4b").write_bytes(b"x")

    result = runner.invoke(audit_main, [])

    assert "2 m4b file(s) across 1 author folder(s)" in result.output
