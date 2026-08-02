"""Author canonicalization: same person merges, different people do not.

A shared surname is not identity. The sole-surname-match fallback used to
accept any single candidate, which filed Michael Williams's book under Tad
Williams -- one author's work landing on another author's shelf, which in Plex
and Prologue is indistinguishable from losing it.
"""

from audiobook_pipeline.library_index import _given_names_compatible


class TestGivenNamesCompatible:
    def test_initials_spacing_variants_are_same_person(self):
        assert _given_names_compatible("J.R.R. Tolkien", "J. R. R. Tolkien")
        assert _given_names_compatible("R.A. Salvatore", "R. A. Salvatore")
        assert _given_names_compatible("George R. R. Martin", "George R.R. Martin")
        assert _given_names_compatible("J. K. Rowling", "J.K. Rowling")
        assert _given_names_compatible("James S.A. Corey", "James S. A. Corey")

    def test_extra_middle_name_is_same_person(self):
        assert _given_names_compatible("Richard Morgan", "Richard K. Morgan")
        assert _given_names_compatible("Paul Thompson", "Paul B. Thompson")

    def test_different_given_names_are_different_people(self):
        assert not _given_names_compatible("Michael Williams", "Tad Williams")
        assert not _given_names_compatible("Glen Cook", "Tonya C. Cook")
        assert not _given_names_compatible("Cixin Liu", "Ken Liu")
        assert not _given_names_compatible("Brandon Sanderson", "Robert Sanderson")

    def test_initial_matching_first_letter_but_spelled_out_differs(self):
        """'Michael' and 'Mark' share an initial and are still not the same."""
        assert not _given_names_compatible("Michael Williams", "Mark Williams")

    def test_bare_surname_cannot_be_matched(self):
        """With no given name there is nothing to disambiguate on."""
        assert not _given_names_compatible("Tolkien", "J. R. R. Tolkien")
        assert not _given_names_compatible("Williams", "Tad Williams")

    def test_identical_names(self):
        assert _given_names_compatible("Brandon Sanderson", "Brandon Sanderson")

    def test_surname_first_form(self):
        assert _given_names_compatible("Salvatore, R. A.", "R. A. Salvatore")
