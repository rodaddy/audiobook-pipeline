"""Tests for library path construction and placing finished books.

Separate from ``test_organize.py``, which covers the pre-rewrite
``ops.organize.parse_path`` and stays untouched -- it encodes the
2026-08-01 measurement where an empty author let Audible rank a different
author's identically-titled book first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.services.organize import (
    AUTHOR_OVERRIDE_MARKER,
    _normalize,
    book_stem,
    build_library_path,
    find_author_override,
    is_near_match,
    place_book,
    reuse_existing_folder,
    shelf_title,
)


def book(**overrides: object) -> BookMetadata:
    """A book with sensible defaults."""
    fields: dict[str, object] = {"title": "Homeland", "author": "R.A. Salvatore"}
    fields.update(overrides)
    return BookMetadata.model_validate(fields)


def make_file(path: Path, content: bytes = b"audio") -> Path:
    """Create a file and its parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# normalizing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Food: A Love Story (2014)", "food a love story"),
        ("Food- A Love Story", "food a love story"),
        ("The Chronicles", "the chronicle"),
        ("Ann Leckie", "ann leckie"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert _normalize(raw) == expected


# ---------------------------------------------------------------------------
# near matching
# ---------------------------------------------------------------------------


def test_stop_word_difference_matches() -> None:
    assert is_near_match(_normalize("Wheel of Time"), _normalize("Wheel Time"))


def test_meaningful_extra_words_block_the_match() -> None:
    """ "Origins of The Wheel of Time" is a different work."""
    assert not is_near_match(
        _normalize("The Wheel of Time"), _normalize("Origins of The Wheel of Time")
    )


def test_an_author_prefix_is_not_a_stop_word() -> None:
    """ "Ann Leckie - The Raven Tower" carries two meaningful extra tokens."""
    assert not is_near_match(
        _normalize("The Raven Tower"), _normalize("Ann Leckie - The Raven Tower")
    )


def test_punctuation_and_year_differences_match() -> None:
    assert is_near_match(
        _normalize("Food: A Love Story (2014)"), _normalize("Food- A Love Story")
    )


def test_single_common_word_does_not_match() -> None:
    assert not is_near_match(_normalize("The"), _normalize("A"))


@pytest.mark.parametrize(
    ("catalogue", "shelf"),
    [
        # The pair that split Brian McClellan's folder on the first live run.
        ("The Powder Mage Trilogy", "Powder Mage"),
        ("Mistborn Saga", "Mistborn"),
        ("Sprawl Trilogy Series", "Sprawl"),
        ("Dragonlance Saga", "Dragonlance"),
        ("Malazan Book of the Fallen Series", "Malazan Book of the Fallen"),
    ],
)
def test_a_series_form_noun_does_not_make_a_second_series(
    catalogue: str, shelf: str
) -> None:
    """ "Trilogy" and "Saga" name the container, not the work."""
    assert is_near_match(_normalize(catalogue), _normalize(shelf))


def test_one_distinctive_word_is_enough_to_match_on() -> None:
    """The old rule needed two tokens, so "Mistborn" could never match."""
    assert is_near_match(_normalize("Mistborn Saga"), _normalize("Mistborn"))


def test_a_meaningful_extra_word_still_blocks_a_short_name() -> None:
    """Loosening the token count must not turn every prefix into a match."""
    assert not is_near_match(_normalize("Ascendant Books"), _normalize("Ascendant"))
    assert not is_near_match(_normalize("Homeland"), _normalize("Homecoming"))


def test_reuse_returns_the_existing_spelling(tmp_path: Path) -> None:
    (tmp_path / "Food A Love Story").mkdir()

    assert reuse_existing_folder(tmp_path, "Food: A Love Story (2014)") == (
        "Food A Love Story"
    )


def test_reuse_prefers_an_exact_match(tmp_path: Path) -> None:
    (tmp_path / "Homeland").mkdir()
    (tmp_path / "Homelands").mkdir()

    assert reuse_existing_folder(tmp_path, "Homeland") == "Homeland"


def test_reuse_leaves_a_genuinely_new_name_alone(tmp_path: Path) -> None:
    (tmp_path / "Brandon Sanderson").mkdir()

    assert reuse_existing_folder(tmp_path, "R.A. Salvatore") == "R.A. Salvatore"


def test_reuse_on_a_missing_parent_is_not_an_error(tmp_path: Path) -> None:
    assert reuse_existing_folder(tmp_path / "nope", "Anything") == "Anything"


# ---------------------------------------------------------------------------
# shelf titles
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Forsworn: A Powder Mage Novella", "Forsworn"),
        ("The Name of the Wind: Kingkiller Chronicle Day One", "The Name of the Wind"),
        ("Homeland", "Homeland"),
        # Only the FIRST colon splits; everything after it is subtitle.
        ("A: B: C", "A"),
    ],
)
def test_the_subtitle_is_dropped(raw: str, expected: str) -> None:
    """The 712 files already on the shelf carry no subtitle. Neither do ours."""
    assert shelf_title(book(title=raw)) == expected


