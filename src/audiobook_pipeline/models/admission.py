"""Typed inputs and outcomes for batch admission before pipeline work starts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AdmissionOptions(BaseModel):
    """Caller-controlled batch admission policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    skip_lease: bool = False
    space_multiplier: int = Field(default=3, gt=0)


class DiskCapacity(BaseModel):
    """The usable capacity at an admission destination."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)


class DiskAdmission(BaseModel):
    """A redacted disk-capacity decision for one source."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_bytes: int = Field(ge=0)
    required_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    sufficient: bool
