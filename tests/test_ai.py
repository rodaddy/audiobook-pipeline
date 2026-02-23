"""Tests for ai.py -- AI-assisted metadata resolution."""

from unittest.mock import MagicMock, Mock

import pytest

from audiobook_pipeline.ai import (
    _parse_resolve_response,
    disambiguate,
    get_client,
    needs_resolution,
    resolve,
)


class TestGetClient:
    """Test OpenAI client creation with various base URLs."""

    def test_returns_none_when_base_url_empty(self):
        client = get_client("", "dummy-key")
        assert client is None

    def test_strips_trailing_slash(self):
        client = get_client("http://localhost:4000/", "key")
        assert client is not None
        assert client.base_url == "http://localhost:4000"

    def test_strips_v1_suffix(self):
        client = get_client("http://localhost:4000/v1", "key")
        assert client is not None
        assert client.base_url == "http://localhost:4000"

    def test_strips_v1_with_trailing_slash(self):
        client = get_client("http://localhost:4000/v1/", "key")
        assert client is not None
        assert client.base_url == "http://localhost:4000"

    def test_uses_placeholder_when_api_key_missing(self):
        client = get_client("http://localhost:4000", "")
        assert client is not None
        assert client.api_key == "not-needed"

    def test_preserves_explicit_api_key(self):
        client = get_client("http://localhost:4000", "sk-real-key")
        assert client is not None
        assert client.api_key == "sk-real-key"


class TestNeedsResolution:
    """Test logic for when AI resolution should fire."""

    def test_conflict_multiple_authors(self):
        assert needs_resolution(
            {"author": "Stephen King"},
            {"author": "Dean Koontz"},
            None,
        )

    def test_conflict_path_vs_tag(self):
        assert needs_resolution(
            {"author": "Brandon Sanderson"},
            {"author": "Patrick Rothfuss"},
            {"author": "Joe Abercrombie"},
        )

    def test_all_empty(self):
        assert needs_resolution(
            {},
            {},
            None,
        )

    def test_all_empty_with_unknown_placeholders(self):
        assert needs_resolution(
            {"author": "Unknown"},
            {"author": "_unsorted"},
            {"author": "Various"},
        )

    def test_no_conflict_same_author(self):
        assert not needs_resolution(
            {"author": "Stephen King"},
            {"author": "Stephen King"},
            {"author": "Stephen King"},
        )

    def test_no_conflict_case_insensitive(self):
        assert not needs_resolution(
            {"author": "Stephen King"},
            {"author": "stephen king"},
            {"author": "STEPHEN KING"},
        )

    def test_no_conflict_single_source(self):
        assert not needs_resolution(
            {"author": "Stephen King"},
            {},
            None,
        )

    def test_ignores_whitespace_differences(self):
        assert not needs_resolution(
            {"author": " Stephen King "},
            {"author": "Stephen King"},
            None,
        )


class TestParseResolveResponse:
    """Test parsing of structured AI responses."""

    def test_complete_response(self):
        content = """AUTHOR: Stephen King
TITLE: The Stand
SERIES: The Stand
POSITION: 1"""
        result = _parse_resolve_response(content)
        assert result == {
            "author": "Stephen King",
            "title": "The Stand",
            "series": "The Stand",
            "position": "1",
        }

    def test_response_with_markdown_code_block(self):
        content = """```
AUTHOR: Stephen King
TITLE: The Stand
SERIES: NONE
POSITION: NONE
```"""
        result = _parse_resolve_response(content)
        assert result == {
            "author": "Stephen King",
            "title": "The Stand",
        }

    def test_response_with_extra_whitespace(self):
        content = """  AUTHOR:   Stephen King
    TITLE:   The Stand
SERIES:  NONE
POSITION: NONE  """
        result = _parse_resolve_response(content)
        assert result == {
            "author": "Stephen King",
            "title": "The Stand",
        }

    def test_response_with_quotes(self):
        content = """AUTHOR: "Stephen King"
TITLE: 'The Stand'
SERIES: NONE
POSITION: NONE"""
        result = _parse_resolve_response(content)
        assert result == {
            "author": "Stephen King",
            "title": "The Stand",
        }

    def test_series_none_excluded(self):
        content = """AUTHOR: Stephen King
TITLE: The Stand
SERIES: NONE
POSITION: NONE"""
        result = _parse_resolve_response(content)
        assert "series" not in result
        assert "position" not in result

    def test_series_n_a_excluded(self):
        content = """AUTHOR: Stephen King
TITLE: The Stand
SERIES: N/A
POSITION: N/A"""
        result = _parse_resolve_response(content)
        assert "series" not in result
        assert "position" not in result

    def test_unknown_values_excluded(self):
        content = """AUTHOR: Stephen King
TITLE: UNKNOWN
SERIES: UNKNOWN
POSITION: UNKNOWN"""
        result = _parse_resolve_response(content)
        assert result == {"author": "Stephen King"}

    def test_strips_audiobook_suffix(self):
        content = """AUTHOR: Stephen King
TITLE: The Stand (Audio Book)
SERIES: NONE
POSITION: NONE"""
        result = _parse_resolve_response(content)
        assert result["title"] == "The Stand"

    def test_strips_unabridged_suffix(self):
        content = """AUTHOR: Stephen King
TITLE: The Stand (Unabridged)
SERIES: NONE
POSITION: NONE"""
        result = _parse_resolve_response(content)
        assert result["title"] == "The Stand"

    def test_normalizes_position_leading_zeros(self):
        content = """AUTHOR: Stephen King
TITLE: The Stand
SERIES: The Stand
POSITION: 01"""
        result = _parse_resolve_response(content)
        assert result["position"] == "1"

    def test_case_insensitive_field_names(self):
        content = """author: Stephen King
title: The Stand
series: NONE
position: NONE"""
        result = _parse_resolve_response(content)
        assert result == {
            "author": "Stephen King",
            "title": "The Stand",
        }

    def test_empty_response(self):
        result = _parse_resolve_response("")
        assert result is None

    def test_malformed_response_no_author(self):
        content = """TITLE: The Stand
SERIES: NONE
POSITION: NONE"""
        result = _parse_resolve_response(content)
        assert result is None

    def test_malformed_response_missing_colons(self):
        content = """AUTHOR Stephen King
TITLE The Stand"""
        result = _parse_resolve_response(content)
        assert result is None

    def test_extra_text_ignored(self):
        content = """Here's my analysis:

AUTHOR: Stephen King
TITLE: The Stand
SERIES: NONE
POSITION: NONE

This is my recommendation."""
        result = _parse_resolve_response(content)
        assert result == {
            "author": "Stephen King",
            "title": "The Stand",
        }


