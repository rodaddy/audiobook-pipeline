"""Behavioural tests for the read-only library audit surface."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from audiobook_pipeline.apps.audit.__main__ import main
from audiobook_pipeline.services.audit import (
    check_duplicates,
    check_leftover_sources,
    check_metadata_tags,
    check_stale,
    check_structure,
    run_audit,
)
from audiobook_pipeline.utils.ffmpeg import FfmpegError


def _library(tmp_path: Path, files: dict[str, bytes]) -> Path:
    root = tmp_path / "library"
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def _probe(tags: dict[str, str]) -> object:
    return type("Probe", (), {"tags": tags})()


GOOD_TAGS = {
    "artist": "Author",
    "album_artist": "Author",
    "album": "Book",
    "title": "Book",
    "genre": "Fantasy",
    "sort_album": "Book",
    "media_type": "2",
    "composer": "Narrator",
    "date": "2020",
    "comment": "note",
    "description": "description",
}


def test_metadata_finds_missing_mandatory_and_recommended_tags(tmp_path: Path) -> None:
    root = _library(tmp_path, {"Author/Book/book.m4b": b"x"})
    with patch(
        "audiobook_pipeline.services.audit.probe",
        return_value=_probe({"artist": "Unknown"}),
    ):
        findings = check_metadata_tags(root)
    assert any(
        finding.message == "Missing mandatory tag: genre" for finding in findings
    )
    assert any("Suspicious value" in finding.message for finding in findings)
    assert any("Missing recommended tag" in finding.message for finding in findings)


def test_metadata_reports_probe_failure_without_writing(tmp_path: Path) -> None:
    root = _library(tmp_path, {"Author/Book/book.m4b": b"x"})
    before = (root / "Author/Book/book.m4b").read_bytes()
    with patch(
        "audiobook_pipeline.services.audit.probe",
        side_effect=FfmpegError("ffprobe", "bad"),
    ):
        findings = check_metadata_tags(root)
    assert findings[0].severity == "critical"
    assert (root / "Author/Book/book.m4b").read_bytes() == before


def test_duplicates_detects_exact_titles_and_multipart_is_not_warning(
    tmp_path: Path,
) -> None:
    root = _library(
        tmp_path,
        {
            "A/One/Homeland.m4b": b"x",
            "B/Two/Homeland.m4b": b"x",
            "C/Three/Book, Part 1.m4b": b"x",
            "C/Three/Book, Part 2.m4b": b"x",
        },
    )
    findings = check_duplicates(root)
    assert any("Duplicate title" in finding.message for finding in findings)
    assert not any(
        finding.path == Path("C/Three/Book, Part 1.m4b")
        and "Directory contains" in finding.message
        for finding in findings
    )


def test_duplicates_detects_near_titles_across_folders(tmp_path: Path) -> None:
    root = _library(
        tmp_path,
        {"Author/One/The Way of Kings.m4b": b"x", "Author/Two/Way of Kings.m4b": b"x"},
    )
    assert any(
        "Near-duplicate" in finding.message for finding in check_duplicates(root)
    )


def test_structure_and_sources_report_issues_without_fix_actions(
    tmp_path: Path,
) -> None:
    root = _library(
        tmp_path,
        {
            "Author/book.m4b": b"x",
            "Author/Book/left.mp3": b"x",
            "Author/Book/book.m4b": b"x",
            "Author/Series/Book/Extra/file.m4b": b"x",
        },
    )
    assert any(
        "missing book subfolder" in finding.message for finding in check_structure(root)
    )
    assert any(
        "Nested too deep" in finding.message for finding in check_structure(root)
    )
    source = next(
        finding
        for finding in check_leftover_sources(root)
        if finding.path == Path("Author/Book/left.mp3")
    )
    assert source.severity == "warning"
    assert source.fixable is False
    assert source.fix_action == ""


def test_stale_is_explicitly_skipped_without_external_query(tmp_path: Path) -> None:
    root = _library(tmp_path, {"Author/Book/book.m4b": b"x"})
    assert check_stale(root)[0].message.startswith("Skipped")


def test_run_audit_runs_only_selected_check(tmp_path: Path) -> None:
    root = _library(tmp_path, {"Author/Book/book.m4b": b"x"})
    with patch(
        "audiobook_pipeline.services.audit.probe", return_value=_probe(GOOD_TAGS)
    ):
        report = run_audit(root, checks=("tags",))
    assert report.total_files == 1
    assert report.findings == ()


def test_library_cli_rejects_nonexistent_source_and_diff_target(tmp_path: Path) -> None:
    runner = CliRunner()
    existing = _library(tmp_path, {})
    missing = tmp_path / "missing"
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("x")
    assert runner.invoke(main, [str(missing)]).exit_code != 0
    assert runner.invoke(main, [str(existing), "--diff", str(missing)]).exit_code != 0
    assert runner.invoke(main, [str(file_path)]).exit_code != 0


def test_library_cli_json_keeps_audit_summary_and_diff_compatibility(
    tmp_path: Path,
) -> None:
    root = _library(tmp_path, {"Author/Book/book.m4b": b"x"})
    target = tmp_path / "target"
    target.mkdir()
    runner = CliRunner()
    audit = runner.invoke(main, [str(root), "--check", "stale", "--json-output"])
    diff = runner.invoke(main, [str(root), "--diff", str(target), "--json-output"])
    audit_payload = json.loads(audit.output)
    diff_payload = json.loads(diff.output)
    assert audit_payload["summary"] == {
        "total_issues": 1,
        "critical": 0,
        "warning": 0,
        "info": 1,
        "fixable": 0,
    }
    assert all(
        finding["fixable"] is False and finding["fix_action"] == ""
        for finding in audit_payload["findings"]
    )
    assert diff_payload["missing"] == 1
    assert isinstance(diff_payload["missing"], int)
    assert len(diff_payload["missing_books"]) == 1


def test_library_cli_exits_one_for_critical_findings(tmp_path: Path) -> None:
    root = _library(tmp_path, {"book.m4b": b"x"})
    with patch(
        "audiobook_pipeline.services.audit.probe",
        return_value=_probe(GOOD_TAGS),
    ):
        result = CliRunner().invoke(main, [str(root), "--check", "structure"])

    assert result.exit_code == 1


def test_library_cli_keeps_warnings_report_only(tmp_path: Path) -> None:
    root = _library(tmp_path, {"Author/book.m4b": b"x"})
    result = CliRunner().invoke(main, [str(root), "--check", "structure"])

    assert result.exit_code == 0


def test_library_diff_exits_one_when_books_are_missing(tmp_path: Path) -> None:
    source = _library(tmp_path, {"Author/Book/book.m4b": b"x"})
    target = tmp_path / "target"
    target.mkdir()

    result = CliRunner().invoke(main, [str(source), "--diff", str(target)])

    assert result.exit_code == 1
