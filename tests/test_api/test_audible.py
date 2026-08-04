"""Typed Audible catalogue boundary tests with mocked HTTP transport."""

from __future__ import annotations

from typing import Any

import httpx

from audiobook_pipeline.services.audible import search


def client_returning(
    payload: dict[str, Any],
    requests: list[httpx.Request],
    *,
    status: int = 200,
    timeout: float = 5.0,
) -> httpx.Client:
    """Return an HTTP client serving one catalogue payload and retaining requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=payload, request=request)

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)


def product(**overrides: Any) -> dict[str, Any]:
    """Build one minimally valid Audible product with selective overrides."""
    result: dict[str, Any] = {
        "asin": "B001ABC",
        "title": "The Great Book",
        "authors": [{"name": "John Smith"}],
    }
    result.update(overrides)
    return result


class TestSearch:
    """Audible search maps external JSON to validated book metadata."""

    def test_successful_search_returns_typed_metadata(self) -> None:
        payload = {
            "products": [
                product(
                    subtitle="A Subtitle",
                    authors=[{"name": "John Smith"}, {"name": "Jane Doe"}],
                    narrators=[{"name": "Narrator"}],
                    series=[{"title": "Great Series", "sequence": "1"}],
                    publisher_summary="<p>A great book.</p>",
                    publisher_name="Acme Publishing",
                    copyright="(c) 2024 John Smith",
                    category_ladders=[
                        {
                            "ladder": [
                                {"name": "Science Fiction"},
                                {"name": "Space Opera"},
                            ]
                        }
                    ],
                    product_images={"1024": "https://covers.test/large.jpg"},
                ),
                product(asin="B002DEF", title="Another Book", authors=[], series=None),
            ]
        }
        requests: list[httpx.Request] = []

        with client_returning(payload, requests) as client:
            results = search(client, "test query")

        assert [(book.asin, book.title) for book in results] == [
            ("B001ABC", "The Great Book"),
            ("B002DEF", "Another Book"),
        ]
        first, second = results
        assert first.author == "John Smith, Jane Doe" and first.narrator == "Narrator"
        assert first.series == "Great Series" and first.series_position == "1"
        assert first.summary == "A great book." and first.publisher == "Acme Publishing"
        assert first.genres == ("Science Fiction", "Space Opera")
        assert first.cover_url == "https://covers.test/large.jpg"
        assert second.series == second.series_position == second.summary == ""

    def test_uses_the_catalogue_endpoint_and_default_region(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning({"products": []}, requests) as client:
            search(client, "test query")

        assert str(requests[0].url).startswith(
            "https://api.audible.com/1.0/catalog/products"
        )

    def test_uses_the_requested_catalogue_region(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning({"products": []}, requests) as client:
            search(client, "test query", region="uk")

        assert str(requests[0].url).startswith(
            "https://api.audible.uk/1.0/catalog/products"
        )

    def test_sends_the_search_contract_parameters(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning({"products": []}, requests) as client:
            search(client, "fantasy books")

        params = requests[0].url.params
        assert params["keywords"] == "fantasy books" and params["num_results"] == "10"
        assert params["products_sort_by"] == "Relevance"
        assert set(params["response_groups"].split(",")) >= {
            "category_ladders",
            "contributors",
            "series",
            "product_extended_attrs",
            "rating",
            "product_details",
        }

    def test_http_error_returns_no_candidates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("network error", request=request)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            assert search(client, "test query") == []

    def test_http_status_error_returns_no_candidates(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning({"error": "not found"}, requests, status=404) as client:
            assert search(client, "test query") == []

    def test_empty_products_array_returns_no_candidates(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning({"products": []}, requests) as client:
            assert search(client, "nonexistent book") == []

    def test_missing_authors_map_to_an_empty_author(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {"products": [product(authors=None, series=None)]}, requests
        ) as client:
            results = search(client, "test")

        assert results[0].author == "" and results[0].series == ""

    def test_empty_series_maps_to_empty_series_fields(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning({"products": [product(series=[])]}, requests) as client:
            result = search(client, "test")[0]

        assert result.series == result.series_position == ""

    def test_product_without_a_required_title_is_dropped(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning({"products": [{}, product()]}, requests) as client:
            results = search(client, "test")

        assert [result.asin for result in results] == ["B001ABC"]

    def test_caller_owned_timeout_can_execute_the_search(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning({"products": []}, requests, timeout=30.0) as client:
            assert search(client, "test") == []


class TestGenreMapping:
    """Genre ladders remain catalogue metadata rather than an implementation detail."""

    def test_single_ladder_maps_all_names(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {
                "products": [
                    product(
                        category_ladders=[
                            {"ladder": [{"name": "Fiction"}, {"name": "Thriller"}]}
                        ]
                    )
                ]
            },
            requests,
        ) as client:
            result = search(client, "test")[0]

        assert result.genres == ("Fiction", "Thriller")

    def test_empty_ladders_map_to_no_genres(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {"products": [product(category_ladders=[])]}, requests
        ) as client:
            assert search(client, "test")[0].genres == ()

    def test_empty_ladder_steps_map_to_no_genres(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {"products": [product(category_ladders=[{"ladder": []}])]}, requests
        ) as client:
            assert search(client, "test")[0].genres == ()

    def test_single_category_maps_to_one_genre(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {
                "products": [
                    product(category_ladders=[{"ladder": [{"name": "Nonfiction"}]}])
                ]
            },
            requests,
        ) as client:
            assert search(client, "test")[0].genres == ("Nonfiction",)

    def test_multiple_ladders_preserve_unique_genres_in_order(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {
                "products": [
                    product(
                        category_ladders=[
                            {"ladder": [{"name": "Fiction"}]},
                            {"ladder": [{"name": "Science"}]},
                        ]
                    )
                ]
            },
            requests,
        ) as client:
            assert search(client, "test")[0].genres == ("Fiction", "Science")


class TestSummaryMapping:
    """HTML cleanup is observable through the typed catalogue response."""

    def test_strips_tags(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {"products": [product(publisher_summary="<p>Hello <b>world</b></p>")]},
            requests,
        ) as client:
            assert search(client, "test")[0].summary == "Hello world"

    def test_plain_text_is_unchanged(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {"products": [product(publisher_summary="No tags here")]}, requests
        ) as client:
            assert search(client, "test")[0].summary == "No tags here"

    def test_empty_summary_maps_to_empty_text(self) -> None:
        requests: list[httpx.Request] = []

        with client_returning(
            {"products": [product(publisher_summary="")]}, requests
        ) as client:
            assert search(client, "test")[0].summary == ""