class TestResolve:
    """Test full metadata resolution with mocked AI client."""

    def test_returns_none_when_client_none(self):
        result = resolve(
            {"author": "King"},
            {"author": "Koontz"},
            None,
            "haiku",
            None,
        )
        assert result is None

    def test_returns_none_when_no_evidence(self):
        mock_client = Mock()
        result = resolve({}, {}, None, "haiku", mock_client)
        assert result is None
        mock_client.chat.completions.create.assert_not_called()

    def test_builds_prompt_with_path_evidence(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(message=Mock(content="AUTHOR: Stephen King\nTITLE: The Stand\nSERIES: NONE\nPOSITION: NONE"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        result = resolve(
            {"author": "Stephen King", "title": "The Stand"},
            {},
            None,
            "haiku",
            mock_client,
            source_filename="the_stand.m4b",
        )

        assert result["author"] == "Stephen King"
        assert result["title"] == "The Stand"

        # Verify prompt structure
        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "the_stand.m4b" in prompt
        assert "File path suggests author: 'Stephen King'" in prompt
        assert "File path title: 'The Stand'" in prompt

    def test_builds_prompt_with_tag_evidence(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(message=Mock(content="AUTHOR: Stephen King\nTITLE: The Stand\nSERIES: NONE\nPOSITION: NONE"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        result = resolve(
            {},
            {"author": "Stephen King", "album": "The Stand", "title": "Chapter 1"},
            None,
            "haiku",
            mock_client,
        )

        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "Embedded tags artist: 'Stephen King'" in prompt
        assert "Tag album: 'The Stand'" in prompt
        assert "Tag title: 'Chapter 1'" in prompt

    def test_builds_prompt_with_audible_candidates(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(message=Mock(content="AUTHOR: Stephen King\nTITLE: The Stand\nSERIES: NONE\nPOSITION: NONE"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [
            {
                "title": "The Stand",
                "author_str": "Stephen King",
                "series": "The Stand",
                "position": "1",
                "score": 95,
            },
            {
                "title": "The Stand: Complete & Uncut",
                "author_str": "Stephen King",
                "score": 90,
            },
        ]

        result = resolve({}, {}, candidates, "haiku", mock_client)

        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "Audible search results:" in prompt
        assert '1. "The Stand" by Stephen King (Series: The Stand #1) [score: 95]' in prompt
        assert '2. "The Stand: Complete & Uncut" by Stephen King [score: 90]' in prompt

    def test_prompt_includes_uuid_nonce(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(message=Mock(content="AUTHOR: Stephen King\nTITLE: The Stand\nSERIES: NONE\nPOSITION: NONE"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        resolve(
            {"author": "Stephen King"},
            {},
            None,
            "haiku",
            mock_client,
            source_filename="test.m4b",
        )

        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        # Verify nonce pattern [8-char-hex] at start
        assert prompt.startswith("[")
        assert "] Resolve metadata for: 'test.m4b'" in prompt[:50]

    def test_sets_correct_api_params(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [
            Mock(message=Mock(content="AUTHOR: Stephen King\nTITLE: The Stand\nSERIES: NONE\nPOSITION: NONE"))
        ]
        mock_client.chat.completions.create.return_value = mock_response

        resolve(
            {"author": "Stephen King"},
            {},
            None,
            "claude-haiku-4-5",
            mock_client,
        )

        call_args = mock_client.chat.completions.create.call_args
        assert call_args.kwargs["model"] == "claude-haiku-4-5"
        assert call_args.kwargs["max_tokens"] == 150
        assert call_args.kwargs["temperature"] == 0.1
        assert call_args.kwargs["extra_headers"]["Cache-Control"] == "no-cache"

    def test_handles_api_exception(self):
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        result = resolve(
            {"author": "Stephen King"},
            {},
            None,
            "haiku",
            mock_client,
        )

        assert result is None


class TestDisambiguate:
    """Test AI-assisted Audible candidate selection."""

    def test_returns_none_when_client_none(self):
        candidates = [{"title": "The Stand", "author_str": "Stephen King", "asin": "B001"}]
        result = disambiguate(candidates, "The Stand", "Stephen King", "haiku", None)
        assert result is None

    def test_returns_none_when_no_candidates(self):
        mock_client = Mock()
        result = disambiguate([], "The Stand", "Stephen King", "haiku", mock_client)
        assert result is None

    def test_selects_first_candidate(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="1"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [
            {"title": "The Stand", "author_str": "Stephen King", "asin": "B001"},
            {"title": "The Stand: Complete", "author_str": "Stephen King", "asin": "B002"},
        ]

        result = disambiguate(candidates, "The Stand", "Stephen King", "haiku", mock_client)
        assert result == candidates[0]

    def test_selects_third_candidate(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="3"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [
            {"title": "Book 1", "author_str": "Author", "asin": "B001"},
            {"title": "Book 2", "author_str": "Author", "asin": "B002"},
            {"title": "Book 3", "author_str": "Author", "asin": "B003"},
        ]

        result = disambiguate(candidates, "Book 3", "Author", "haiku", mock_client)
        assert result == candidates[2]

    def test_returns_none_when_ai_picks_zero(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="0"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [{"title": "The Stand", "author_str": "Stephen King", "asin": "B001"}]
        result = disambiguate(candidates, "Different Book", "Author", "haiku", mock_client)
        assert result is None

    def test_extracts_number_from_text(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="The best match is option 2."))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [
            {"title": "Book 1", "author_str": "Author", "asin": "B001"},
            {"title": "Book 2", "author_str": "Author", "asin": "B002"},
        ]

        result = disambiguate(candidates, "Book 2", "Author", "haiku", mock_client)
        assert result == candidates[1]

    def test_limits_candidates_to_five(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="1"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [{"title": f"Book {i}", "author_str": "Author", "asin": f"B{i:03d}"} for i in range(10)]

        disambiguate(candidates, "Book 1", "Author", "haiku", mock_client)

        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        # Only first 5 should appear in prompt
        assert "Book 0" in prompt
        assert "Book 4" in prompt
        assert "Book 5" not in prompt

    def test_prompt_includes_uuid_nonce(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="1"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [{"title": "The Stand", "author_str": "Stephen King", "asin": "B001"}]

        disambiguate(candidates, "The Stand", "Stephen King", "haiku", mock_client)

        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        # Verify nonce pattern [8-char-hex] at start
        assert prompt.startswith("[")
        assert '] Find the best match for: "The Stand"' in prompt[:60]

    def test_includes_author_in_prompt_when_provided(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="1"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [{"title": "The Stand", "author_str": "Stephen King", "asin": "B001"}]

        disambiguate(candidates, "The Stand", "Stephen King", "haiku", mock_client)

        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert " by Stephen King" in prompt

    def test_omits_author_when_empty(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="1"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [{"title": "The Stand", "author_str": "Stephen King", "asin": "B001"}]

        disambiguate(candidates, "The Stand", "", "haiku", mock_client)

        call_args = mock_client.chat.completions.create.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert ' by ' not in prompt.split("Search results:")[0]

    def test_handles_api_exception(self):
        mock_client = Mock()
        mock_client.chat.completions.create.side_effect = Exception("API error")

        candidates = [{"title": "The Stand", "author_str": "Stephen King", "asin": "B001"}]
        result = disambiguate(candidates, "The Stand", "Stephen King", "haiku", mock_client)
        assert result is None

    def test_extracts_first_digit_from_multi_digit(self):
        # Regression test: "10" matches [0-5] and extracts "1"
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="10"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [
            {"title": "Book 1", "author_str": "Author", "asin": "B001"},
            {"title": "Book 2", "author_str": "Author", "asin": "B002"},
        ]
        result = disambiguate(candidates, "Book 1", "Author", "haiku", mock_client)
        # Regex [0-5] matches first "1" from "10", returns candidates[0]
        assert result == candidates[0]

    def test_returns_none_when_no_valid_digit(self):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.choices = [Mock(message=Mock(content="No match found"))]
        mock_client.chat.completions.create.return_value = mock_response

        candidates = [{"title": "Book 1", "author_str": "Author", "asin": "B001"}]
        result = disambiguate(candidates, "Book 1", "Author", "haiku", mock_client)
        assert result is None
