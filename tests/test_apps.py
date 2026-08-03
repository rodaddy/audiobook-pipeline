"""Tests for the two command-line entry points.

Separate from ``test_cli.py``, which covers the pre-rewrite ``cli.py`` and
stays untouched until that module is retired.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from click.testing import CliRunner

from audiobook_pipeline.apps.audit.__main__ import main as audit_main
from audiobook_pipeline.apps.convert.__main__ import main as convert_main
from audiobook_pipeline.services import discovery
from audiobook_pipeline.services.pipeline import RunContext

MINUTE_MS = 60 * 1000


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

    def record(*_: object, **__: object) -> object:
        called["n"] += 1
        return type("Row", (), {"status": "completed"})()

    monkeypatch.setattr("audiobook_pipeline.apps.convert.__main__.process_book", record)

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

    def fail(book: object, context: RunContext, **_: object) -> object:
        return type("Row", (), {"status": "failed"})()

    monkeypatch.setattr("audiobook_pipeline.apps.convert.__main__.process_book", fail)

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

    def succeed(book: object, context: RunContext, **_: object) -> object:
        return type("Row", (), {"status": "completed"})()

    monkeypatch.setattr(
        "audiobook_pipeline.apps.convert.__main__.process_book", succeed
    )

    result = runner.invoke(convert_main, [str(source)])

    assert result.exit_code == 0
    assert "1 completed, 0 failed" in result.output


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
