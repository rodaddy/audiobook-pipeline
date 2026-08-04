"""Public pipeline failures and the stable retry classification contract."""

from __future__ import annotations

from audiobook_pipeline.models.stage import ErrorCategory

_EXIT_CATEGORIES: dict[int, ErrorCategory] = {
    2: ErrorCategory.PERMANENT,
    3: ErrorCategory.PERMANENT,
}


class PipelineError(Exception):
    """Base error carrying an operator-safe description of pipeline failure."""

    def __init__(self, message: str) -> None:
        """Create an error with its stable public message attribute."""
        super().__init__(message)
        self.message = message


class ConfigError(PipelineError):
    """Invalid or missing pipeline configuration."""


class ManifestError(PipelineError):
    """Pipeline state storage or state-machine failure."""


class StageError(PipelineError):
    """A named pipeline stage failed with a classified process result."""

    def __init__(
        self, message: str, stage: str, exit_code: int, category: ErrorCategory
    ) -> None:
        """Create a stage failure with stable retry metadata."""
        super().__init__(message)
        self.stage = stage
        self.exit_code = exit_code
        self.category = category


class ExternalToolError(PipelineError):
    """An external subprocess failed with operator-safe diagnostic detail."""

    def __init__(self, tool: str, exit_code: int, stderr: str) -> None:
        """Create a subprocess failure without retaining raw control characters."""
        self.tool = tool
        self.exit_code = exit_code
        self.stderr = _sanitize_stderr(stderr)
        super().__init__(f"{tool} exited with code {exit_code}: {self.stderr}")


def categorize_exit_code(code: int) -> ErrorCategory:
    """Classify only exit codes 2 and 3 as permanent; every other int retries.

    Zero, negative, and unknown values remain transient for legacy compatibility:
    callers use this function only for a failure record, and must not mistake an
    unmapped process status for proof that retrying is unsafe.
    """
    return _EXIT_CATEGORIES.get(code, ErrorCategory.TRANSIENT)


def _sanitize_stderr(stderr: str) -> str:
    """Collapse untrusted subprocess detail into one safe display line."""
    return " ".join(stderr.split())
