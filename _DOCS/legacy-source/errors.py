"""Exception hierarchy and error categorization for the audiobook pipeline."""

from .models import ErrorCategory


class PipelineError(Exception):
    """Base exception for all pipeline errors."""


class ConfigError(PipelineError):
    """Invalid or missing configuration."""


class ManifestError(PipelineError):
    """Manifest read/write or state machine error."""


class StageError(PipelineError):
    """A pipeline stage failed."""

    def __init__(
        self,
        message: str,
        stage: str,
        exit_code: int,
        category: ErrorCategory,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.exit_code = exit_code
        self.category = category


class ExternalToolError(PipelineError):
    """An external subprocess (ffmpeg, ffprobe, etc.) failed."""

    def __init__(self, tool: str, exit_code: int, stderr: str) -> None:
        super().__init__(f"{tool} exited with code {exit_code}: {stderr}")
        self.tool = tool
        self.exit_code = exit_code
        self.stderr = stderr


def categorize_exit_code(code: int) -> ErrorCategory:
    """Map a process exit code to an error category.

    Exit codes 2 and 3 indicate permanent failures (bad input, missing file).
    All other non-zero codes are treated as transient (retriable).
    """
    if code in (2, 3):
        return ErrorCategory.PERMANENT
    return ErrorCategory.TRANSIENT
