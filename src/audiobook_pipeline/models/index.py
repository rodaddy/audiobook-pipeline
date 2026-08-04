"""Typed identities and results for the durable audiobook library index."""

from __future__ import annotations

import os
import unicodedata
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IndexIdentityError(ValueError):
    """An index identity was structurally ambiguous or unusable."""


_ABSOLUTE_PATH_MESSAGE = "index paths must be absolute"
_SOURCE_MESSAGE = "source stem must contain non-whitespace text"
_FOLDER_MESSAGE = "folder name must be a non-empty single path component"
_FILENAME_MESSAGE = "filename must be a non-empty single path component"
_TIMEZONE_MESSAGE = "claimed_at must be timezone-aware"
_CLAIMED_RESERVATION_MESSAGE = "claimed state requires a reservation"
_NONCLAIMED_RESERVATION_MESSAGE = "non-claimed state forbids a reservation"


def normalize_path_key(path: Path) -> str:
    """Return a stable key for an absolute path without resolving symlinks."""
    if not path.is_absolute():
        raise IndexIdentityError(_ABSOLUTE_PATH_MESSAGE)
    return unicodedata.normalize("NFC", os.path.normpath(str(path)))


def normalize_text_key(value: str) -> str:
    """Return a case-insensitive, Unicode-normalized identity key."""
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


class IndexOptions(BaseModel):
    """Explicit policies governing an index coordinator instance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stale_reservation_after_seconds: int | None = Field(default=None, ge=1)
    busy_timeout_ms: int = Field(default=5_000, ge=1)


class SourceIdentity(BaseModel):
    """One source book identity used for cross-source batch deduplication."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stem: str = Field(min_length=1)

    @field_validator("stem")
    @classmethod
    def require_identity(cls, value: str) -> str:
        """Reject stems that normalize to no usable identity."""
        if not normalize_text_key(value):
            raise IndexIdentityError(_SOURCE_MESSAGE)
        return value

    @property
    def key(self) -> str:
        """The normalized source identity key."""
        return normalize_text_key(self.stem)


class FolderIdentity(BaseModel):
    """A visible folder and its normalized parent/name identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parent: Path
    name: str = Field(min_length=1)

    @field_validator("parent")
    @classmethod
    def parent_is_absolute(cls, value: Path) -> Path:
        """Require an absolute parent identity."""
        normalize_path_key(value)
        return value

    @field_validator("name")
    @classmethod
    def reject_separators(cls, value: str) -> str:
        """Reject names that would escape the represented parent."""
        if Path(value).name != value or not normalize_text_key(value):
            raise IndexIdentityError(_FOLDER_MESSAGE)
        return value

    @property
    def parent_key(self) -> str:
        """The stable identity of the containing directory."""
        return normalize_path_key(self.parent)

    @property
    def name_key(self) -> str:
        """The normalized sibling-comparison key."""
        return normalize_text_key(self.name)


class DestinationIdentity(BaseModel):
    """One candidate output file, named independently of source formatting."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directory: Path
    filename: str = Field(min_length=1)

    @field_validator("directory")
    @classmethod
    def directory_is_absolute(cls, value: Path) -> Path:
        """Require an absolute output directory identity."""
        normalize_path_key(value)
        return value

    @field_validator("filename")
    @classmethod
    def reject_filename_separators(cls, value: str) -> str:
        """Reject filenames that could make two destinations ambiguous."""
        if Path(value).name != value or not value.strip():
            raise IndexIdentityError(_FILENAME_MESSAGE)
        return value

    @property
    def directory_key(self) -> str:
        """The stable identity of the output directory."""
        return normalize_path_key(self.directory)

    @property
    def key(self) -> str:
        """The normalized identity used by the unique SQLite constraint."""
        return f"{self.directory_key}\x1f{normalize_text_key(self.filename)}"

    @property
    def path(self) -> Path:
        """The visible destination path for filesystem operations."""
        return self.directory / self.filename


class IndexedFile(BaseModel):
    """A destination whose finished file is known to the index."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: DestinationIdentity


class IndexCounts(BaseModel):
    """The current cardinality of durable folder and file index entries."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    folders: int = Field(ge=0)
    files: int = Field(ge=0)


class ClaimState(StrEnum):
    """The mutually exclusive result of asking to reserve a destination."""

    CLAIMED = "claimed"
    REGISTERED = "registered"
    RESERVED = "reserved"


class Reservation(BaseModel):
    """A coordinator-issued capability to commit or release one destination."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    destination: DestinationIdentity
    source: SourceIdentity
    claim_id: str = Field(min_length=1)
    claimed_at: datetime

    @field_validator("claimed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """Refuse ambiguous wall-clock timestamps in reservation decisions."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise IndexIdentityError(_TIMEZONE_MESSAGE)
        return value


class ReservationClaim(BaseModel):
    """The result of an atomic destination-reservation attempt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ClaimState
    reservation: Reservation | None = None

    @model_validator(mode="after")
    def require_matching_reservation_state(self) -> ReservationClaim:
        """Require a reservation only for a successful claim."""
        if self.state is ClaimState.CLAIMED and self.reservation is None:
            raise IndexIdentityError(_CLAIMED_RESERVATION_MESSAGE)
        if self.state is not ClaimState.CLAIMED and self.reservation is not None:
            raise IndexIdentityError(_NONCLAIMED_RESERVATION_MESSAGE)
        return self
