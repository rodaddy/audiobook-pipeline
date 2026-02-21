"""Tests for errors.py -- exception hierarchy and exit code categorization."""

import pytest

from audiobook_pipeline.errors import (
    ConfigError,
    ExternalToolError,
    ManifestError,
    PipelineError,
    StageError,
    categorize_exit_code,
)
from audiobook_pipeline.models import ErrorCategory


class TestExceptionHierarchy:
    def test_all_inherit_from_pipeline_error(self):
        assert issubclass(ConfigError, PipelineError)
        assert issubclass(ManifestError, PipelineError)
        assert issubclass(StageError, PipelineError)
        assert issubclass(ExternalToolError, PipelineError)

    def test_pipeline_error_is_exception(self):
        assert issubclass(PipelineError, Exception)


class TestStageError:
    def test_attributes(self):
        err = StageError(
            "validation failed",
            stage="validate",
            exit_code=2,
            category=ErrorCategory.PERMANENT,
        )
        assert err.stage == "validate"
        assert err.exit_code == 2
        assert err.category == ErrorCategory.PERMANENT
        assert "validation failed" in str(err)


class TestExternalToolError:
    def test_attributes(self):
        err = ExternalToolError(tool="ffmpeg", exit_code=1, stderr="codec error")
        assert err.tool == "ffmpeg"
        assert err.exit_code == 1
        assert err.stderr == "codec error"
        assert "ffmpeg" in str(err)
        assert "codec error" in str(err)


class TestCategorizeExitCode:
    @pytest.mark.parametrize("code", [2, 3])
    def test_permanent_codes(self, code: int):
        assert categorize_exit_code(code) == ErrorCategory.PERMANENT

    @pytest.mark.parametrize("code", [1, 4, 127, 255])
    def test_transient_codes(self, code: int):
        assert categorize_exit_code(code) == ErrorCategory.TRANSIENT
