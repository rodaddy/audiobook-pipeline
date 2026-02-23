"""Tests for pipeline_db.py -- SQLite state machine replacing JSON manifests."""

import threading

import pytest

from audiobook_pipeline.pipeline_db import PipelineDB
from audiobook_pipeline.models import (
    ErrorCategory,
    PipelineMode,
    Stage,
    StageStatus,
)
from audiobook_pipeline.errors import ManifestError


@pytest.fixture
def db(tmp_path):
    pdb = PipelineDB(tmp_path / "test.db")
    yield pdb
    pdb.close()


@pytest.fixture
def book(db):
    """Create a book record and return (db, book_hash)."""
    db.create("abc123", "/input/book", PipelineMode.CONVERT)
    return db, "abc123"


class TestCreate:
    def test_creates_book_record(self, db):
        data = db.create("h1", "/src/book", PipelineMode.CONVERT)
        assert data["book_hash"] == "h1"
        assert data["source_path"] == "/src/book"
        assert data["mode"] == "convert"
        assert data["status"] == "pending"
        assert data["retry_count"] == 0

    def test_all_stages_pending_for_convert(self, db):
        data = db.create("h1", "/src", PipelineMode.CONVERT)
        for stage in Stage:
            assert data["stages"][stage.value]["status"] == "pending"

    def test_enrich_pre_completes_early_stages(self, db):
        data = db.create("h1", "/src/book.m4b", PipelineMode.ENRICH)
        assert data["stages"]["validate"]["status"] == "completed"
        assert data["stages"]["concat"]["status"] == "completed"
        assert data["stages"]["convert"]["status"] == "completed"
        assert data["stages"]["convert"]["output_file"] == "/src/book.m4b"
        assert data["stages"]["asin"]["status"] == "pending"

    def test_organize_pre_completes_three_stages(self, db):
        data = db.create("h1", "/src/book.m4b", PipelineMode.ORGANIZE)
        assert data["stages"]["validate"]["status"] == "completed"
        assert data["stages"]["concat"]["status"] == "completed"
        assert data["stages"]["convert"]["status"] == "completed"
        assert data["stages"]["asin"]["status"] == "pending"
        assert data["stages"]["metadata"]["status"] == "pending"
        assert data["stages"]["organize"]["status"] == "pending"

    def test_returns_created_data(self, db):
        data = db.create("h1", "/src", PipelineMode.CONVERT)
        assert isinstance(data, dict)
        assert "created_at" in data

    def test_create_replaces_existing(self, db):
        db.create("h1", "/src/old", PipelineMode.CONVERT)
        data = db.create("h1", "/src/new", PipelineMode.CONVERT)
        assert data["source_path"] == "/src/new"


class TestRead:
    def test_read_existing(self, book):
        pdb, h = book
        data = pdb.read(h)
        assert data is not None
        assert data["book_hash"] == h

    def test_read_missing_returns_none(self, db):
        assert db.read("nonexistent") is None

    def test_read_field_dotted_path(self, book):
        pdb, h = book
        status = pdb.read_field(h, "stages.validate.status")
        assert status == "pending"

    def test_read_field_missing_returns_none(self, db):
        assert db.read_field("nope", "anything") is None

    def test_read_field_metadata_shortcut(self, db):
        db.create("h1", "/src", PipelineMode.CONVERT)
        db.update("h1", {"metadata": {"parsed_author": "Tolkien"}})
        result = db.read_field("h1", "metadata.parsed_author")
        assert result == "Tolkien"

    def test_read_field_stage_output_file(self, db):
        db.create("h1", "/src", PipelineMode.CONVERT)
        db.update(
            "h1",
            {"stages": {"convert": {"output_file": "/out/book.m4b"}}},
        )
        result = db.read_field("h1", "stages.convert.output_file")
        assert result == "/out/book.m4b"


class TestUpdate:
    def test_update_status(self, book):
        pdb, h = book
        pdb.update(h, {"status": "processing"})
        data = pdb.read(h)
        assert data["status"] == "processing"

    def test_update_metadata(self, book):
        pdb, h = book
        pdb.update(
            h,
            {
                "metadata": {
                    "parsed_author": "Sanderson",
                    "parsed_title": "Mistborn",
                    "target_bitrate": 96,
                }
            },
        )
        data = pdb.read(h)
        assert data["metadata"]["parsed_author"] == "Sanderson"
        assert data["metadata"]["parsed_title"] == "Mistborn"
        assert data["metadata"]["target_bitrate"] == 96

    def test_update_stage_data(self, book):
        pdb, h = book
        pdb.update(
            h,
            {"stages": {"convert": {"output_file": "/out/book.m4b"}}},
        )
        data = pdb.read(h)
        assert data["stages"]["convert"]["output_file"] == "/out/book.m4b"

    def test_update_missing_raises(self, db):
        with pytest.raises(ManifestError):
            db.update("nope", {"status": "x"})


