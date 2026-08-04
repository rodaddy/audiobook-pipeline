"""Regression tests for public catalogue title and author matching."""

from __future__ import annotations

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.services.identify import best_match
from audiobook_pipeline.services.matching import normalize_title, titles_match


def candidate(title: str, author: str, asin: str) -> BookMetadata:
    """Create one validated search result for a public ranking assertion."""
    return BookMetadata(title=title, author=author, asin=asin)


class TestSubtitleDoesNotSinkTheMatch:
    def test_colon_subtitle_selects_the_matching_book(self) -> None:
        unrelated = candidate("Oath Sworn", "Brian McClellan", "A")
        expected = candidate("Forsworn: A Powder Mage Novella", "Brian McClellan", "B")

        assert best_match([unrelated, expected], title_hint="Forsworn") is expected

    def test_dash_subtitle_selects_the_matching_book(self) -> None:
        unrelated = candidate("Exiled", "R.A. Salvatore", "A")
        expected = candidate(
            "Exile - Book Two of the Dark Elf Trilogy", "R.A. Salvatore", "B"
        )

        assert best_match([unrelated, expected], title_hint="Exile") is expected

    def test_exact_full_title_wins_when_the_scores_otherwise_tie(self) -> None:
        exact = candidate("Forsworn", "Brian McClellan", "A")
        subtitle = candidate("Forsworn: A Powder Mage Novella", "Brian McClellan", "B")

        assert best_match([exact, subtitle], title_hint="Forsworn") is exact

    def test_unrelated_titles_do_not_match_as_one_library_key(self) -> None:
        assert not titles_match(
            normalize_title("Forsworn"), normalize_title("Oath Sworn")
        )

    def test_subtitle_matching_does_not_rescue_a_different_book(self) -> None:
        assert not titles_match(
            normalize_title("Hero"), normalize_title("Heroes of Olympus")
        )


class TestRightAuthorWins:
    def test_correct_author_outranks_an_exact_title_by_someone_else(self) -> None:
        wrong = candidate("Forsworn", "David Estes", "A")
        expected = candidate("Forsworn: A Powder Mage Novella", "Brian McClellan", "B")

        assert (
            best_match(
                [wrong, expected], title_hint="Forsworn", author_hint="Brian McClellan"
            )
            is expected
        )

    def test_without_an_author_hint_the_exact_title_stays_first(self) -> None:
        expected = candidate("Forsworn", "David Estes", "A")
        subtitle = candidate("Forsworn: A Powder Mage Novella", "Brian McClellan", "B")

        assert best_match([expected, subtitle], title_hint="Forsworn") is expected

    def test_matching_author_and_title_beat_matching_author_alone(self) -> None:
        wrong = candidate("Some Other Book", "Brian McClellan", "A")
        expected = candidate("Promise of Blood", "Brian McClellan", "B")

        assert (
            best_match(
                [wrong, expected],
                title_hint="Promise of Blood",
                author_hint="Brian McClellan",
            )
            is expected
        )
