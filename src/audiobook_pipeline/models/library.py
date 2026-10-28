"""Typed, read-only descriptions of library scans and audit findings."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class LibraryBook(BaseModel):
    """One logical book discovered from one or more audio files."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    author: str
    author_key: str
    title: str
    title_key: str
    path: Path
    multipart: bool = False


class LibraryDiff(BaseModel):
    """The source books present or absent from a finished target library."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    missing: tuple[LibraryBook, ...] = ()
    matched: tuple[LibraryBook, ...] = ()
    source_count: int = Field(ge=0)
    target_count: int = Field(ge=0)


class AuditFinding(BaseModel):
    """A read-only observation about one library path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check: str
    severity: str
    path: Path | None = None
    message: str
    fixable: bool = False
    fix_action: str = ""


class AuditReport(BaseModel):
    """Findings produced by selected read-only audit checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    library_root: Path
    total_files: int = Field(ge=0)
    findings: tuple[AuditFinding, ...] = ()

    def count(self, severity: str) -> int:
        """Count findings at one severity."""
        return sum(finding.severity == severity for finding in self.findings)
