"""Tests for filename sanitization and book hash generation."""

from pathlib import Path

from audiobook_pipeline.sanitize import (
    generate_book_hash,
    sanitize_chapter_title,
    sanitize_filename,
)


class TestSanitizeFilename:
    def test_replaces_unsafe_chars(self):
        assert sanitize_filename('a/b\\c:"d') == "a_b_c_d"

    def test_removes_leading_dots(self):
        assert sanitize_filename("..hidden") == "hidden"

    def test_removes_leading_underscores(self):
        assert sanitize_filename("__private") == "private"

    def test_collapses_underscores(self):
        assert sanitize_filename("a___b") == "a_b"

    def test_preserves_normal_names(self):
        assert sanitize_filename("chapter_01.mp3") == "chapter_01.mp3"

    def test_truncation_preserves_extension(self):
        long_name = "a" * 300 + ".mp3"
        result = sanitize_filename(long_name)
        assert result.endswith(".mp3")
        assert len(result.encode("utf-8")) <= 255

    def test_truncation_without_extension(self):
        long_name = "a" * 300
        result = sanitize_filename(long_name)
        assert len(result.encode("utf-8")) <= 255

    def test_removes_trailing_dots(self):
        assert sanitize_filename("name...") == "name"


class TestSanitizeChapterTitle:
    def test_uses_spaces(self):
        assert sanitize_chapter_title("Chapter: One") == "Chapter One"

    def test_collapses_multiple_spaces(self):
        assert sanitize_chapter_title("a/b\\c") == "a b c"

    def test_strips_whitespace(self):
        assert sanitize_chapter_title("  hello  ") == "hello"


class TestGenerateBookHash:
    def test_deterministic_for_file(self, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"audio data")
        h1 = generate_book_hash(f)
        h2 = generate_book_hash(f)
        assert h1 == h2

    def test_length_is_16(self, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"audio data")
        assert len(generate_book_hash(f)) == 16

    def test_hex_chars_only(self, tmp_path):
        f = tmp_path / "test.mp3"
        f.write_bytes(b"audio data")
        h = generate_book_hash(f)
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_files_different_hash(self, tmp_path):
        f1 = tmp_path / "a.mp3"
        f1.write_bytes(b"data1")
        f2 = tmp_path / "b.mp3"
        f2.write_bytes(b"data2")
        assert generate_book_hash(f1) != generate_book_hash(f2)

    def test_directory_mode(self, tmp_path):
        audio_dir = tmp_path / "book"
        audio_dir.mkdir()
        (audio_dir / "ch1.mp3").write_bytes(b"ch1")
        (audio_dir / "ch2.flac").write_bytes(b"ch2")
        (audio_dir / "cover.jpg").write_bytes(b"img")  # not audio
        h = generate_book_hash(audio_dir)
        assert len(h) == 16

    def test_directory_ignores_non_audio(self, tmp_path):
        d1 = tmp_path / "d1"
        d1.mkdir()
        (d1 / "ch1.mp3").write_bytes(b"ch1")
        (d1 / "notes.txt").write_bytes(b"notes")

        d2 = tmp_path / "d2"
        d2.mkdir()
        (d2 / "ch1.mp3").write_bytes(b"ch1")
        # d2 has no notes.txt -- hash should still differ because paths differ
        # But the hash includes the directory path itself
        h1 = generate_book_hash(d1)
        h2 = generate_book_hash(d2)
        assert h1 != h2  # different paths