def test_a_title_that_is_only_a_colon_keeps_its_original() -> None:
    """Never return an empty stem: an unnamed file is worse than a long one."""
    assert shelf_title(book(title=":")) == ":"


# ---------------------------------------------------------------------------
# stems
# ---------------------------------------------------------------------------


def test_series_book_carries_its_number() -> None:
    metadata = book(series="The Dark Elf Trilogy", series_position="1")
    assert book_stem(metadata) == "Book 1 - Homeland"


def test_standalone_is_just_the_title() -> None:
    assert book_stem(book()) == "Homeland"


def test_series_without_a_position_is_treated_as_standalone() -> None:
    assert book_stem(book(series="Some Series")) == "Homeland"


def test_year_is_not_in_the_stem() -> None:
    """It lives in the tags; in the name it makes one book into two."""
    assert "2014" not in book_stem(book(release_year=2014))


def test_illegal_characters_are_sanitized() -> None:
    assert book_stem(book(title="Cause/Effect Part 2")) == "Cause_Effect Part 2"


def test_the_subtitle_never_reaches_the_stem_as_an_underscore() -> None:
    """The exact defect the first live run wrote into the real library."""
    metadata = book(
        title="Forsworn: A Powder Mage Novella",
        series="The Powder Mage Trilogy",
        series_position="0.1",
    )

    assert book_stem(metadata) == "Book 0.1 - Forsworn"


# ---------------------------------------------------------------------------
# library paths
# ---------------------------------------------------------------------------


def test_standalone_path_is_author_then_book_folder(tmp_path: Path) -> None:
    assert build_library_path(tmp_path, book()) == (
        tmp_path / "R.A. Salvatore" / "Homeland" / "Homeland.m4b"
    )


def test_series_path_inserts_the_series_level(tmp_path: Path) -> None:
    metadata = book(series="The Dark Elf Trilogy", series_position="1")

    assert build_library_path(tmp_path, metadata) == (
        tmp_path
        / "R.A. Salvatore"
        / "The Dark Elf Trilogy"
        / "Book 1 - Homeland"
        / "Book 1 - Homeland.m4b"
    )


def test_the_book_gets_its_own_folder(tmp_path: Path) -> None:
    """619 of the 712 files already on the shelf are filed this way."""
    path = build_library_path(tmp_path, book())

    assert path.parent.name == path.stem


def test_missing_author_gets_a_named_folder(tmp_path: Path) -> None:
    assert build_library_path(tmp_path, book(author="")).parents[1].name == (
        "Unknown Author"
    )


def test_existing_author_folder_is_reused(tmp_path: Path) -> None:
    (tmp_path / "R.A. Salvatore").mkdir()

    path = build_library_path(tmp_path, book(author="R A Salvatore"))

    assert path.parents[1].name == "R.A. Salvatore"


def test_an_existing_book_folder_is_reused(tmp_path: Path) -> None:
    """Otherwise a re-run files the same book beside itself under a new spelling."""
    (tmp_path / "R.A. Salvatore" / "Homeland (2003)").mkdir(parents=True)

    path = build_library_path(tmp_path, book())

    assert path.parent.name == "Homeland (2003)"


# ---------------------------------------------------------------------------
# the author override marker
# ---------------------------------------------------------------------------


def test_marker_is_found_at_the_start_directory(tmp_path: Path) -> None:
    (tmp_path / AUTHOR_OVERRIDE_MARKER).write_text("")

    assert find_author_override(tmp_path, tmp_path) == tmp_path


