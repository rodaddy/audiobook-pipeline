"""Tests for api/audible.py -- Audible catalog search with mocked HTTP."""

from unittest.mock import MagicMock, patch

import pytest

from audiobook_pipeline.api.audible import _extract_genre, _strip_html, search


class TestSearch:
    """Test Audible API search with mocked httpx calls."""

    @patch("audiobook_pipeline.api.audible.httpx.get")
    def test_successful_search_returns_results(self, mock_get):
        # Mock successful API response with expanded fields
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [
                {
                    "asin": "B001ABC",
                    "title": "The Great Book",
                    "subtitle": "A Subtitle",
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
                    "publisher_summary": "<p>A great book.</p>",
                    "publisher_name": "Acme Publishing",
                    "copyright": "(c) 2024 John Smith",
                    "language": "english",
                    "category_ladders": [
                        {
                            "ladder": [
                                {"name": "Science Fiction"},
                                {"name": "Space Opera"},
                            ],
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
        assert results[0]["subtitle"] == "A Subtitle"
        assert results[0]["authors"] == ["John Smith", "Jane Doe"]
        assert results[0]["author_str"] == "John Smith, Jane Doe"
        assert results[0]["series"] == "Great Series"
        assert results[0]["position"] == "1"
        assert results[0]["publisher_summary"] == "A great book."
        assert results[0]["publisher_name"] == "Acme Publishing"
        assert results[0]["copyright"] == "(c) 2024 John Smith"
        assert results[0]["language"] == "english"
        assert results[0]["genre"] == "Science Fiction/Space Opera"

        assert results[1]["asin"] == "B002DEF"
        assert results[1]["series"] == ""
        assert results[1]["position"] == ""
        assert results[1]["publisher_summary"] == ""
        assert results[1]["genre"] == ""

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
        rg = params["response_groups"]
        assert "contributors" in rg
        assert "series" in rg
        assert "category_ladders" in rg
        assert "product_extended_attrs" in rg
        assert "rating" in rg
        assert "product_details" in rg

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


class TestExtractGenre:
    """Test genre extraction from Audible category_ladders."""

    def test_single_ladder(self):
        ladders = [{"ladder": [{"name": "Fiction"}, {"name": "Thriller"}]}]
        assert _extract_genre(ladders) == "Fiction/Thriller"

    def test_empty_ladders(self):
        assert _extract_genre([]) == ""

    def test_empty_ladder_steps(self):
        assert _extract_genre([{"ladder": []}]) == ""

    def test_single_category(self):
        ladders = [{"ladder": [{"name": "Nonfiction"}]}]
        assert _extract_genre(ladders) == "Nonfiction"

    def test_uses_first_ladder_only(self):
        ladders = [
            {"ladder": [{"name": "Fiction"}]},
            {"ladder": [{"name": "Science"}]},
        ]
        assert _extract_genre(ladders) == "Fiction"


class TestStripHtml:
    """Test HTML tag stripping."""

    def test_strips_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_plain_text_unchanged(self):
        assert _strip_html("No tags here") == "No tags here"

    def test_empty_string(self):
        assert _strip_html("") == ""
