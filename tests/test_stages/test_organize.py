"""Legacy organize-stage guards exercised through rewrite lifecycle APIs."""

from __future__ import annotations

from pathlib import Path

import pytest

from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.stage import (
    PipelineLevel,
    PipelineMode,
    Stage,
    stages_for,
)
from audiobook_pipeline.services.organize import build_library_path, place_book


def book(**overrides: object) -> BookMetadata:
    """Build typed metadata as it reaches the public organization service."""
    fields: dict[str, object] = {"title": "Great Book", "author": "John Smith"}
    fields.update(overrides)
    return BookMetadata.model_validate(fields)


def write(path: Path, content: bytes = b"audio") -> Path:
    """Create a source or existing library file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            book(),
            ("John Smith", "Great Book", "Great Book.m4b"),
        ),
        (
            book(series="Good Series", series_position="3"),
            (
                "John Smith",
                "Good Series",
                "Book 3 - Great Book",
                "Book 3 - Great Book.m4b",
            ),
        ),
        (
            book(author=""),
            ("Unknown Author", "Great Book", "Great Book.m4b"),
        ),
    ],
)
def test_organization_layout_uses_resolved_metadata(
    tmp_path: Path, metadata: BookMetadata, expected: tuple[str, ...]
) -> None:
    """Placement is a pure consequence of resolved metadata, not source names."""
    path = build_library_path(tmp_path, metadata)
    assert path.relative_to(tmp_path).parts == expected


def test_organize_mode_is_placement_only_after_the_rewrite() -> None:
    """ASIN and tagging belong to their own prior lifecycle, never organize."""
    stages = stages_for(PipelineMode.ORGANIZE, PipelineLevel.NORMAL)
    assert stages == (Stage.ORGANIZE,)


def test_existing_destination_is_not_overwritten(tmp_path: Path) -> None:
    """An ambiguous reorganization produces a visible suffix rather than data loss."""
    destination = write(tmp_path / "library" / "John Smith" / "Great Book.m4b", b"old")
    source = write(tmp_path / "work" / "tagged.m4b", b"new")
    result = place_book(source, destination)
    assert result.name == "Great Book (2).m4b"
    assert destination.read_bytes() == b"old"
    assert result.read_bytes() == b"new"


def test_copy_mode_leaves_tagged_stage_output_available(tmp_path: Path) -> None:
    """Copy behavior preserves the tagged work artifact when a caller needs it."""
    source = write(tmp_path / "work" / "tagged.m4b")
    result = place_book(
        source, tmp_path / "library" / "John Smith" / "Great Book.m4b", move=False
    )
    assert source.exists()
    assert result.read_bytes() == b"audio"


def test_move_mode_creates_the_destination_tree(tmp_path: Path) -> None:
    """Normal placement makes the missing per-book hierarchy before moving."""
    source = write(tmp_path / "work" / "tagged.m4b")
    destination = tmp_path / "library" / "John Smith" / "Great Book" / "Great Book.m4b"
    assert place_book(source, destination) == destination
    assert destination.exists() and not source.exists()
