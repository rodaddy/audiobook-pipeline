"""Public behavior tests for pipeline errors and retry categorization."""

from __future__ import annotations

import pytest

from audiobook_pipeline.errors import (
    ConfigError,
    ExternalToolError,
    ManifestError,
    PipelineError,
    StageError,
    categorize_exit_code,
)
from audiobook_pipeline.models.stage import ErrorCategory


class TestExceptionHierarchy:
    def test_all_inherit_from_pipeline_error(self) -> None:
        assert issubclass(ConfigError, PipelineError)
        assert issubclass(ManifestError, PipelineError)
        assert issubclass(StageError, PipelineError)
        assert issubclass(ExternalToolError, PipelineError)

    def test_pipeline_error_is_exception(self) -> None:
        assert issubclass(PipelineError, Exception)

    @pytest.mark.parametrize("error_type", (ConfigError, ManifestError))
    def test_message_is_a_stable_public_attribute(
        self, error_type: type[PipelineError]
    ) -> None:
        error = error_type("state is unavailable")
        assert error.message == "state is unavailable"
        assert str(error) == "state is unavailable"


class TestStageError:
    def test_attributes(self) -> None:
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
    def test_attributes(self) -> None:
        err = ExternalToolError(tool="ffmpeg", exit_code=1, stderr="codec error")
        assert err.tool == "ffmpeg"
        assert err.exit_code == 1
        assert err.stderr == "codec error"
        assert "ffmpeg" in str(err)
        assert "codec error" in str(err)

    def test_stderr_detail_is_one_operator_safe_line(self) -> None:
        err = ExternalToolError(tool="ffmpeg", exit_code=1, stderr="codec\nerror\t")
        assert err.stderr == "codec error"
        assert str(err) == "ffmpeg exited with code 1: codec error"


class TestCategorizeExitCode:
    @pytest.mark.parametrize("code", [2, 3])
    def test_permanent_codes(self, code: int) -> None:
        assert categorize_exit_code(code) == ErrorCategory.PERMANENT

    @pytest.mark.parametrize("code", [-1, 0, 1, 4, 127, 255])
    def test_all_other_codes_are_transient(self, code: int) -> None:
        assert categorize_exit_code(code) == ErrorCategory.TRANSIENT
