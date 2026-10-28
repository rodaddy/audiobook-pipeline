"""Current pipeline identification and fallback tests replacing the ASIN stage."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from audiobook_pipeline.config import Settings
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import ChapterSet
from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.models.stage import PipelineLevel, PipelineMode, StageStatus
from audiobook_pipeline.services import ai_selection, audible, concat, pipeline
from audiobook_pipeline.services.pipeline import RunContext


@pytest.fixture
def context() -> Iterator[RunContext]:
    """Create a network-isolated current pipeline context."""
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    conn = sqlite3.connect(":memory:")
    yield RunContext(config=Settings(), conn=conn, client=client)
    client.close()
    conn.close()


def book() -> BookDirectory:
    """Return one in-memory-shaped source book for identification tests."""
    path = Path("/source/Book")
    return BookDirectory(
        path=path,
        files=(AudioFile(path=path / "Book.m4b", duration_ms=60_000),),
    )


def identify(context: RunContext, claim: ParsedPath) -> tuple[BookMetadata, ChapterSet]:
    """Run the current identification boundary without file or database stages."""
    return pipeline._identify(
        context, book(), ChapterSet(source=concat.SOURCE_EMBEDDED), claim
    )


def candidate(
    title: str, author: str, asin: str, *, series: str = "", position: str = ""
) -> BookMetadata:
    """Build one typed Audible candidate."""
    return BookMetadata(
        title=title,
        author=author,
        asin=asin,
        series=series,
        series_position=position,
    )


class TestCatalogueSearch:
    """Current single-query identification and candidate-selection semantics."""

    def test_basic_title_search_selects_the_catalogue_metadata(
        self, context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        found = candidate("Test Book", "Test Author", "B001")
        queries: list[str] = []

        def search(_: httpx.Client, query: str) -> list[BookMetadata]:
            queries.append(query)
            return [found]

        monkeypatch.setattr(audible, "search", search)

        metadata, _ = identify(context, ParsedPath(title="Test Book"))

        assert metadata == found and queries == ["Test Book"]

    def test_path_series_is_preserved_when_the_catalogue_has_no_match(
        self, context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(audible, "search", lambda *_: [])

        metadata, _ = identify(
            context,
            ParsedPath(
                title="Book Title", author="Author", series="Series", position="2"
            ),
        )

        assert metadata == BookMetadata(
            title="Book Title", author="Author", series="Series", series_position="2"
        )

    def test_author_is_included_in_the_current_single_catalogue_query(
        self, context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        queries: list[str] = []

        def search(_: httpx.Client, query: str) -> list[BookMetadata]:
            queries.append(query)
            return []

        monkeypatch.setattr(audible, "search", search)

        identify(context, ParsedPath(title="Book", author="Author", series="Series"))

        assert queries == ["Book Author"]

    def test_ai_evidence_deduplicates_catalogue_asins(self) -> None:
        duplicate = candidate("Book", "Author", "B001")
        evidence = ai_selection.evidence_for(
            book(), ParsedPath(title="Book"), duplicate, [duplicate, duplicate]
        )

        assert [offered.asin for offered in evidence.candidates] == ["B001"]

    def test_catalogue_http_failure_falls_back_to_the_path_claim(
        self, context: RunContext
    ) -> None:
        metadata, _ = identify(context, ParsedPath(title="Book", author="Author"))

        assert metadata == BookMetadata(title="Book", author="Author")


class TestAsinResolution:
    """Catalogue adoption, AI selection, and current dry-run semantics."""

    def test_catalogue_match_carries_its_asin_and_series(
        self, context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        found = candidate(
            "Great Book", "John Smith", "B001ABC", series="A Series", position="1"
        )
        monkeypatch.setattr(audible, "search", lambda *_: [found])

        metadata, _ = identify(
            context, ParsedPath(title="Great Book", author="John Smith")
        )

        assert metadata == found

    def test_no_catalogue_result_keeps_the_path_author_without_an_asin(
        self, context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(audible, "search", lambda *_: [])

        metadata, _ = identify(
            context, ParsedPath(title="Tag Title", author="Tag Author")
        )

        assert metadata.author == "Tag Author" and metadata.asin == ""

    def test_ai_selection_replaces_the_initial_candidate_on_a_path_conflict(
        self, context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        initial = candidate("Book A", "Author A", "B001")
        selected = candidate("Book B", "Author B", "B002")
        context.config.ai.base_url = "https://provider.test"
        context.config.level = PipelineLevel.AI
        monkeypatch.setattr(audible, "search", lambda *_: [initial, selected])
        monkeypatch.setattr(ai_selection, "resolver_for", lambda *_: (None, False))
        monkeypatch.setattr(ai_selection, "selected_match", lambda *_: selected)

        metadata, _ = identify(context, ParsedPath(title="Book", author="Path Author"))

        assert metadata == selected

    def test_dry_run_skips_identification_before_any_catalogue_request(
        self, context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        context.config.dry_run = True
        monkeypatch.setattr(audible, "search", lambda *_: pytest.fail("search called"))

        row = pipeline.process_book(book(), context, mode=PipelineMode.CONVERT)

        assert row.status == StageStatus.SKIPPED.value

    def test_identification_requires_no_persisted_manifest_state(
        self, context: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(audible, "search", lambda *_: [])

        metadata, _ = identify(
            context, ParsedPath(title="From Source", author="Convert Author")
        )

        assert metadata == BookMetadata(title="From Source", author="Convert Author")
