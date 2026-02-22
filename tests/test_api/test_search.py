"""Tests for api/search.py -- fuzzy scoring and path parsing."""

import pytest

from audiobook_pipeline.api.search import (
    _strip_series_numbers,
    parse_source_path,
    score_results,
)


class TestScoreResults:
    """Test fuzzy scoring of Audible search results."""

    def test_exact_match_high_score(self):
        results = [
            {
                "asin": "B001",
                "title": "The Great Book",
                "authors": ["John Smith"],
            },
        ]
        scored = score_results(results, "The Great Book", "John Smith")
        assert len(scored) == 1
        # Exact match: title 60 + author 30 + position 10 = 100
        assert 95 <= scored[0]["score"] <= 100

    def test_title_only_matching(self):
        results = [
            {
                "asin": "B001",
                "title": "Project Hail Mary",
                "authors": ["Andy Weir"],
            },
        ]
        scored = score_results(results, "Project Hail Mary", "")
        assert len(scored) == 1
        # Exact title, no author: title 60 + author 0 + position 10 = 70
        assert 65 <= scored[0]["score"] <= 70

    def test_position_bonus_favors_first_result(self):
        results = [
            {
                "asin": "B001",
                "title": "Good Match",
                "authors": ["Author One"],
            },
            {
                "asin": "B002",
                "title": "Good Match",
                "authors": ["Author Two"],
            },
        ]
        scored = score_results(results, "Good Match", "")
        # First result gets 10pt bonus, second gets 8pt bonus
        assert scored[0]["score"] > scored[1]["score"]

    def test_author_partial_match(self):
        results = [
            {
                "asin": "B001",
                "title": "Book Title",
                "authors": ["J.K. Rowling"],
            },
        ]
        scored = score_results(results, "Book Title", "Rowling")
        # Exact title (60) + partial_ratio author match (~100) * 0.3 = 30 + position 10 = 100
        assert 95 <= scored[0]["score"] <= 100

    def test_multiple_authors_picks_best_match(self):
        results = [
            {
                "asin": "B001",
                "title": "Collaboration",
                "authors": ["Alice Smith", "Bob Jones", "Carol White"],
            },
        ]
        scored = score_results(results, "Collaboration", "Bob Jones")
        # Exact title (60) + exact author match (30) + position (10) = 100
        assert 95 <= scored[0]["score"] <= 100

    def test_sorting_by_score_descending(self):
        results = [
            {
                "asin": "B001",
                "title": "Weak Match",
                "authors": ["Unknown"],
            },
            {
                "asin": "B002",
                "title": "Perfect Match",
                "authors": ["Known Author"],
            },
        ]
        scored = score_results(results, "Perfect Match", "Known Author")
        # B002 should be first after sorting
        assert scored[0]["asin"] == "B002"
        assert scored[0]["score"] > scored[1]["score"]

    def test_empty_results(self):
        scored = score_results([], "Any Title", "Any Author")
        assert scored == []

    def test_empty_authors_with_author_hint(self):
        results = [
            {
                "asin": "B001",
                "title": "Book Title",
                "authors": [],
            },
        ]
        scored = score_results(results, "Book Title", "Some Author")
        assert len(scored) == 1
        # Exact title (60) + no author match (0) + position (10) = 70
        assert 65 <= scored[0]["score"] <= 70


class TestParseSourcePath:
    """Test path parsing to extract title/author hints."""

    def test_simple_filename(self):
        result = parse_source_path("The Great Book.m4b")
        assert result["title_hint"] == "The Great Book"
        assert result["author_hint"] == ""
        assert result["query"] == "The Great Book"

    def test_author_in_parent_directory(self):
        result = parse_source_path("/library/John Smith/The Great Book.m4b")
        assert result["title_hint"] == "The Great Book"
        assert result["author_hint"] == "John Smith"
        assert result["query"] == "John Smith The Great Book"

    def test_strips_series_numbers_brackets(self):
        result = parse_source_path("Series Name [03] Book Title.m4b")
        assert "[03]" not in result["title_hint"]
        assert "Book Title" in result["title_hint"]

    def test_strips_series_numbers_hash(self):
        result = parse_source_path("Book #02- The Sequel.m4b")
        assert "#02-" not in result["title_hint"]
        assert "The Sequel" in result["title_hint"]

    def test_strips_series_numbers_leading_digit(self):
        result = parse_source_path("03 - Book Three.m4b")
        assert result["title_hint"] == "Book Three"

    def test_strips_hash_suffix(self):
        result = parse_source_path("BookName - abc123def4567890.m4b")
        # Should strip the hash suffix
        assert "abc123def4567890" not in result["title_hint"]
        assert "BookName" in result["title_hint"]

    def test_parent_matches_basename_looks_at_grandparent(self):
        path = "/authors/Jane Doe/Book Title/Book Title.m4b"
        result = parse_source_path(path)
        assert result["title_hint"] == "Book Title"
        assert result["author_hint"] == "Jane Doe"

    def test_removes_brackets_parens_braces(self):
        result = parse_source_path("Title [Extra] (Info) {More}.m4b")
        assert "[" not in result["title_hint"]
        assert "]" not in result["title_hint"]
        assert "(" not in result["title_hint"]
        assert ")" not in result["title_hint"]
        assert "{" not in result["title_hint"]
        assert "}" not in result["title_hint"]

    def test_collapses_whitespace(self):
        result = parse_source_path("Too    Many     Spaces.m4b")
        assert "  " not in result["title_hint"]
        assert result["title_hint"] == "Too Many Spaces"


class TestStripSeriesNumbers:
    """Test internal series number stripping function."""

    def test_bracket_numbers(self):
        assert _strip_series_numbers("Book [12] Title") == "Book Title"

    def test_hash_numbers(self):
        assert _strip_series_numbers("Book #5- Title") == "Book Title"

    def test_leading_digit_dash(self):
        assert _strip_series_numbers("03 - Title") == "Title"
        assert _strip_series_numbers("3- Title") == "Title"

    def test_standalone_number_between_spaces(self):
        # Removes single-digit to 3-digit numbers surrounded by spaces
        assert _strip_series_numbers("Series 5 Book") == "Series Book"

    def test_preserves_year_like_numbers(self):
        # Year-like numbers (4+ digits) should be preserved
        result = _strip_series_numbers("Book 2025 Edition")
        # The pattern strips 1-3 digit numbers between spaces, so 2025 stays
        assert "2025" in result

    def test_collapses_extra_whitespace(self):
        result = _strip_series_numbers("Too   Many    Spaces")
        assert "  " not in result
