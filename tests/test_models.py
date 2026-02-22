"""Tests for models.py -- enums, constants, stage ordering."""

from audiobook_pipeline.models import (
    AUDIO_EXTENSIONS,
    PRE_COMPLETED_STAGES,
    STAGE_ORDER,
    ErrorCategory,
    PipelineMode,
    Stage,
    StageStatus,
)


class TestPipelineMode:
    def test_values(self):
        assert PipelineMode.CONVERT == "convert"
        assert PipelineMode.ENRICH == "enrich"
        assert PipelineMode.METADATA == "metadata"
        assert PipelineMode.ORGANIZE == "organize"

    def test_from_string(self):
        assert PipelineMode("convert") is PipelineMode.CONVERT


class TestStage:
    def test_all_stages(self):
        assert len(Stage) == 8
        assert Stage.VALIDATE == "validate"
        assert Stage.CLEANUP == "cleanup"


class TestStageOrder:
    def test_all_modes_have_stage_order(self):
        for mode in PipelineMode:
            assert mode in STAGE_ORDER

    def test_convert_has_all_stages(self):
        assert len(STAGE_ORDER[PipelineMode.CONVERT]) == 8

    def test_enrich_skips_early_stages(self):
        stages = STAGE_ORDER[PipelineMode.ENRICH]
        assert Stage.VALIDATE not in stages
        assert Stage.CONCAT not in stages
        assert Stage.CONVERT not in stages
        assert Stage.ASIN in stages

    def test_organize_is_minimal(self):
        stages = STAGE_ORDER[PipelineMode.ORGANIZE]
        assert stages == [Stage.ORGANIZE, Stage.CLEANUP]


class TestPreCompletedStages:
    def test_convert_has_no_pre_completed(self):
        assert PipelineMode.CONVERT not in PRE_COMPLETED_STAGES

    def test_enrich_pre_completes_three(self):
        pre = PRE_COMPLETED_STAGES[PipelineMode.ENRICH]
        assert set(pre) == {Stage.VALIDATE, Stage.CONCAT, Stage.CONVERT}

    def test_organize_pre_completes_five(self):
        pre = PRE_COMPLETED_STAGES[PipelineMode.ORGANIZE]
        assert len(pre) == 5


class TestAudioExtensions:
    def test_common_formats(self):
        assert ".mp3" in AUDIO_EXTENSIONS
        assert ".flac" in AUDIO_EXTENSIONS
        assert ".m4a" in AUDIO_EXTENSIONS
        assert ".m4b" in AUDIO_EXTENSIONS

    def test_excludes_non_audio(self):
        assert ".txt" not in AUDIO_EXTENSIONS
        assert ".pdf" not in AUDIO_EXTENSIONS

    def test_is_frozenset(self):
        assert isinstance(AUDIO_EXTENSIONS, frozenset)


class TestErrorCategory:
    def test_values(self):
        assert ErrorCategory.TRANSIENT == "transient"
        assert ErrorCategory.PERMANENT == "permanent"


class TestStageStatus:
    def test_values(self):
        assert StageStatus.PENDING == "pending"
        assert StageStatus.RUNNING == "running"
        assert StageStatus.COMPLETED == "completed"
        assert StageStatus.FAILED == "failed"
