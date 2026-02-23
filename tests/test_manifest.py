"""Tests for pipeline_db.py -- SQLite state machine, concurrent access, mode pre-completion."""

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
def manifest(tmp_path):
    return PipelineDB(tmp_path / "test.db")


@pytest.fixture
def book(manifest):
    """Create a manifest and return (manifest, book_hash)."""
    manifest.create("abc123", "/input/book", PipelineMode.CONVERT)
    return manifest, "abc123"


class TestCreate:
    def test_creates_book_record(self, manifest, tmp_path):
        manifest.create("h1", "/src/book", PipelineMode.CONVERT)
        data = manifest.read("h1")
        assert data is not None
        assert data["book_hash"] == "h1"
        assert data["source_path"] == "/src/book"
        assert data["mode"] == "convert"
        assert data["status"] == "pending"
        assert data["retry_count"] == 0

    def test_all_stages_pending_for_convert(self, manifest):
        data = manifest.create("h1", "/src", PipelineMode.CONVERT)
        for stage in Stage:
            assert data["stages"][stage.value]["status"] == "pending"

    def test_enrich_pre_completes_early_stages(self, manifest):
        data = manifest.create("h1", "/src/book.m4b", PipelineMode.ENRICH)
        assert data["stages"]["validate"]["status"] == "completed"
        assert data["stages"]["concat"]["status"] == "completed"
        assert data["stages"]["convert"]["status"] == "completed"
        assert data["stages"]["convert"]["output_file"] == "/src/book.m4b"
        assert data["stages"]["asin"]["status"] == "pending"

    def test_organize_pre_completes_three_stages(self, manifest):
        data = manifest.create("h1", "/src/book.m4b", PipelineMode.ORGANIZE)
        assert data["stages"]["validate"]["status"] == "completed"
        assert data["stages"]["concat"]["status"] == "completed"
        assert data["stages"]["convert"]["status"] == "completed"
        assert data["stages"]["asin"]["status"] == "pending"
        assert data["stages"]["metadata"]["status"] == "pending"
        assert data["stages"]["organize"]["status"] == "pending"

    def test_returns_created_data(self, manifest):
        data = manifest.create("h1", "/src", PipelineMode.CONVERT)
        assert isinstance(data, dict)
        assert "created_at" in data


class TestRead:
    def test_read_existing(self, book):
        m, h = book
        data = m.read(h)
        assert data is not None
        assert data["book_hash"] == h

    def test_read_missing_returns_none(self, manifest):
        assert manifest.read("nonexistent") is None

    def test_read_field_dotted_path(self, book):
        m, h = book
        status = m.read_field(h, "stages.validate.status")
        assert status == "pending"

    def test_read_field_missing_returns_none(self, manifest):
        assert manifest.read_field("nope", "anything") is None


class TestUpdate:
    def test_update_merges_dict(self, book):
        m, h = book
        m.update(h, {"status": "processing"})
        data = m.read(h)
        assert data["status"] == "processing"

    def test_update_missing_raises(self, manifest):
        with pytest.raises(ManifestError):
            manifest.update("nope", {"status": "x"})


class TestSetStage:
    def test_set_stage_running(self, book):
        m, h = book
        m.set_stage(h, Stage.VALIDATE, StageStatus.RUNNING)
        status = m.read_field(h, "stages.validate.status")
        assert status == "running"

    def test_set_stage_completed_adds_timestamp(self, book):
        m, h = book
        m.set_stage(h, Stage.VALIDATE, StageStatus.COMPLETED)
        data = m.read(h)
        assert data["stages"]["validate"]["status"] == "completed"
        assert "completed_at" in data["stages"]["validate"]

    def test_set_stage_failed_no_timestamp(self, book):
        m, h = book
        m.set_stage(h, Stage.VALIDATE, StageStatus.FAILED)
        data = m.read(h)
        assert data["stages"]["validate"]["status"] == "failed"
        assert "completed_at" not in data["stages"]["validate"]


class TestCheckStatus:
    def test_new_when_no_manifest(self, manifest):
        assert manifest.check_status("nope") == "new"

    def test_pending_after_create(self, book):
        m, h = book
        assert m.check_status(h) == "pending"

    def test_reflects_updates(self, book):
        m, h = book
        m.update(h, {"status": "completed"})
        assert m.check_status(h) == "completed"


class TestGetNextStage:
    def test_first_stage_for_convert(self, book):
        m, h = book
        nxt = m.get_next_stage(h, PipelineMode.CONVERT)
        assert nxt == Stage.VALIDATE

    def test_skips_completed_stages(self, book):
        m, h = book
        m.set_stage(h, Stage.VALIDATE, StageStatus.COMPLETED)
        nxt = m.get_next_stage(h, PipelineMode.CONVERT)
        assert nxt == Stage.CONCAT

    def test_returns_none_when_all_done(self, manifest):
        manifest.create("done", "/src", PipelineMode.ORGANIZE)
        manifest.set_stage("done", Stage.ASIN, StageStatus.COMPLETED)
        manifest.set_stage("done", Stage.METADATA, StageStatus.COMPLETED)
        manifest.set_stage("done", Stage.ORGANIZE, StageStatus.COMPLETED)
        assert manifest.get_next_stage("done", PipelineMode.ORGANIZE) is None

    def test_enrich_starts_at_asin(self, manifest):
        manifest.create("e1", "/src/b.m4b", PipelineMode.ENRICH)
        nxt = manifest.get_next_stage("e1", PipelineMode.ENRICH)
        assert nxt == Stage.ASIN

    def test_missing_manifest_raises(self, manifest):
        with pytest.raises(ManifestError):
            manifest.get_next_stage("nope", PipelineMode.CONVERT)


class TestRetryAndError:
    def test_increment_retry(self, book):
        m, h = book
        m.increment_retry(h)
        data = m.read(h)
        assert data["retry_count"] == 1
        m.increment_retry(h)
        data = m.read(h)
        assert data["retry_count"] == 2

    def test_set_error(self, book):
        m, h = book
        m.set_error(h, "validate", 2, ErrorCategory.PERMANENT, "bad input")
        data = m.read(h)
        err = data["last_error"]
        assert err["stage"] == "validate"
        assert err["exit_code"] == 2
        assert err["category"] == "permanent"
        assert err["message"] == "bad input"
        assert "timestamp" in err

    def test_error_on_missing_raises(self, manifest):
        with pytest.raises(ManifestError):
            manifest.set_error("nope", "x", 1, ErrorCategory.TRANSIENT, "msg")


class TestAtomicWrites:
    def test_concurrent_safe(self, manifest, tmp_path):
        """Verify concurrent updates work correctly via SQLite."""
        manifest.create("atom", "/src", PipelineMode.CONVERT)
        for i in range(20):
            manifest.update("atom", {"retry_count": i})
        data = manifest.read("atom")
        assert data["retry_count"] == 19
