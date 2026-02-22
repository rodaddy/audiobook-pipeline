"""Tests for api/audible.py -- Audible catalog search with mocked HTTP."""

from unittest.mock import MagicMock, patch

import pytest

from audiobook_pipeline.api.audible import search


class TestSearch:
    """Test Audible API search with mocked httpx calls."""

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_successful_search_returns_results(self, mock_get):
        # Mock successful API response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [
                {
                    "asin": "B001ABC",
                    "title": "The Great Book",
                    "authors": [
                        {"name": "John Smith"},
                        {"name": "Jane Doe"},
                    ],
                    "series": [
                        {
                            "title": "Great Series",
                            "sequence": "1",
                        },
                    ],
                },
                {
                    "asin": "B002DEF",
                    "title": "Another Book",
                    "authors": [{"name": "Bob Jones"}],
                    "series": None,
                },
            ],
        }
        mock_get.return_value = mock_response

        results = search("test query")

        assert len(results) == 2
        assert results[0]["asin"] == "B001ABC"
        assert results[0]["title"] == "The Great Book"
        assert results[0]["authors"] == ["John Smith", "Jane Doe"]
        assert results[0]["author_str"] == "John Smith, Jane Doe"
        assert results[0]["series"] == "Great Series"
        assert results[0]["position"] == "1"

        assert results[1]["asin"] == "B002DEF"
        assert results[1]["series"] == ""
        assert results[1]["position"] == ""

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_uses_correct_api_endpoint(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"products": []}
        mock_get.return_value = mock_response

        search("test query", region="com")

        # Verify the correct API URL was called
        call_args = mock_get.call_args
        assert "api.audible.com/1.0/catalog/products" in call_args[0][0]

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_uses_custom_region(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"products": []}
        mock_get.return_value = mock_response

        search("test query", region="uk")

        call_args = mock_get.call_args
        assert "api.audible.uk/1.0/catalog/products" in call_args[0][0]

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_includes_correct_query_params(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"products": []}
        mock_get.return_value = mock_response

        search("fantasy books")

        call_kwargs = mock_get.call_args[1]
        params = call_kwargs["params"]
        assert params["keywords"] == "fantasy books"
        assert params["num_results"] == "10"
        assert params["products_sort_by"] == "Relevance"
        assert "contributors" in params["response_groups"]
        assert "series" in params["response_groups"]

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_http_error_returns_empty_list(self, mock_get):
        # Simulate HTTP error
        import httpx
        mock_get.side_effect = httpx.HTTPError("Network error")

        results = search("test query")

        assert results == []

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_http_status_error_returns_empty_list(self, mock_get):
        # Simulate 404 or other HTTP status error
        import httpx
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )
        mock_get.return_value = mock_response

        results = search("test query")

        assert results == []

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_empty_products_array(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"products": []}
        mock_get.return_value = mock_response

        results = search("nonexistent book")

        assert results == []

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_missing_authors_handled_gracefully(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [
                {
                    "asin": "B001",
                    "title": "Book Without Authors",
                    "authors": None,
                    "series": None,
                },
            ],
        }
        mock_get.return_value = mock_response

        results = search("test")

        assert len(results) == 1
        assert results[0]["authors"] == []
        assert results[0]["author_str"] == ""

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_empty_series_array_handled(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [
                {
                    "asin": "B001",
                    "title": "Standalone Book",
                    "authors": [{"name": "Author"}],
                    "series": [],
                },
            ],
        }
        mock_get.return_value = mock_response

        results = search("test")

        assert results[0]["series"] == ""
        assert results[0]["position"] == ""

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_missing_fields_use_empty_defaults(self, mock_get):
        # Test defensive parsing when fields are missing entirely
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [
                {
                    # Minimal product, missing optional fields
                },
            ],
        }
        mock_get.return_value = mock_response

        results = search("test")

        assert len(results) == 1
        assert results[0]["asin"] == ""
        assert results[0]["title"] == ""
        assert results[0]["authors"] == []
        assert results[0]["series"] == ""
        assert results[0]["position"] == ""

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_timeout_set_correctly(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"products": []}
        mock_get.return_value = mock_response

        search("test")

        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["timeout"] == 30.0
