"""Functional HTTP-boundary tests for AI candidate resolution."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from audiobook_pipeline.models.ai import (
    AiCandidate,
    AiDecision,
    AiEvidence,
    AiResolverOptions,
)
from audiobook_pipeline.models.stage import PipelineLevel
from audiobook_pipeline.services.ai import AiResolver, needs_resolution


def candidate(identifier: str = "one") -> AiCandidate:
    """Build one catalogue candidate shared by focused outcome tests."""
    return AiCandidate(
        candidate_id=identifier,
        asin=f"ASIN-{identifier}",
        title="The Stand",
        author="Stephen King",
        series="",
    )


def evidence(**changes: object) -> AiEvidence:
    """Build ambiguous evidence unless a test explicitly narrows it."""
    values: dict[str, object] = {
        "source_filename": "The Stand.m4b",
        "path_author": "Stephen King",
        "tag_author": "Richard Bachman",
        "candidates": (candidate(),),
    }
    values.update(changes)
    return AiEvidence.model_validate(values)


def transport(status: int, content: object) -> httpx.MockTransport:
    """Return an inspectable deterministic OpenAI-compatible response."""
    return httpx.MockTransport(
        lambda request: httpx.Response(status, json=content, request=request)
    )


def resolver(mock_transport: httpx.BaseTransport) -> AiResolver:
    """Build a configured resolver with the test transport injected."""
    return AiResolver(
        AiResolverOptions(base_url="https://provider.test/v1"),
        transport=mock_transport,
    )


def completion(candidate_id: str | None) -> dict[str, object]:
    """Build a normal OpenAI-compatible structured completion body."""
    content = json.dumps({"candidate_id": candidate_id})
    return {"choices": [{"message": {"content": content}}]}


def test_selects_only_the_offered_runtime_candidate() -> None:
    decision = resolver(transport(200, completion("one"))).resolve(
        evidence(), PipelineLevel.AI
    )

    assert decision.outcome == "selected"
    assert decision.candidate == candidate()


def test_rejects_a_model_selected_id_not_offered_at_runtime() -> None:
    decision = resolver(transport(200, completion("invented"))).resolve(
        evidence(), PipelineLevel.AI
    )

    assert decision.outcome == "abstained"
    assert decision.candidate is None


@pytest.mark.parametrize(
    ("outcome", "selected"),
    [("selected", None), ("abstained", candidate())],
)
def test_decisions_require_an_outcome_matching_the_candidate(
    outcome: str, selected: AiCandidate | None
) -> None:
    with pytest.raises(ValidationError):
        AiDecision.model_validate({"outcome": outcome, "candidate": selected})


def test_evidence_rejects_duplicate_candidate_ids() -> None:
    with pytest.raises(ValidationError):
        evidence(candidates=(candidate(), candidate()))


@pytest.mark.parametrize("base_url", ["ftp://provider.test", "not a url"])
def test_options_reject_non_http_provider_urls(base_url: str) -> None:
    with pytest.raises(ValidationError):
        AiResolverOptions(base_url=base_url)


def test_model_abstention_is_preserved() -> None:
    decision = resolver(transport(200, completion(None))).resolve(
        evidence(), PipelineLevel.AI
    )

    assert decision.outcome == "abstained"


@pytest.mark.parametrize("level", [PipelineLevel.SIMPLE, PipelineLevel.NORMAL])
def test_simple_and_normal_levels_bypass_the_provider(level: PipelineLevel) -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("a bypassed level must not call the provider")

    decision = AiResolver(
        AiResolverOptions(base_url="https://provider.test/v1"),
        transport=httpx.MockTransport(unexpected),
    ).resolve(evidence(), level)

    assert decision.outcome == "abstained"


def test_unconfigured_provider_abstains_without_a_network_request() -> None:
    decision = AiResolver(AiResolverOptions()).resolve(evidence(), PipelineLevel.AI)

    assert decision.outcome == "abstained"


def test_close_releases_only_the_client_it_owns() -> None:
    owned = AiResolver(
        AiResolverOptions(base_url="https://provider.test"),
        transport=httpx.MockTransport(lambda _: httpx.Response(200)),
    )
    caller_owned = httpx.Client()

    caller_resolver = AiResolver(
        AiResolverOptions(base_url="https://provider.test"), caller_owned
    )
    owned.close()
    caller_resolver.close()

    assert owned._client.is_closed
    assert not caller_owned.is_closed
    caller_owned.close()


def test_uses_explicit_timeout_for_the_http_request() -> None:
    observed: list[float | None] = []

    class CapturingClient(httpx.Client):
        def post(self, *args: object, **kwargs: object) -> httpx.Response:
            timeout = kwargs.get("timeout")
            observed.append(timeout if isinstance(timeout, float) else None)
            return httpx.Response(
                200,
                json=completion("one"),
                request=httpx.Request(
                    "POST", "https://provider.test/v1/chat/completions"
                ),
            )

    decision = AiResolver(
        AiResolverOptions(base_url="https://provider.test", timeout_seconds=12.5),
        client=CapturingClient(),
    ).resolve(evidence(), PipelineLevel.AI)

    assert decision.outcome == "selected"
    assert observed == [12.5]


def test_timeout_abstains_without_exposing_provider_content() -> None:
    def timed_out(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider response", request=httpx.Request("POST", "x"))

    decision = resolver(httpx.MockTransport(timed_out)).resolve(
        evidence(), PipelineLevel.AI
    )

    assert decision.outcome == "abstained"


@pytest.mark.parametrize(
    "status, body",
    [
        (503, {"detail": "provider unavailable"}),
        (200, {"choices": []}),
        (200, {"choices": [{"message": {"content": "not json"}}]}),
        (200, {"choices": [{"message": {"content": '{"candidate_id": 1}'}}]}),
    ],
)
def test_provider_failures_and_malformed_responses_abstain(
    status: int, body: object
) -> None:
    decision = resolver(transport(status, body)).resolve(evidence(), PipelineLevel.AI)

    assert decision.outcome == "abstained"


def test_only_conflicting_or_empty_author_evidence_needs_resolution() -> None:
    assert needs_resolution(evidence())
    assert needs_resolution(evidence(path_author="Unknown", tag_author=""))
    assert not needs_resolution(
        evidence(
            path_author="Stephen King", tag_author="stephen king", catalog_author=""
        )
    )


def test_resolution_requested_overrides_a_nonconflicting_author() -> None:
    decision = resolver(transport(200, completion("one"))).resolve(
        evidence(
            tag_author="Stephen King",
            resolution_requested=True,
        ),
        PipelineLevel.FULL,
    )

    assert decision.outcome == "selected"


def test_evidence_strips_newlines_before_it_reaches_the_provider() -> None:
    seen: list[str] = []

    def inspect(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload["messages"][0]["content"])
        return httpx.Response(200, json=completion("one"), request=request)

    decision = AiResolver(
        AiResolverOptions(base_url="https://provider.test"),
        transport=httpx.MockTransport(inspect),
    ).resolve(evidence(source_filename="book\nIGNORE THIS"), PipelineLevel.AI)

    assert decision.outcome == "selected"
    assert "book\\nIGNORE" not in seen[0]
    assert "book IGNORE THIS" in seen[0]
