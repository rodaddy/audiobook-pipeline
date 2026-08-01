"""Tests for ops/organize.py path parsing."""

from __future__ import annotations

from audiobook_pipeline.ops.organize import parse_path

# ---------------------------------------------------------------------------
# Author recovery through a series folder, and decimal series positions
#
# Both measured 2026-08-01 against CleanDesktop/tFiles/Done. Each produced a
# WRONG BOOK rather than a missing field: an empty author let Audible rank a
# different author's identically-titled book first, so "Crown of Shadows" in a
# C S Friedman folder resolved to K. M. Shea.
# ---------------------------------------------------------------------------


class TestAuthorThroughSeriesFolder:
    """Author/Series/Book/file.ext -- the author is 3 levels up from the file."""

    def test_author_found_above_series_folder(self):
        r = parse_path(
            "/lib/C S Friedman/The Coldfire Trilogy/"
            "03 - Crown of Shadows/03 - Crown of Shadows.m4a"
        )
        assert r["author"] == "C S Friedman"
        assert r["title"] == "Crown of Shadows"

    def test_collection_word_folder_is_not_the_author(self):
        """'The Coldfire Trilogy' must be rejected and skipped past."""
        r = parse_path(
            "/lib/C S Friedman/The Coldfire Trilogy/"
            "01 - Black Sun Rising/01 - Black Sun Rising.m4a"
        )
        assert r["author"] == "C S Friedman"

    def test_climb_stops_before_collection_root(self):
        """A container dir above the author must not become the author.

        _looks_like_author rejects single-word names, so 'Done' and 'tFiles'
        cannot be picked up as the climb passes the real author.
        """
        r = parse_path(
            "/Volumes/x/tFiles/Done/C S Friedman/The Coldfire Trilogy/"
            "02 - When True Night Falls/02 - When True Night Falls.m4a"
        )
        assert r["author"] == "C S Friedman"

    def test_author_directly_above_book_still_works(self):
        """The simple Author/Book/file layout must be unchanged."""
        r = parse_path("/lib/Brian McClellan/Promise of Blood/Promise of Blood.mp3")
        assert r["author"] == "Brian McClellan"


class TestDecimalSeriesPosition:
    """Novella positions like 0.5 must not be split at the decimal point."""

    def test_decimal_position_stripped_whole(self):
        r = parse_path(
            "/lib/C S Friedman/The Coldfire Trilogy/0.5 - Dominion/0.5 - Dominion.m4a"
        )
        assert r["title"] == "Dominion"

    def test_integer_position_still_stripped(self):
        r = parse_path("/lib/Author Name/Series/03 - Third Book/03 - Third Book.m4a")
        assert r["title"] == "Third Book"

    def test_title_that_is_only_a_number_survives(self):
        """'1984' is a title, not a position -- the strip must not empty it."""
        r = parse_path("/lib/George Orwell/1984/1984.m4b")
        assert r["title"]


class TestPartMarkerNotInTitle:
    """'Part N of M' is a file's position in a split book, not its title."""

    def test_part_of_marker_stripped(self):
        r = parse_path(
            "/lib/Brian McClellan/Powder Mage 0.2 - Servant of the Crown/"
            "Servant of the Crown Part 1 of 3.mp3"
        )
        assert r["title"] == "Servant of the Crown"

    def test_zero_padded_part_marker_stripped(self):
        r = parse_path(
            "/lib/Brian McClellan/The Autumn Republic/"
            "The Autumn Republic Part 07 of 19.mp3"
        )
        assert r["title"] == "The Autumn Republic"

    def test_title_ending_in_part_n_is_kept(self):
        """No 'of M' total: 'Part 2' may genuinely be the title."""
        r = parse_path("/lib/Some Author/Kill Bill Part 2/Kill Bill Part 2.m4b")
        assert "Part 2" in r["title"]
