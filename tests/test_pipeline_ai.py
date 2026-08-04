"""Public lifecycle tests for optional AI candidate selection."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest

from audiobook_pipeline.config import Settings
from audiobook_pipeline.models.ai import AiResolverOptions
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import ChapterSet
from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.models.stage import PipelineLevel
from audiobook_pipeline.services import ai_selection, audible, concat, pipeline
from audiobook_pipeline.services.ai import AiResolver
from audiobook_pipeline.services.pipeline import RunContext


@pytest.fixture
def context() -> Iterator[RunContext]:
    """A closed-after-test context with an explicit non-AI baseline."""
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    conn = sqlite3.connect(":memory:")
    yield RunContext(
        config=Settings(level=PipelineLevel.NORMAL), conn=conn, client=client
    )
    client.close()
    conn.close()


@dataclass(frozen=True)
class AiSetup:
    """Settings and optional caller-owned resolver for one identification."""

    level: PipelineLevel
    resolver: AiResolver | None = None
    all_books: bool = False


def book(multi: bool = False) -> BookDirectory:
    """Return an in-memory-shaped source book with a stable title."""
    path = Path("/source/The Book")
    files: tuple[AudioFile, ...] = (
        AudioFile(path=path / "01.mp3", duration_ms=60_000),
    )
    if multi:
        files += (AudioFile(path=path / "02.mp3", duration_ms=60_000),)
    return BookDirectory(path=path, files=files)


def candidates(count: int = 6) -> list[BookMetadata]:
    """Return catalogue results whose first item deterministic matching selects."""
    return [
        BookMetadata(
            title="The Book" if index == 0 else f"Other Book {index}",
            author="Catalogue Author",
            asin=f"ASIN-{index}",
        )
        for index in range(count)
    ]


def resolver(selected: str | None, seen: list[dict[str, object]]) -> AiResolver:
    """Return a real resolver against an observable mock provider."""

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(json.loads(body["messages"][0]["content"]))
        response = {
            "choices": [
                {"message": {"content": json.dumps({"candidate_id": selected})}}
            ]
        }
        return httpx.Response(200, json=response, request=request)

    return AiResolver(
        AiResolverOptions(base_url="https://provider.test"),
        transport=httpx.MockTransport(respond),
    )


@dataclass(frozen=True)
class IdentifyInput:
    """The remaining inputs to one AI-aware identification call."""

    setup: AiSetup
    chapters: ChapterSet = field(
        default_factory=lambda: ChapterSet(source=concat.SOURCE_EMBEDDED)
    )
    payload: dict[str, object] = field(default_factory=dict)
    claim: ParsedPath = field(
        default_factory=lambda: ParsedPath(author="Path Author", title="The Book")
    )


def identify(context: RunContext, request: IdentifyInput) -> BookMetadata:
    """Run one identification request with a typed AI configuration."""
    config = context.config.model_copy(deep=True)
    config.ai.base_url = "https://provider.test"
    config.ai.all_books = request.setup.all_books
    config.level = request.setup.level
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=request.payload)
        )
    )
    active = context.model_copy(
        update={
            "config": config,
            "client": client,
            "ai_resolver": request.setup.resolver,
        }
    )
    try:
        metadata, _ = pipeline._identify(
            active,
            book(request.chapters.source != concat.SOURCE_EMBEDDED),
            request.chapters,
            request.claim,
        )
        return metadata
    finally:
        client.close()


@pytest.mark.parametrize("level", [PipelineLevel.SIMPLE, PipelineLevel.NORMAL])
def test_non_ai_levels_bypass_the_injected_resolver(
    context: RunContext, monkeypatch: pytest.MonkeyPatch, level: PipelineLevel
) -> None:
    found, selected = candidates(), resolver("ASIN-1", [])
    monkeypatch.setattr(audible, "search", lambda *_: found)
    monkeypatch.setattr(selected, "resolve", lambda *_: pytest.fail("resolver called"))

    assert identify(context, IdentifyInput(AiSetup(level, selected))) == found[0]
    selected.close()


@dataclass(frozen=True)
class SelectionCase:
    """One provider outcome and the catalogue identity it must leave behind."""

    level: PipelineLevel
    all_books: bool
    author: str
    selected: str | None
    expected: int


@pytest.mark.parametrize(
    "case",
    [
        SelectionCase(PipelineLevel.AI, False, "Path Author", "ASIN-2", 2),
        SelectionCase(PipelineLevel.FULL, True, "Catalogue Author", "ASIN-1", 1),
        SelectionCase(PipelineLevel.AI, False, "Path Author", "ASIN-5", 0),
        SelectionCase(PipelineLevel.AI, False, "Path Author", None, 0),
    ],
)
def test_ai_selection_is_bounded_and_falls_back_deterministically(
    context: RunContext, monkeypatch: pytest.MonkeyPatch, case: SelectionCase
) -> None:
    found = candidates()
    seen: list[dict[str, object]] = []
    selected = resolver(case.selected, seen)
    monkeypatch.setattr(audible, "search", lambda *_: found)

    metadata = identify(
        context,
        IdentifyInput(
            AiSetup(case.level, selected, case.all_books),
            claim=ParsedPath(author=case.author, title="The Book"),
        ),
    )

    assert metadata == found[case.expected]
    assert len(seen) == 1
    evidence = seen[0]["evidence"]
    assert isinstance(evidence, dict) and len(evidence["candidates"]) == 5
    selected.close()


def test_ai_selected_edition_still_falls_back_when_runtime_mismatches(
    context: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    found, selected = candidates(), resolver("ASIN-1", [])
    monkeypatch.setattr(audible, "search", lambda *_: found)

    metadata = identify(
        context,
        IdentifyInput(
            AiSetup(PipelineLevel.AI, selected),
            ChapterSet(source="file-boundary"),
            {"runtimeLengthMs": 3_600_000, "chapters": []},
        ),
    )

    assert metadata.asin == "" and metadata.author == "Path Author"
    selected.close()


def test_catalogue_candidate_evidence_flattens_and_caps_external_text() -> None:
    noisy = f"{'x' * 241}\nremaining"
    candidate = BookMetadata(
        title=noisy,
        author=noisy,
        asin="ASIN-noisy",
        series=noisy,
        series_position=noisy,
    )
    invalid_asin_candidates = [
        candidate.model_copy(update={"asin": "A" * 41}),
        candidate.model_copy(update={"asin": "ASIN\r\ninvalid"}),
    ]

    evidence = ai_selection.evidence_for(
        book(), ParsedPath(), candidate, [*invalid_asin_candidates, candidate]
    )
    offered = evidence.candidates[0]

    text_values = (
        offered.title,
        offered.author,
        offered.series,
        offered.series_position,
    )
    assert all("\n" not in value and "\r" not in value for value in text_values)
    assert len(offered.title) == len(offered.author) == len(offered.series) == 240
    assert len(offered.series_position) == 40
    assert len(evidence.candidates) == 1
    assert offered.asin == "ASIN-noisy"


def test_pipeline_closes_only_the_resolver_it_creates(
    context: RunContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    found, created = candidates(), []

    class CapturingResolver(AiResolver):
        def __init__(self, options: AiResolverOptions) -> None:
            super().__init__(
                options, transport=httpx.MockTransport(lambda _: httpx.Response(200))
            )
            created.append(self)

    monkeypatch.setattr(audible, "search", lambda *_: found)
    monkeypatch.setattr(ai_selection, "AiResolver", CapturingResolver)
    caller = resolver(None, [])

    identify(context, IdentifyInput(AiSetup(PipelineLevel.AI)))
    identify(context, IdentifyInput(AiSetup(PipelineLevel.AI, caller)))

    assert created[0]._client.is_closed and not caller._client.is_closed
    caller.close()
