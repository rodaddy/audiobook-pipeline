"""Resolve ambiguous audiobook identities through an injected HTTP provider.

This service is deliberately isolated from pipeline orchestration.  Its only
successful outcome is one of the candidates supplied at call time; malformed
responses, provider failures, and disabled levels all return an abstention.
"""

from __future__ import annotations

import json
from typing import Literal

import httpx
from loguru import logger
from pydantic import ValidationError

from audiobook_pipeline.models.ai import (
    AiCandidate,
    AiDecision,
    AiEvidence,
    AiProviderDecision,
    AiProviderResponse,
    AiResolverOptions,
)
from audiobook_pipeline.models.stage import PipelineLevel

log = logger.bind(stage="ai")

_UNKNOWN_AUTHORS = frozenset({"unknown", "_unsorted", "various"})
_AI_LEVELS = frozenset({PipelineLevel.AI, PipelineLevel.FULL})


class ProviderResponseError(ValueError):
    """The provider returned a structurally unusable completion body."""


def needs_resolution(evidence: AiEvidence) -> bool:
    """Whether metadata sources conflict or provide no usable author."""
    authors = {
        author.casefold()
        for author in (
            evidence.path_author,
            evidence.tag_author,
            evidence.catalog_author,
        )
        if author and author.casefold() not in _UNKNOWN_AUTHORS
    }
    return len(authors) != 1


class AiResolver:
    """An optional, synchronous OpenAI-compatible candidate selector.

    Args:
        options: Explicit provider settings, normally mapped from ``AiSettings``.
        client: An injected client for tests or a caller-owned connection pool.
        transport: A deterministic transport used only when this service builds
            its own client.  It makes HTTP outcome tests independent of a server.
    """

    def __init__(
        self,
        options: AiResolverOptions,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Store explicit provider settings and one caller-controlled HTTP boundary."""
        if client is not None and transport is not None:
            msg = "provide either an HTTP client or a transport, not both"
            raise ValueError(msg)
        self._options = options
        self._client = client or httpx.Client(transport=transport)
        self._owns_client = client is None

    def close(self) -> None:
        """Close the owned HTTP client; caller-owned clients remain open."""
        if self._owns_client:
            self._client.close()

    def resolve(self, evidence: AiEvidence, level: PipelineLevel) -> AiDecision:
        """Return a validated selected candidate or a safe abstention."""
        if not self._should_query(evidence, level):
            return AiDecision.abstained()
        return self._request_selection(evidence)

    def _should_query(self, evidence: AiEvidence, level: PipelineLevel) -> bool:
        """Apply level, endpoint, candidate, and ambiguity gates before HTTP."""
        return bool(
            level in _AI_LEVELS
            and self._options.is_configured
            and evidence.candidates
            and (
                self._options.all_books
                or evidence.resolution_requested
                or needs_resolution(evidence)
            )
        )

    def _request_selection(self, evidence: AiEvidence) -> AiDecision:
        """Call the provider and degrade every non-success to abstention."""
        try:
            response = self._client.post(
                self._endpoint(),
                headers=self._headers(),
                json=self._request_body(evidence),
                timeout=self._options.timeout_seconds,
            )
            response.raise_for_status()
            return self._validate_response(response, evidence.candidates)
        except httpx.TimeoutException:
            log.warning("ai_resolution_abstained", reason="timeout")
            return AiDecision.abstained()
        except httpx.HTTPError:
            log.warning("ai_resolution_abstained", reason="provider_error")
            return AiDecision.abstained()
        except (json.JSONDecodeError, ProviderResponseError, ValidationError):
            log.warning("ai_resolution_abstained", reason="malformed_response")
            return AiDecision.abstained()

    def _endpoint(self) -> str:
        """Return the OpenAI-compatible chat-completions endpoint."""
        base_url = self._options.base_url
        if not base_url:
            raise ProviderResponseError
        return f"{base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        """Build request headers without exposing the credential to logging."""
        token = self._options.api_key.get_secret_value()
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _request_body(self, evidence: AiEvidence) -> dict[str, object]:
        """Build a small structured-selection request from sanitized evidence."""
        prompt = {
            "task": "Select exactly one offered candidate, or null when uncertain.",
            "output": {"candidate_id": "offered id or null"},
            "evidence": evidence.model_dump(mode="json"),
        }
        return {
            "model": self._options.model,
            "temperature": 0,
            "max_tokens": 40,
            "messages": [{"role": "user", "content": json.dumps(prompt)}],
        }

    @staticmethod
    def _validate_response(
        response: httpx.Response, candidates: tuple[AiCandidate, ...]
    ) -> AiDecision:
        """Accept only a selected ID present in this call's candidate set."""
        provider = AiProviderResponse.model_validate(response.json())
        content = provider.choices[0].message.content
        decision = AiProviderDecision.model_validate_json(content)
        if decision.candidate_id is None:
            return AiDecision.abstained()
        candidate = next(
            (item for item in candidates if item.candidate_id == decision.candidate_id),
            None,
        )
        return (
            AiDecision.selected(candidate)
            if candidate
            else _abstain("unoffered_candidate")
        )


def _abstain(
    reason: Literal[
        "malformed_response", "provider_error", "timeout", "unoffered_candidate"
    ],
) -> AiDecision:
    """Log a safe, machine-stable degradation event and return abstention."""
    log.bind(event="ai_resolution_abstained", reason=reason).warning(
        "ai_resolution_abstained"
    )
    return AiDecision.abstained()
