"""Tests for api/search.py title scoring.

An Audible title routinely carries a subtitle the source folder omits, and a
plain fuzzy ratio punishes those extra tokens hard enough to rank the WRONG
book first. Measured 2026-08-01: hint "Forsworn" from a `Brian McClellan/`
folder scored David Estes's exact-title "Forsworn" at 100 and McClellan's
"Forsworn: A Powder Mage Novella" at 41, and the pipeline tagged the book with
the wrong author.
"""

from __future__ import annotations

from audiobook_pipeline.api.search import _title_score, score_results


def _result(title: str, authors: list[str], asin: str = "X") -> dict:
    return {"title": title, "authors": authors, "asin": asin}


class TestSubtitleDoesNotSinkTheMatch:
    def test_colon_subtitle_scores_as_the_head(self):
        assert _title_score("Forsworn", "Forsworn: A Powder Mage Novella") == 100

    def test_dash_subtitle_scores_as_the_head(self):
        assert _title_score("Exile", "Exile - Book Two of the Dark Elf Trilogy") == 100

    def test_exact_full_title_still_scores_100(self):
        assert _title_score("Forsworn", "Forsworn") == 100

    def test_unrelated_title_still_scores_low(self):
        assert _title_score("Forsworn", "Oath Sworn") < 80

    def test_head_match_does_not_rescue_a_different_book(self):
        """'Hero' must not match 'Heroes of Olympus' via the subtitle path."""
        assert _title_score("Hero", "Heroes of Olympus") < 80


class TestRightAuthorWins:
    def test_correct_author_outranks_exact_title_by_someone_else(self):
        """The end-to-end failure, as a ranking assertion."""
        results = [
            _result("Forsworn", ["David Estes"], "A"),
            _result("Forsworn: A Powder Mage Novella", ["Brian McClellan"], "B"),
        ]
        scored = score_results(results, "Forsworn", "Brian McClellan")
        assert scored[0]["authors"] == ["Brian McClellan"]

    def test_without_an_author_hint_ranking_is_unchanged(self):
        """No author known: the exact title still wins, as before."""
        results = [
            _result("Forsworn", ["David Estes"], "A"),
            _result("Forsworn: A Powder Mage Novella", ["Brian McClellan"], "B"),
        ]
        scored = score_results(results, "Forsworn", "")
        assert scored[0]["authors"] == ["David Estes"]

    def test_matching_author_and_title_beats_matching_author_alone(self):
        results = [
            _result("Some Other Book", ["Brian McClellan"], "A"),
            _result("Promise of Blood", ["Brian McClellan"], "B"),
        ]
        scored = score_results(results, "Promise of Blood", "Brian McClellan")
        assert scored[0]["title"] == "Promise of Blood"
