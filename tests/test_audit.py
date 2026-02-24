"""Tests for ops/audit.py -- library audit checks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from audiobook_pipeline.ops.audit import (
    ALL_CHECKS,
    AuditFinding,
    AuditReport,
    _is_franchise_folder,
    _normalize_author,
    _normalize_for_dedup,
    apply_fixes,
    check_duplicates,
    check_leftover_sources,
    check_metadata_tags,
    check_stale_plex,
    check_structure,
    run_audit,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_library(tmp_path: Path, structure: dict[str, bytes | str]) -> Path:
    """Create a mock library directory structure.

    Keys are relative paths, values are file contents (bytes or str).
    """
    lib = tmp_path / "AudioBooks"
    for rel_path, content in structure.items():
        full = lib / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            full.write_bytes(content)
        else:
            full.write_text(content)
    return lib


def _fake_ffprobe_tags(tags: dict[str, str]):
    """Return a mock for _ffprobe_tags that returns the given tags."""

    def _mock(path: Path) -> dict[str, str]:
        return {k.lower(): v for k, v in tags.items()}

    return _mock


GOOD_TAGS = {
    "artist": "Brandon Sanderson, Michael Kramer",
    "album_artist": "Brandon Sanderson",
    "album": "The Way of Kings",
    "title": "The Way of Kings",
    "genre": "Fantasy",
    "sort_album": "Stormlight Archive 1 - The Way of Kings",
    "media_type": "2",
    "composer": "Michael Kramer",
    "date": "2010",
    "comment": "A great book",
    "description": "A great book",
}


# ---------------------------------------------------------------------------
# AuditFinding / AuditReport
# ---------------------------------------------------------------------------


class TestAuditFinding:
    def test_to_dict(self):
        f = AuditFinding(
            check="tags",
            severity="critical",
            path="Author/Book/file.m4b",
            message="Missing tag",
            fixable=True,
            fix_action="delete",
        )
        d = f.to_dict()
        assert d["check"] == "tags"
        assert d["fixable"] is True

    def test_defaults(self):
        f = AuditFinding(check="tags", severity="info", path="a.m4b", message="ok")
        assert f.fixable is False
        assert f.fix_action == ""


class TestAuditReport:
    def test_counts(self):
        r = AuditReport(library_root="/test", total_files=10)
        r.findings = [
            AuditFinding("tags", "critical", "a", "m1"),
            AuditFinding("tags", "critical", "b", "m2"),
            AuditFinding("tags", "warning", "c", "m3"),
            AuditFinding("tags", "info", "d", "m4", fixable=True),
        ]
        assert r.critical_count == 2
        assert r.warning_count == 1
        assert r.info_count == 1
        assert r.fixable_count == 1

    def test_to_dict(self):
        r = AuditReport(library_root="/test", total_files=5)
        r.findings = [AuditFinding("tags", "warning", "a", "m1")]
        d = r.to_dict()
        assert d["summary"]["total_issues"] == 1
        assert len(d["findings"]) == 1


# ---------------------------------------------------------------------------
# Check 1: Metadata tags
# ---------------------------------------------------------------------------


class TestCheckMetadataTags:
    def test_good_tags_no_findings(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"Author/Book/book.m4b": b"\x00"},
        )
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(GOOD_TAGS)
        ):
            findings = check_metadata_tags(lib)
        # Should have no critical or warning findings
        critical_warning = [
            f for f in findings if f.severity in ("critical", "warning")
        ]
        assert len(critical_warning) == 0

    def test_missing_mandatory_tag(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        tags = {**GOOD_TAGS}
        del tags["genre"]
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(tags)
        ):
            findings = check_metadata_tags(lib)
        critical = [f for f in findings if f.severity == "critical"]
        assert any("genre" in f.message for f in critical)

    def test_suspicious_artist_value(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        tags = {**GOOD_TAGS, "album_artist": "Unknown"}
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(tags)
        ):
            findings = check_metadata_tags(lib)
        critical = [f for f in findings if f.severity == "critical"]
        assert any(
            "Suspicious value" in f.message and "album_artist" in f.message
            for f in critical
        )

    def test_title_matches_author(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        tags = {
            **GOOD_TAGS,
            "title": "Brandon Sanderson",
            "album_artist": "Brandon Sanderson",
        }
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(tags)
        ):
            findings = check_metadata_tags(lib)
        warnings = [f for f in findings if f.severity == "warning"]
        assert any("matches album_artist" in f.message for f in warnings)

    def test_genre_audiobook_warning(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        tags = {**GOOD_TAGS, "genre": "Audiobook"}
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(tags)
        ):
            findings = check_metadata_tags(lib)
        warnings = [f for f in findings if f.severity == "warning"]
        assert any("Audiobook" in f.message for f in warnings)

    def test_ffprobe_failure(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        with patch("audiobook_pipeline.ops.audit._ffprobe_tags", return_value=None):
            findings = check_metadata_tags(lib)
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert "corrupt" in findings[0].message

    def test_missing_media_type(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        tags = {k: v for k, v in GOOD_TAGS.items() if k != "media_type"}
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(tags)
        ):
            findings = check_metadata_tags(lib)
        warnings = [f for f in findings if f.severity == "warning"]
        assert any("media_type" in f.message for f in warnings)

    def test_missing_recommended_tags(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        tags = {
            k: v
            for k, v in GOOD_TAGS.items()
            if k not in ("composer", "date", "comment", "description")
        }
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(tags)
        ):
            findings = check_metadata_tags(lib)
        info = [f for f in findings if f.severity == "info"]
        assert len(info) >= 3  # composer, date, comment/description

    def test_no_m4b_files(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/readme.txt": "hello"})
        with patch("audiobook_pipeline.ops.audit._ffprobe_tags") as mock:
            findings = check_metadata_tags(lib)
        mock.assert_not_called()
        assert findings == []


# ---------------------------------------------------------------------------
# Check 2: Duplicates
# ---------------------------------------------------------------------------


class TestCheckDuplicates:
    def test_no_duplicates(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {
                "Author A/Book One/Book One.m4b": b"\x00",
                "Author B/Book Two/Book Two.m4b": b"\x00",
            },
        )
        findings = check_duplicates(lib)
        assert len(findings) == 0

    def test_exact_duplicate_titles(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {
                "R.A. Salvatore/Homeland/Homeland.m4b": b"\x00",
                "R. A. Salvatore/Homeland/Homeland.m4b": b"\x00",
            },
        )
        findings = check_duplicates(lib)
        dupe_findings = [f for f in findings if "Duplicate title" in f.message]
        assert len(dupe_findings) >= 1

    def test_multiple_m4b_in_dir_different_titles(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {
                "Author/Book/Totally Different.m4b": b"\x00",
                "Author/Book/Another Book.m4b": b"\x00",
            },
        )
        findings = check_duplicates(lib)
        multi = [f for f in findings if "Directory contains" in f.message]
        assert len(multi) == 1
        assert multi[0].severity == "warning"

    def test_multipart_m4b_in_dir_is_info(self, tmp_path):
        """Multi-part files (Part 1, Part 2) should be info, not warning."""
        lib = _make_library(
            tmp_path,
            {
                "Author/Book/Book Title, Part 1.m4b": b"\x00",
                "Author/Book/Book Title, Part 2.m4b": b"\x00",
                "Author/Book/Book Title, Part 3.m4b": b"\x00",
            },
        )
        findings = check_duplicates(lib)
        multi = [f for f in findings if "Multi-part book" in f.message]
        assert len(multi) == 1
        assert multi[0].severity == "info"
        # Should NOT have a warning about multiple M4B files
        warnings = [f for f in findings if "Directory contains" in f.message]
        assert len(warnings) == 0

    def test_near_duplicates(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {
                "Author/The Way of Kings/The Way of Kings.m4b": b"\x00",
                "Author/Way of Kings/Way of Kings.m4b": b"\x00",
            },
        )
        findings = check_duplicates(lib)
        # Should catch either as exact dup (after normalization) or near-dup
        assert len(findings) >= 1


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


class TestNormalizeForDedup:
    def test_strips_part_suffix(self):
        assert _normalize_for_dedup("homeland, part 1") == "homeland"
        assert _normalize_for_dedup("homeland part 3") == "homeland"

    def test_strips_asin(self):
        assert (
            _normalize_for_dedup("the way of kings [B00AAI79WY]") == "the way of kings"
        )

    def test_strips_unabridged(self):
        assert (
            _normalize_for_dedup("the way of kings (unabridged)") == "the way of kings"
        )
        assert _normalize_for_dedup("the way of kings (Abridged)") == "the way of kings"

    def test_strips_author_prefix(self):
        result = _normalize_for_dedup(
            "b. t. narro - the rhythm of rivalry", author="B. T. Narro"
        )
        assert "rhythm of rivalry" in result
        assert "narro" not in result

    def test_strips_book_n_suffix(self):
        assert (
            _normalize_for_dedup("the rhythm of rivalry - book 1")
            == "the rhythm of rivalry"
        )

    def test_strips_series_prefix(self):
        assert _normalize_for_dedup("book 3 - homeland") == "homeland"
        assert _normalize_for_dedup("3 - homeland") == "homeland"

    def test_basic_normalization(self):
        assert _normalize_for_dedup("The Way of Kings") == "the way of kings"


class TestNormalizeAuthor:
    def test_strips_periods(self):
        assert _normalize_author("R.A. Salvatore") == "ra salvatore"

    def test_period_spacing_variations(self):
        # R.A. Salvatore, R. A. Salvatore, R.A Salvatore all normalize the same
        a = _normalize_author("R.A. Salvatore")
        b = _normalize_author("R. A. Salvatore")
        c = _normalize_author("R.A Salvatore")
        assert a == b == c

    def test_and_ampersand_equivalence(self):
        a = _normalize_author("Margaret Weis & Tracy Hickman")
        b = _normalize_author("Margaret Weis and Tracy Hickman")
        assert a == b

    def test_edited_by_prefix(self):
        assert _normalize_author("Edited by John Smith") == "john smith"

    def test_whitespace_collapse(self):
        assert _normalize_author("  John   Smith  ") == "john smith"


class TestIsFranchiseFolder:
    def test_known_franchises(self):
        assert _is_franchise_folder("Dragonlance") is True
        assert _is_franchise_folder("Forgotten Realms") is True
        assert _is_franchise_folder("Star Wars") is True

    def test_not_franchise(self):
        assert _is_franchise_folder("Brandon Sanderson") is False


# ---------------------------------------------------------------------------
# Check 3: Structure
# ---------------------------------------------------------------------------


class TestCheckStructure:
    def test_correct_structure(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"Author/Book Title/Book Title.m4b": b"\x00"},
        )
        findings = check_structure(lib)
        # No structural issues (may have info about .author-override absence)
        problems = [f for f in findings if f.severity in ("critical", "warning")]
        assert len(problems) == 0

    def test_m4b_directly_under_author(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"Author/book.m4b": b"\x00"},
        )
        findings = check_structure(lib)
        warnings = [f for f in findings if f.severity == "warning"]
        assert any("missing book subfolder" in f.message for f in warnings)

    def test_too_deeply_nested(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"Author/Series/Book/SubDir/Extra/file.m4b": b"\x00"},
        )
        findings = check_structure(lib)
        warnings = [f for f in findings if f.severity == "warning"]
        assert any("too deep" in f.message.lower() for f in warnings)

    def test_brackets_in_filename(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"Author/Book/[01] Chapter One.m4b": b"\x00"},
        )
        findings = check_structure(lib)
        warnings = [f for f in findings if f.severity == "warning"]
        assert any("brackets" in f.message.lower() for f in warnings)

    def test_author_override_noted(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {
                "Author/Book/book.m4b": b"\x00",
                "Author/.author-override": "",
            },
        )
        findings = check_structure(lib)
        info = [f for f in findings if f.severity == "info"]
        assert any("override" in f.message.lower() for f in info)


# ---------------------------------------------------------------------------
# Check 4: Leftover sources
# ---------------------------------------------------------------------------


class TestCheckLeftoverSources:
    def test_no_sources(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        findings = check_leftover_sources(lib)
        assert len(findings) == 0

    def test_leftover_mp3_with_m4b(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {
                "Author/Book/book.m4b": b"\x00",
                "Author/Book/chapter1.mp3": b"\x00",
                "Author/Book/chapter2.mp3": b"\x00",
            },
        )
        findings = check_leftover_sources(lib)
        assert len(findings) == 2
        assert all(f.fixable for f in findings)
        assert all(f.fix_action == "delete" for f in findings)

    def test_source_without_m4b(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"Author/Book/chapter1.mp3": b"\x00"},
        )
        findings = check_leftover_sources(lib)
        assert len(findings) == 1
        assert findings[0].severity == "critical"
        assert not findings[0].fixable

    def test_ignores_unsorted(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"_unsorted/stuff/file.mp3": b"\x00"},
        )
        findings = check_leftover_sources(lib)
        assert len(findings) == 0

    def test_multiple_source_types(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {
                "Author/Book/book.m4b": b"\x00",
                "Author/Book/track.flac": b"\x00",
                "Author/Book/track.wav": b"\x00",
            },
        )
        findings = check_leftover_sources(lib)
        assert len(findings) == 2


# ---------------------------------------------------------------------------
# Check 5: Stale Plex (mocked)
# ---------------------------------------------------------------------------


class TestCheckStalePlex:
    def test_no_token_skips(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        findings = check_stale_plex(lib, plex_token="")
        assert len(findings) == 1
        assert "Skipped" in findings[0].message


# ---------------------------------------------------------------------------
# Fix actions
# ---------------------------------------------------------------------------


class TestApplyFixes:
    def test_delete_action(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {
                "Author/Book/book.m4b": b"\x00",
                "Author/Book/leftover.mp3": b"\x00",
            },
        )
        findings = [
            AuditFinding(
                check="sources",
                severity="warning",
                path="Author/Book/leftover.mp3",
                message="Leftover",
                fixable=True,
                fix_action="delete",
            )
        ]
        actions = apply_fixes(lib, findings)
        assert len(actions) == 1
        assert "Deleted" in actions[0]
        assert not (lib / "Author/Book/leftover.mp3").exists()

    def test_dry_run_no_delete(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"Author/Book/leftover.mp3": b"\x00"},
        )
        findings = [
            AuditFinding(
                check="sources",
                severity="warning",
                path="Author/Book/leftover.mp3",
                message="Leftover",
                fixable=True,
                fix_action="delete",
            )
        ]
        actions = apply_fixes(lib, findings, dry_run=True)
        assert len(actions) == 1
        assert "DRY-RUN" in actions[0]
        assert (lib / "Author/Book/leftover.mp3").exists()

    def test_touch_action(self, tmp_path):
        lib = _make_library(
            tmp_path,
            {"Author/Book/book.m4b": b"\x00"},
        )
        findings = [
            AuditFinding(
                check="stale",
                severity="warning",
                path="Author/Book/book.m4b",
                message="Stale",
                fixable=True,
                fix_action="touch",
            )
        ]
        actions = apply_fixes(lib, findings)
        assert len(actions) == 1
        assert "Touched" in actions[0]

    def test_skips_non_fixable(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        findings = [
            AuditFinding(
                check="tags",
                severity="critical",
                path="Author/Book/book.m4b",
                message="Missing tag",
            )
        ]
        actions = apply_fixes(lib, findings)
        assert len(actions) == 0


# ---------------------------------------------------------------------------
# run_audit orchestrator
# ---------------------------------------------------------------------------


class TestRunAudit:
    def test_runs_selected_checks(self, tmp_path):
        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(GOOD_TAGS)
        ):
            report = run_audit(lib, checks=("tags",))
        assert report.total_files == 1
        # Only tag findings, no structure/duplicate findings
        assert all(f.check == "tags" for f in report.findings)

    def test_empty_library(self, tmp_path):
        lib = tmp_path / "AudioBooks"
        lib.mkdir()
        report = run_audit(lib, checks=("duplicates", "structure", "sources"))
        assert report.total_files == 0
        assert len(report.findings) == 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


class TestCLIAudit:
    def test_help_output(self):
        from click.testing import CliRunner

        from audiobook_pipeline.cli_audit import main

        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "Audit an audiobook library" in result.output
        assert "--fix" in result.output
        assert "--check" in result.output

    def test_json_output(self, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from loguru import logger

        from audiobook_pipeline.cli_audit import main

        lib = _make_library(tmp_path, {"Author/Book/book.m4b": b"\x00"})
        monkeypatch.setattr("audiobook_pipeline.cli._find_config_file", lambda: None)
        monkeypatch.chdir(tmp_path)

        # Suppress loguru to keep stdout clean for JSON parsing
        logger.remove()
        with patch(
            "audiobook_pipeline.ops.audit._ffprobe_tags", _fake_ffprobe_tags(GOOD_TAGS)
        ):
            runner = CliRunner()
            result = runner.invoke(main, [str(lib), "--json-output", "--check", "tags"])

        assert result.exit_code == 0, result.output + str(result.exception or "")
        # Extract JSON from output (skip any non-JSON prefix lines)
        output = result.output.strip()
        json_start = output.index("{")
        data = json.loads(output[json_start:])
        assert "summary" in data
        assert "findings" in data
