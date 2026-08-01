"""Tests for chapter handling in concat + ffprobe.read_chapters.

Chapters are the point of an M4B: they are what makes an 11-hour book
navigable. The concat stage derived them from FILE BOUNDARIES only, and its
single-file branch wrote a header with no chapters at all.

Measured 2026-08-01 on the real files:
    The Martian.m4b            160 embedded chapters -> 0 written
    $100M Leads.m4b             29 embedded chapters -> 0 written
    Legend of Drizzt Book 36    40 embedded chapters -> 0 written
Verified end-to-end that the pipeline's own ffmpeg command DOES carry chapters
into the encoded output once metadata.txt contains them.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from audiobook_pipeline.ffprobe import read_chapters


def _ffprobe_json(chapters: list[dict]) -> object:
    class R:
        returncode = 0
        stdout = json.dumps({"chapters": chapters})

    return R()


def _chapter(start: float, end: float, title: str | None) -> dict:
    d = {"start_time": str(start), "end_time": str(end)}
    if title is not None:
        d["tags"] = {"title": title}
    return d


class TestReadChapters:
    def test_reads_embedded_chapters(self):
        probe = _ffprobe_json(
            [
                _chapter(0, 30.467, "Opening Credits"),
                _chapter(30.467, 31.8, "Chapter 1"),
            ]
        )
        with patch("audiobook_pipeline.ffprobe.subprocess.run", return_value=probe):
            got = read_chapters(Path("/fake/book.m4b"))
        assert got == [
            {"start_ms": 0, "end_ms": 30467, "title": "Opening Credits"},
            {"start_ms": 30467, "end_ms": 31800, "title": "Chapter 1"},
        ]

    def test_untitled_chapter_gets_a_positional_name(self):
        with patch(
            "audiobook_pipeline.ffprobe.subprocess.run",
            return_value=_ffprobe_json([_chapter(0, 10, None)]),
        ):
            got = read_chapters(Path("/fake/book.m4b"))
        assert got[0]["title"] == "Chapter 1"

    def test_no_chapters_returns_empty(self):
        with patch(
            "audiobook_pipeline.ffprobe.subprocess.run",
            return_value=_ffprobe_json([]),
        ):
            assert read_chapters(Path("/fake/book.m4b")) == []

    def test_zero_length_chapter_skipped(self):
        """A start==end mark is not navigable and breaks players."""
        with patch(
            "audiobook_pipeline.ffprobe.subprocess.run",
            return_value=_ffprobe_json(
                [_chapter(5, 5, "Empty"), _chapter(5, 10, "Ok")]
            ),
        ):
            got = read_chapters(Path("/fake/book.m4b"))
        assert [c["title"] for c in got] == ["Ok"]

    def test_malformed_chapter_skipped_not_fatal(self):
        bad = {"start_time": "not-a-number", "end_time": "10"}
        with patch(
            "audiobook_pipeline.ffprobe.subprocess.run",
            return_value=_ffprobe_json([bad, _chapter(0, 10, "Good")]),
        ):
            got = read_chapters(Path("/fake/book.m4b"))
        assert [c["title"] for c in got] == ["Good"]

    def test_probe_failure_returns_empty(self):
        class R:
            returncode = 1
            stdout = ""

        with patch("audiobook_pipeline.ffprobe.subprocess.run", return_value=R()):
            assert read_chapters(Path("/fake/book.m4b")) == []

    def test_unparseable_output_returns_empty(self):
        class R:
            returncode = 0
            stdout = "<html>not json</html>"

        with patch("audiobook_pipeline.ffprobe.subprocess.run", return_value=R()):
            assert read_chapters(Path("/fake/book.m4b")) == []


class TestConcatWritesChapters:
    """The metadata.txt that convert consumes must carry the chapters."""

    def _run_concat(self, tmp_path, audio_files, chapters_by_name, durations):
        """Run the concat stage over a fake book, return metadata.txt text."""
        from audiobook_pipeline.config import PipelineConfig
        from audiobook_pipeline.pipeline_db import PipelineDB
        from audiobook_pipeline.stages import concat

        book = tmp_path / "Book"
        book.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in audio_files:
            f = book / name
            f.write_bytes(b"\x00")
            paths.append(f)

        cfg = PipelineConfig(_env_file=None, work_dir=str(tmp_path / "work"))
        db = PipelineDB(cfg.db_path)
        h = "h" * 16
        db.create(h, str(book), "convert")
        work = Path(cfg.work_dir) / h
        work.mkdir(parents=True, exist_ok=True)
        (work / "audio_files.txt").write_text("\n".join(str(p) for p in paths) + "\n")

        with (
            patch(
                "audiobook_pipeline.stages.concat.get_duration",
                side_effect=lambda p: durations[p.name],
            ),
            patch(
                "audiobook_pipeline.stages.concat.read_chapters",
                side_effect=lambda p: chapters_by_name.get(p.name, []),
            ),
        ):
            concat.run(book, h, cfg, db, dry_run=False)

        return (work / "metadata.txt").read_text()

    def test_single_file_embedded_chapters_are_written(self, tmp_path):
        """THE bug: one already-chaptered m4b produced zero chapters."""
        md = self._run_concat(
            tmp_path,
            ["The Martian.m4b"],
            {
                "The Martian.m4b": [
                    {"start_ms": 0, "end_ms": 30467, "title": "Opening Credits"},
                    {"start_ms": 30467, "end_ms": 60000, "title": "Chapter 1"},
                ]
            },
            {"The Martian.m4b": 60.0},
        )
        assert md.count("[CHAPTER]") == 2
        assert "title=Opening Credits" in md
        assert "title=Chapter 1" in md

    def test_single_file_without_chapters_is_one_chapter(self, tmp_path):
        """No marks to preserve: the file itself is the chapter."""
        md = self._run_concat(tmp_path, ["Book.mp3"], {}, {"Book.mp3": 3600.0})
        assert md.count("[CHAPTER]") == 1
        assert "title=Book" in md

    def test_file_per_chapter_still_uses_file_boundaries(self, tmp_path):
        """The original behaviour must be unchanged."""
        md = self._run_concat(
            tmp_path,
            ["01 - One.mp3", "02 - Two.mp3", "03 - Three.mp3"],
            {},
            {"01 - One.mp3": 10.0, "02 - Two.mp3": 20.0, "03 - Three.mp3": 30.0},
        )
        assert md.count("[CHAPTER]") == 3
        assert "START=0" in md
        assert "START=10000" in md
        assert "START=30000" in md

    def test_multi_file_embedded_chapters_are_offset_and_kept(self, tmp_path):
        """A 2-part book whose parts each carry chapters keeps ALL of them.

        Previously this produced 2 chapters (one per file) and discarded the
        4 real ones.
        """
        md = self._run_concat(
            tmp_path,
            ["Part 1.m4b", "Part 2.m4b"],
            {
                "Part 1.m4b": [
                    {"start_ms": 0, "end_ms": 5000, "title": "A"},
                    {"start_ms": 5000, "end_ms": 10000, "title": "B"},
                ],
                "Part 2.m4b": [
                    {"start_ms": 0, "end_ms": 5000, "title": "C"},
                    {"start_ms": 5000, "end_ms": 10000, "title": "D"},
                ],
            },
            {"Part 1.m4b": 10.0, "Part 2.m4b": 10.0},
        )
        assert md.count("[CHAPTER]") == 4
        # Part 2's marks must be shifted past Part 1's duration.
        assert "START=10000" in md
        assert "START=15000" in md

    def test_chapter_past_end_of_file_is_clamped(self, tmp_path):
        """A bad END in the source must not run past the joined audio."""
        md = self._run_concat(
            tmp_path,
            ["A.m4b", "B.m4b"],
            {"A.m4b": [{"start_ms": 0, "end_ms": 999999, "title": "Runaway"}]},
            {"A.m4b": 10.0, "B.m4b": 10.0},
        )
        assert "END=10000" in md
        assert "999999" not in md


class TestRemoteChapterFallback:
    """Audnexus fills in only when the audio itself carries no marks."""

    def _run(self, tmp_path, files, embedded, durations, remote=None):
        from audiobook_pipeline.config import PipelineConfig
        from audiobook_pipeline.pipeline_db import PipelineDB
        from audiobook_pipeline.stages import concat

        book = tmp_path / "Book"
        book.mkdir(parents=True, exist_ok=True)
        paths = []
        for name in files:
            f = book / name
            f.write_bytes(b"\x00")
            paths.append(f)

        cfg = PipelineConfig(_env_file=None, work_dir=str(tmp_path / "work"))
        db = PipelineDB(cfg.db_path)
        h = "r" * 16
        db.create(h, str(book), "convert")
        work = Path(cfg.work_dir) / h
        work.mkdir(parents=True, exist_ok=True)
        (work / "audio_files.txt").write_text("\n".join(str(p) for p in paths) + "\n")

        with (
            patch(
                "audiobook_pipeline.stages.concat.get_duration",
                side_effect=lambda p: durations[p.name],
            ),
            patch(
                "audiobook_pipeline.stages.concat.read_chapters",
                side_effect=lambda p: embedded.get(p.name, []),
            ),
            patch(
                "audiobook_pipeline.stages.concat._remote_chapters",
                return_value=remote or [],
            ) as mock_remote,
        ):
            concat.run(book, h, cfg, db, dry_run=False)

        md = (work / "metadata.txt").read_text()
        prov = ((db.read(h) or {}).get("metadata") or {}).get("chapter_source")
        return md, prov, mock_remote

    def test_remote_chapters_used_when_none_embedded(self, tmp_path):
        """19 hour-long file splits become the book's 42 real chapters."""
        files = [f"Part {n:02d}.mp3" for n in range(1, 20)]
        remote = [(i * 1000, (i + 1) * 1000, f"Chapter {i + 1}") for i in range(42)]
        md, prov, _ = self._run(
            tmp_path, files, {}, dict.fromkeys(files, 3600.0), remote
        )
        assert md.count("[CHAPTER]") == 42
        assert prov == "audnexus"

    def test_embedded_chapters_are_never_replaced(self, tmp_path):
        """Marks from the actual file always beat a remote guess."""
        embedded = {
            "Book.m4b": [{"start_ms": 0, "end_ms": 500, "title": "Real Chapter"}]
        }
        remote = [(0, 1000, "Remote Chapter")]
        md, prov, mock_remote = self._run(
            tmp_path, ["Book.m4b"], embedded, {"Book.m4b": 1.0}, remote
        )
        assert "title=Real Chapter" in md
        assert "Remote Chapter" not in md
        assert prov == "embedded"
        mock_remote.assert_not_called()

    def test_falls_back_to_file_boundaries_when_remote_has_nothing(self, tmp_path):
        """A rejected or missing remote table keeps the file-boundary chapters.

        This is the Servant of the Crown case: Audnexus described a different
        edition (13.42% duration gap) and was refused, so the 3 source files
        stay as 3 chapters rather than the book getting a wrong chapter map.
        """
        files = ["Part 1.mp3", "Part 2.mp3", "Part 3.mp3"]
        md, prov, _ = self._run(
            tmp_path, files, {}, dict.fromkeys(files, 3600.0), remote=[]
        )
        assert md.count("[CHAPTER]") == 3
        assert prov == "file-boundary"