def test_marker_is_found_in_an_ancestor(tmp_path: Path) -> None:
    deep = tmp_path / "Series" / "Book"
    deep.mkdir(parents=True)
    (tmp_path / AUTHOR_OVERRIDE_MARKER).write_text("")

    assert find_author_override(deep, tmp_path) == tmp_path


def test_the_climb_stops_at_the_boundary(tmp_path: Path) -> None:
    """A marker outside the library must never redirect a book into it."""
    outside = tmp_path / "outside"
    library = outside / "library"
    library.mkdir(parents=True)
    (outside / AUTHOR_OVERRIDE_MARKER).write_text("")

    assert find_author_override(library, library) is None


def test_no_marker_yields_none(tmp_path: Path) -> None:
    assert find_author_override(tmp_path, tmp_path) is None


# ---------------------------------------------------------------------------
# placing the file
# ---------------------------------------------------------------------------


def test_move_puts_the_file_in_place_and_removes_the_source(tmp_path: Path) -> None:
    source = make_file(tmp_path / "work" / "book.m4b")
    destination = tmp_path / "library" / "Author" / "Homeland.m4b"

    result = place_book(source, destination)

    assert result == destination
    assert destination.read_bytes() == b"audio"
    assert not source.exists()


def test_copy_leaves_the_source_in_place(tmp_path: Path) -> None:
    source = make_file(tmp_path / "work" / "book.m4b")
    destination = tmp_path / "library" / "Author" / "Homeland.m4b"

    place_book(source, destination, move=False)

    assert source.exists()
    assert destination.exists()


def test_an_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    """A collision means the identification was ambiguous; a human should see."""
    destination = make_file(tmp_path / "library" / "Homeland.m4b", b"original")
    source = make_file(tmp_path / "work" / "book.m4b", b"new")

    result = place_book(source, destination)

    assert result.name == "Homeland (2).m4b"
    assert destination.read_bytes() == b"original"
    assert result.read_bytes() == b"new"


def test_collisions_keep_counting_up(tmp_path: Path) -> None:
    make_file(tmp_path / "library" / "Homeland.m4b", b"a")
    make_file(tmp_path / "library" / "Homeland (2).m4b", b"b")
    source = make_file(tmp_path / "work" / "book.m4b", b"c")

    result = place_book(source, tmp_path / "library" / "Homeland.m4b")

    assert result.name == "Homeland (3).m4b"


def test_destination_directories_are_created(tmp_path: Path) -> None:
    source = make_file(tmp_path / "work" / "book.m4b")
    destination = tmp_path / "library" / "A" / "B" / "C" / "Homeland.m4b"

    assert place_book(source, destination).exists()


def test_initialled_names_unify_across_spellings() -> None:
    """ "R.A. Salvatore" and "R A Salvatore" are one author, one folder."""
    assert is_near_match(_normalize("R.A. Salvatore"), _normalize("R A Salvatore"))
    assert is_near_match(_normalize("J.R.R. Tolkien"), _normalize("J R R Tolkien"))


def test_different_authors_still_do_not_match() -> None:
    assert not is_near_match(
        _normalize("R.A. Salvatore"), _normalize("Brandon Sanderson")
    )


def test_different_narrations_stay_separate() -> None:
    """The only real-library collision, 2026-08-02: two Harry Potter readings."""
    assert not is_near_match(
        _normalize("Harry Potter (Jim Dale)"),
        _normalize("Harry Potter (Stephen Fry)"),
    )


def test_edition_notes_are_still_stripped() -> None:
    """ "(Unabridged)" describes the edition; it does not distinguish the work."""
    assert is_near_match(
        _normalize("The Autumn Republic (Unabridged)"),
        _normalize("The Autumn Republic"),
    )


# ---------------------------------------------------------------------------
# Cases measured against the live 130-author library, 2026-08-02. Every one is
# a REAL duplicate the library was already carrying -- there were no false
# positives across 130 authors and 271 series folders.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("J. R. R. Tolkien", "J.R.R. Tolkien"),
        (
            "Christopher Tolkien, J. R. R. Tolkien",
            "J.R.R. Tolkien, Christopher Tolkien",
        ),
        ("The Children of Hurin", "The Children of Húrin"),
        ("Kingkiller Chronicles", "The Kingkiller Chronicle"),
        ("Sage of Shadowdale", "The Sage of Shadowdale"),
    ],
)
def test_real_library_duplicates_are_matched(left: str, right: str) -> None:
    assert is_near_match(_normalize(left), _normalize(right))
