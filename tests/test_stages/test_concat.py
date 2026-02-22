"""Tests for concat stage."""

from pathlib import Path
from unittest.mock import patch

from audiobook_pipeline.config import PipelineConfig
from audiobook_pipeline.manifest import Manifest
from audiobook_pipeline.stages.concat import run


class TestConcatStage:
    def _make_config(self, tmp_path):
        return PipelineConfig(
            _env_file=None,
            work_dir=tmp_path / "work",
            manifest_dir=tmp_path / "manifests",
        )

    def _setup_work_dir(self, config, book_hash, audio_files):
        work_dir = config.work_dir / book_hash
        work_dir.mkdir(parents=True)
        file_list = work_dir / "audio_files.txt"
        file_list.write_text("\n".join(str(f) for f in audio_files) + "\n")
        return work_dir

    @patch("audiobook_pipeline.stages.concat.get_duration", return_value=63.0)
    def test_generates_files_txt(self, mock_dur, tmp_path):
        config = self._make_config(tmp_path)
        config.manifest_dir.mkdir(parents=True)
        manifest = Manifest(config.manifest_dir)
        book_hash = "testconcat01"
        manifest.create(book_hash, "/src/book", "convert")

        audio_files = [
            Path("/src/book/Chapter 01.mp3"),
            Path("/src/book/Chapter 02.mp3"),
        ]
        self._setup_work_dir(config, book_hash, audio_files)

        run(
            source_path=Path("/src/book"),
            book_hash=book_hash,
            config=config,
            manifest=manifest,
        )

        files_txt = (config.work_dir / book_hash / "files.txt").read_text()
        assert "file '/src/book/Chapter 01.mp3'" in files_txt
        assert "file '/src/book/Chapter 02.mp3'" in files_txt

    @patch("audiobook_pipeline.stages.concat.get_duration", return_value=63.0)
    def test_escapes_single_quotes(self, mock_dur, tmp_path):
        config = self._make_config(tmp_path)
        config.manifest_dir.mkdir(parents=True)
        manifest = Manifest(config.manifest_dir)
        book_hash = "testconcat02"
        manifest.create(book_hash, "/src/book", "convert")

        audio_files = [Path("/src/book/O'Brien Chapter 01.mp3")]
        self._setup_work_dir(config, book_hash, audio_files)

        run(
            source_path=Path("/src/book"),
            book_hash=book_hash,
            config=config,
            manifest=manifest,
        )

        files_txt = (config.work_dir / book_hash / "files.txt").read_text()
        assert "O'\\''Brien" in files_txt

    @patch("audiobook_pipeline.stages.concat.get_duration", return_value=63.0)
    def test_metadata_has_chapters(self, mock_dur, tmp_path):
        config = self._make_config(tmp_path)
        config.manifest_dir.mkdir(parents=True)
        manifest = Manifest(config.manifest_dir)
        book_hash = "testconcat03"
        manifest.create(book_hash, "/src/book", "convert")

        audio_files = [
            Path("/src/book/Chapter 01.mp3"),
            Path("/src/book/Chapter 02.mp3"),
        ]
        self._setup_work_dir(config, book_hash, audio_files)

        run(
            source_path=Path("/src/book"),
            book_hash=book_hash,
            config=config,
            manifest=manifest,
        )

        metadata = (config.work_dir / book_hash / "metadata.txt").read_text()
        assert ";FFMETADATA1" in metadata
        assert "[CHAPTER]" in metadata
        assert "TIMEBASE=1/1000" in metadata
        assert "START=0" in metadata
        assert "END=63000" in metadata  # 63s * 1000
        assert "title=Chapter 01" in metadata

    @patch("audiobook_pipeline.stages.concat.get_duration", return_value=300.0)
    def test_single_file_no_chapters(self, mock_dur, tmp_path):
        config = self._make_config(tmp_path)
        config.manifest_dir.mkdir(parents=True)
        manifest = Manifest(config.manifest_dir)
        book_hash = "testconcat04"
        manifest.create(book_hash, "/src/book", "convert")

        audio_files = [Path("/src/book/audiobook.mp3")]
        self._setup_work_dir(config, book_hash, audio_files)

        run(
            source_path=Path("/src/book"),
            book_hash=book_hash,
            config=config,
            manifest=manifest,
        )

        metadata = (config.work_dir / book_hash / "metadata.txt").read_text()
        assert ";FFMETADATA1" in metadata
        assert "[CHAPTER]" not in metadata

    def test_missing_audio_files_txt_fails(self, tmp_path):
        config = self._make_config(tmp_path)
        config.manifest_dir.mkdir(parents=True)
        manifest = Manifest(config.manifest_dir)
        book_hash = "testconcat05"
        manifest.create(book_hash, "/src/book", "convert")

        run(
            source_path=Path("/src/book"),
            book_hash=book_hash,
            config=config,
            manifest=manifest,
        )

        data = manifest.read(book_hash)
        assert data["stages"]["concat"]["status"] == "failed"

    @patch("audiobook_pipeline.stages.concat.get_duration", return_value=60.0)
    def test_updates_manifest_chapter_count(self, mock_dur, tmp_path):
        config = self._make_config(tmp_path)
        config.manifest_dir.mkdir(parents=True)
        manifest = Manifest(config.manifest_dir)
        book_hash = "testconcat06"
        manifest.create(book_hash, "/src/book", "convert")

        audio_files = [Path(f"/src/book/ch{i:02d}.mp3") for i in range(5)]
        self._setup_work_dir(config, book_hash, audio_files)

        run(
            source_path=Path("/src/book"),
            book_hash=book_hash,
            config=config,
            manifest=manifest,
        )

        data = manifest.read(book_hash)
        assert data["metadata"]["chapter_count"] == 5
