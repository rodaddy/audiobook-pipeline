"""Typed boundaries for optional AI-assisted catalogue resolution.

The model may choose one candidate already returned by the catalogue search,
but it cannot create metadata.  That keeps an AI choice tied to the ASIN that
later controls chapter lookup and M4B tagging.
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    field_validator,
    model_validator,
)

_MAX_EVIDENCE_TEXT = 240
_HTTP_URL = TypeAdapter(HttpUrl)


class AiResolverOptions(BaseModel):
    """Explicit settings for an OpenAI-compatible resolution endpoint.

    The caller maps its typed ``PIPELINE_LLM_*`` settings here.  This model
    deliberately reads no environment variables, which keeps service use
    deterministic in tests and prevents hidden provider configuration.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_url: str = ""
    api_key: SecretStr = SecretStr("")
    model: str = Field(default="haiku", min_length=1, max_length=120)
    all_books: bool = False
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    @property
    def is_configured(self) -> bool:
        """Whether a provider endpoint was explicitly supplied."""
        return bool(self.base_url)

    @field_validator("base_url")
    @classmethod
    def _http_endpoint(cls, value: str) -> str:
        """Accept only an explicit http(s) endpoint, retaining blank as disabled."""
        return str(_HTTP_URL.validate_python(value)) if value else ""


class AiCandidate(BaseModel):
    """One ranked catalogue result that the provider may select."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=120)
    asin: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=_MAX_EVIDENCE_TEXT)
    author: str = Field(min_length=1, max_length=_MAX_EVIDENCE_TEXT)
    series: str = Field(default="", max_length=_MAX_EVIDENCE_TEXT)
    series_position: str = Field(default="", max_length=40)


class AiEvidence(BaseModel):
    """Sanitized runtime evidence and the bounded candidate set for one book."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_filename: str = ""
    source_directory: str = ""
    path_author: str = ""
    path_title: str = ""
    path_series: str = ""
    path_position: str = ""
    tag_author: str = ""
    tag_title: str = ""
    tag_album: str = ""
    catalog_author: str = ""
    resolution_requested: bool = False
    candidates: tuple[AiCandidate, ...] = Field(default=(), max_length=5)

    @field_validator(
        "source_filename",
        "source_directory",
        "path_author",
        "path_title",
        "path_series",
        "path_position",
        "tag_author",
        "tag_title",
        "tag_album",
        "catalog_author",
    )
    @classmethod
    def _sanitize_evidence_text(cls, value: str) -> str:
        """Remove prompt-shaping newlines and bound untrusted source strings."""
        return value.replace("\n", " ").replace("\r", " ").strip()[:_MAX_EVIDENCE_TEXT]

    @model_validator(mode="after")
    def _unique_candidate_ids(self) -> AiEvidence:
        """Reject ambiguous provider identifiers before an HTTP request is made."""
        candidate_ids = {candidate.candidate_id for candidate in self.candidates}
        if len(candidate_ids) != len(self.candidates):
            raise DuplicateCandidateIdError
        return self


class AiProviderMessage(BaseModel):
    """The message portion of one OpenAI-compatible completion choice."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    content: str


class AiProviderChoice(BaseModel):
    """One OpenAI-compatible completion choice."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    message: AiProviderMessage


class AiProviderResponse(BaseModel):
    """Validated OpenAI-compatible completion envelope from the provider."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    choices: tuple[AiProviderChoice, ...] = Field(min_length=1)


class AiProviderDecision(BaseModel):
    """The only structured decision accepted from the model response body."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str | None = Field(default=None, max_length=120)


class AiDecision(BaseModel):
    """A provider selection validated against the exact runtime candidates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["selected", "abstained"]
    candidate: AiCandidate | None = None

    @model_validator(mode="after")
    def _matches_outcome(self) -> AiDecision:
        """Require selected and abstained outcomes to carry matching data."""
        if (self.outcome == "selected") != (self.candidate is not None):
            raise InvalidAiDecisionError
        return self

    @classmethod
    def selected(cls, candidate: AiCandidate) -> AiDecision:
        """Return an accepted choice while preserving its validated metadata."""
        return cls(outcome="selected", candidate=candidate)

    @classmethod
    def abstained(cls) -> AiDecision:
        """Return the deterministic non-AI fallback signal."""
        return cls(outcome="abstained")


class DuplicateCandidateIdError(ValueError):
    """The offered candidate identifiers cannot identify one unique result."""


class InvalidAiDecisionError(ValueError):
    """The decision outcome and selected candidate disagree."""
