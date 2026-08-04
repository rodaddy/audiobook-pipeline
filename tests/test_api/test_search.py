"""Public matching, identification, and path-claim regression tests."""

from __future__ import annotations

from pathlib import Path

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.parsed import ParsedPath
from audiobook_pipeline.services.identify import best_match
from audiobook_pipeline.services.matching import normalize_title
from audiobook_pipeline.services.parse import parse_path

ROOT = Path("/source")


def candidate(title: str, author: str, asin: str) -> BookMetadata:
    """Create one validated catalogue result for a ranking assertion."""
    return BookMetadata(title=title, author=author, asin=asin)


def parsed(relative: str) -> ParsedPath:
    """Read an in-memory-shaped source path without touching a media corpus."""
    return parse_path(ROOT / relative, ROOT)


class TestCatalogueRanking:
    """Historical search scores expressed through public winner selection."""

    def test_exact_match_wins_against_a_weaker_title(self) -> None:
        exact = candidate("The Great Book", "John Smith", "B001")
        weaker = candidate("A Different Book", "John Smith", "B002")

        assert (
            best_match(
                [weaker, exact], title_hint="The Great Book", author_hint="John Smith"
            )
            is exact
        )

    def test_title_only_matching_selects_the_title(self) -> None:
        expected = candidate("Project Hail Mary", "Andy Weir", "B001")
        weaker = candidate("Project Artemis", "Andy Weir", "B002")

        assert (
            best_match([weaker, expected], title_hint="Project Hail Mary") is expected
        )

    def test_catalogue_position_breaks_an_exact_tie(self) -> None:
        first = candidate("Good Match", "Author One", "B001")
        second = candidate("Good Match", "Author Two", "B002")

        assert best_match([first, second], title_hint="Good Match") is first

    def test_partial_author_match_beats_the_same_title_by_another_author(self) -> None:
        wrong = candidate("Book Title", "Another Writer", "B001")
        expected = candidate("Book Title", "J.K. Rowling", "B002")

        assert (
            best_match(
                [wrong, expected], title_hint="Book Title", author_hint="Rowling"
            )
            is expected
        )

    def test_multiple_credited_authors_match_the_named_credit(self) -> None:
        wrong = candidate("Collaboration", "Alice Smith", "B001")
        expected = candidate(
            "Collaboration", "Alice Smith, Bob Jones, Carol White", "B002"
        )

        assert (
            best_match(
                [wrong, expected], title_hint="Collaboration", author_hint="Bob Jones"
            )
            is expected
        )

    def test_best_match_returns_the_highest_ranked_candidate(self) -> None:
        weak = candidate("Weak Match", "Unknown", "B001")
        expected = candidate("Perfect Match", "Known Author", "B002")

        assert (
            best_match(
                [weak, expected],
                title_hint="Perfect Match",
                author_hint="Known Author",
            )
            is expected
        )

    def test_empty_catalogue_has_no_match(self) -> None:
        assert best_match([], title_hint="Any Title", author_hint="Any Author") is None

    def test_missing_catalogue_author_is_still_a_usable_title_match(self) -> None:
        expected = candidate("Book Title", "", "B001")

        assert (
            best_match([expected], title_hint="Book Title", author_hint="Some Author")
            is expected
        )


class TestSourcePathClaims:
    """Historical source-name cases at the public path parser boundary."""

    def test_simple_filename_claims_a_title_without_an_author(self) -> None:
        claim = parsed("The Great Book.m4b")

        assert claim.title == "The Great Book"
        assert claim.author == ""

    def test_author_parent_claims_the_author_and_title(self) -> None:
        claim = parsed("John Smith/The Great Book.m4b")

        assert claim.title == "The Great Book"
        assert claim.author == "John Smith"

    def test_bracketed_series_position_is_separate_from_the_title(self) -> None:
        claim = parsed("Series Name [03] Book Title.m4b")

        assert claim.title == "Book Title"
        assert claim.series == "Series Name"
        assert claim.position == "03"

    def test_hash_marked_position_is_separate_from_the_title(self) -> None:
        claim = parsed("John Smith-My Series-#02-The Sequel.m4b")

        assert claim.title == "The Sequel"
        assert claim.series == "My Series"
        assert claim.position == "02"

    def test_numbered_series_position_is_separate_from_the_title(self) -> None:
        claim = parsed("Series Name 03 - Book Three.m4b")

        assert claim.title == "Book Three"
        assert claim.position == "03"

    def test_pipeline_hash_suffix_is_not_part_of_the_title_claim(self) -> None:
        claim = parsed("BookName - abc123def4567890.m4b")

        assert claim.title == "BookName"

    def test_parent_repeating_the_title_is_not_adopted_as_an_author(self) -> None:
        claim = parsed("Jane Doe/Book Title/Book Title.m4b")

        assert claim.title == "Book Title"
        assert claim.author == "Jane Doe"

    def test_normalized_matching_discards_bracket_and_punctuation_delimiters(
        self,
    ) -> None:
        title = normalize_title("Title [Extra] (Info) {More}")

        assert all(mark not in title for mark in "[](){}")

    def test_normalized_matching_collapses_whitespace(self) -> None:
        assert normalize_title("Too    Many     Spaces") == "too many spaces"


class TestTitleNormalization:
    """Historical series-number cleanup through the public matching API."""

    def test_bracket_numbers_do_not_become_part_of_the_title_key(self) -> None:
        assert normalize_title("Book [12] Title") == "book title"

    def test_hash_marked_position_is_read_without_leaking_into_the_title(self) -> None:
        claim = parsed("Author-Collection-#5-Title.m4b")

        assert claim.title == "Title"
        assert claim.position == "5"

    def test_leading_number_and_dash_do_not_become_part_of_the_title_key(self) -> None:
        assert normalize_title("03 - Title") == "title"
        assert normalize_title("3- Title") == "title"

    def test_series_position_is_not_part_of_a_structured_title_claim(self) -> None:
        claim = parsed("Series 5 - Book Title.m4b")

        assert claim.title == "Book Title"
        assert claim.position == "5"

    def test_year_like_numbers_are_preserved_in_a_title_key(self) -> None:
        assert "2025" in normalize_title("Book 2025 Edition")

    def test_normalized_key_collapses_extra_whitespace(self) -> None:
        assert "  " not in normalize_title("Too   Many    Spaces")