class TestSetStage:
    def test_set_stage_running(self, book):
        pdb, h = book
        pdb.set_stage(h, Stage.VALIDATE, StageStatus.RUNNING)
        status = pdb.read_field(h, "stages.validate.status")
        assert status == "running"

    def test_set_stage_completed_adds_timestamp(self, book):
        pdb, h = book
        pdb.set_stage(h, Stage.VALIDATE, StageStatus.COMPLETED)
        data = pdb.read(h)
        assert data["stages"]["validate"]["status"] == "completed"
        assert "completed_at" in data["stages"]["validate"]

    def test_set_stage_failed_no_timestamp(self, book):
        pdb, h = book
        pdb.set_stage(h, Stage.VALIDATE, StageStatus.FAILED)
        data = pdb.read(h)
        assert data["stages"]["validate"]["status"] == "failed"
        assert "completed_at" not in data["stages"]["validate"]


class TestCheckStatus:
    def test_new_when_no_record(self, db):
        assert db.check_status("nope") == "new"

    def test_pending_after_create(self, book):
        pdb, h = book
        assert pdb.check_status(h) == "pending"

    def test_reflects_updates(self, book):
        pdb, h = book
        pdb.update(h, {"status": "completed"})
        assert pdb.check_status(h) == "completed"


class TestGetNextStage:
    def test_first_stage_for_convert(self, book):
        pdb, h = book
        nxt = pdb.get_next_stage(h, PipelineMode.CONVERT)
        assert nxt == Stage.VALIDATE

    def test_skips_completed_stages(self, book):
        pdb, h = book
        pdb.set_stage(h, Stage.VALIDATE, StageStatus.COMPLETED)
        nxt = pdb.get_next_stage(h, PipelineMode.CONVERT)
        assert nxt == Stage.CONCAT

    def test_returns_none_when_all_done(self, db):
        db.create("done", "/src", PipelineMode.ORGANIZE)
        db.set_stage("done", Stage.ASIN, StageStatus.COMPLETED)
        db.set_stage("done", Stage.METADATA, StageStatus.COMPLETED)
        db.set_stage("done", Stage.ORGANIZE, StageStatus.COMPLETED)
        assert db.get_next_stage("done", PipelineMode.ORGANIZE) is None

    def test_enrich_starts_at_asin(self, db):
        db.create("e1", "/src/b.m4b", PipelineMode.ENRICH)
        nxt = db.get_next_stage("e1", PipelineMode.ENRICH)
        assert nxt == Stage.ASIN

    def test_missing_book_raises(self, db):
        with pytest.raises(ManifestError):
            db.get_next_stage("nope", PipelineMode.CONVERT)


class TestRetryAndError:
    def test_increment_retry(self, book):
        pdb, h = book
        pdb.increment_retry(h)
        data = pdb.read(h)
        assert data["retry_count"] == 1
        pdb.increment_retry(h)
        data = pdb.read(h)
        assert data["retry_count"] == 2

    def test_set_error(self, book):
        pdb, h = book
        pdb.set_error(h, "validate", 2, ErrorCategory.PERMANENT, "bad input")
        data = pdb.read(h)
        err = data["last_error"]
        assert err["stage"] == "validate"
        assert err["exit_code"] == 2
        assert err["category"] == "permanent"
        assert err["message"] == "bad input"
        assert "timestamp" in err

    def test_error_on_missing_raises(self, db):
        with pytest.raises(ManifestError):
            db.set_error("nope", "x", 1, ErrorCategory.TRANSIENT, "msg")

    def test_retry_on_missing_raises(self, db):
        with pytest.raises(ManifestError):
            db.increment_retry("nope")


class TestCoverArt:
    def test_store_and_retrieve(self, book):
        pdb, h = book
        img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        pdb.store_cover(h, img)
        result = pdb.get_cover(h)
        assert result == img

    def test_get_cover_returns_none_when_empty(self, book):
        pdb, h = book
        assert pdb.get_cover(h) is None

    def test_get_cover_returns_none_for_missing_book(self, db):
        assert db.get_cover("nope") is None

    def test_extract_cover_to_file(self, book, tmp_path):
        pdb, h = book
        img = b"\xff\xd8\xff\xe0" + b"\x00" * 50
        pdb.store_cover(h, img)
        cover_path = pdb.extract_cover_to_file(h, tmp_path / "covers")
        assert cover_path is not None
        assert cover_path.exists()
        assert cover_path.read_bytes() == img

    def test_extract_cover_returns_none_when_no_cover(self, book, tmp_path):
        pdb, h = book
        assert pdb.extract_cover_to_file(h, tmp_path / "covers") is None

    def test_cover_art_size_tracked(self, book):
        pdb, h = book
        img = b"\x89PNG" + b"\x00" * 200
        pdb.store_cover(h, img)
        data = pdb.read(h)
        conn = pdb._get_conn()
        row = conn.execute(
            "SELECT cover_art_size FROM books WHERE book_hash = ?", (h,)
        ).fetchone()
        assert row["cover_art_size"] == len(img)


class TestAuthorAliases:
    def test_save_and_get(self, db):
        db.save_alias("J.R.R. Tolkien", "J. R. R. Tolkien")
        assert db.get_alias("J.R.R. Tolkien") == "J. R. R. Tolkien"

    def test_get_missing_returns_none(self, db):
        assert db.get_alias("Unknown") is None

    def test_get_aliases_for(self, db):
        db.save_alias("JRR Tolkien", "J. R. R. Tolkien")
        db.save_alias("Tolkien, J.R.R.", "J. R. R. Tolkien")
        aliases = db.get_aliases_for("J. R. R. Tolkien")
        assert sorted(aliases) == ["JRR Tolkien", "Tolkien, J.R.R."]

    def test_save_same_as_canonical_is_noop(self, db):
        db.save_alias("Same", "Same")
        assert db.get_alias("Same") is None

    def test_upsert_overwrites(self, db):
        db.save_alias("variant", "canonical1")
        db.save_alias("variant", "canonical2")
        assert db.get_alias("variant") == "canonical2"


class TestLocking:
    def test_acquire_and_release(self, db):
        assert db.acquire_reorganize_lock() is True
        db.release_reorganize_lock()

    def test_double_acquire_fails(self, db):
        assert db.acquire_reorganize_lock() is True
        assert db.acquire_reorganize_lock() is False

    def test_acquire_after_release(self, db):
        assert db.acquire_reorganize_lock() is True
        db.release_reorganize_lock()
        assert db.acquire_reorganize_lock() is True


class TestBatchOperations:
    def test_reset_book(self, book):
        pdb, h = book
        pdb.reset_book(h)
        assert pdb.read(h) is None

    def test_list_books(self, db):
        db.create("h1", "/a", PipelineMode.CONVERT)
        db.create("h2", "/b", PipelineMode.ORGANIZE)
        all_books = db.list_books()
        assert len(all_books) == 2

    def test_list_books_filtered(self, db):
        db.create("h1", "/a", PipelineMode.CONVERT)
        db.create("h2", "/b", PipelineMode.ORGANIZE)
        result = db.list_books(mode="organize")
        assert len(result) == 1
        assert result[0]["book_hash"] == "h2"


class TestThreadSafety:
    def test_concurrent_creates(self, tmp_path):
        """Multiple threads can create books without corruption."""
        pdb = PipelineDB(tmp_path / "thread_test.db")
        errors = []

        def worker(i):
            try:
                pdb.create(f"hash_{i}", f"/src/{i}", PipelineMode.CONVERT)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        books = pdb.list_books()
        assert len(books) == 20
        pdb.close()

    def test_concurrent_stage_updates(self, tmp_path):
        """Multiple threads updating different stages simultaneously."""
        pdb = PipelineDB(tmp_path / "stage_test.db")
        pdb.create("book1", "/src", PipelineMode.CONVERT)
        errors = []

        stages = list(Stage)

        def worker(stage):
            try:
                pdb.set_stage("book1", stage, StageStatus.COMPLETED)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(s,)) for s in stages]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        data = pdb.read("book1")
        for stage in stages:
            assert data["stages"][stage.value]["status"] == "completed"
        pdb.close()
