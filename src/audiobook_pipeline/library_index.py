"""Pre-built index of a Plex audiobook library for O(1) lookups.

Scans the destination library once at batch start using os.walk(),
replacing per-call iterdir() with dict lookups. Supports:
- Fast folder name reuse (normalized near-match detection)
- File existence checks for early-skip
- Cross-source dedup within a batch
- Dynamic registration as new content is added
- Correctly-placed detection for reorganize mode
- Author name canonicalization via surname matching
"""

import os
import re
from pathlib import Path

from loguru import logger

from .ops.organize import _normalize_for_compare

log = logger.bind(stage="index")


class LibraryIndex:
    """In-memory index of library folder structure for batch operations.

    Built once via os.walk() at batch start. Provides O(1) lookups
    instead of per-call iterdir() scans.
    """

    def __init__(self, library_root: Path) -> None:
        self.library_root = library_root
        # Map: parent_path -> {normalized_name: actual_name}
        self._folders: dict[Path, dict[str, str]] = {}
        # Set of (dest_dir, filename) for file existence checks
        self._files: set[tuple[Path, str]] = set()
        # Set of source stems already processed in this batch
        self._processed: set[str] = set()
        # Map: lowercase surname -> list of existing author folder names
        self._authors_by_surname: dict[str, list[str]] = {}
        self._scan(library_root)

    def _scan(self, root: Path) -> None:
        """Walk the library tree and build lookup dicts."""
        if not root.is_dir():
            log.debug(f"Library root does not exist yet: {root}")
            return

        folder_count = 0
        file_count = 0

        for dirpath, dirnames, filenames in os.walk(root):
            parent = Path(dirpath)
            # Index subdirectories under this parent
            if dirnames:
                normalized = {}
                for d in dirnames:
                    norm = _normalize_for_compare(d)
                    normalized[norm] = d
                self._folders[parent] = normalized
                folder_count += len(dirnames)
            # Index files for existence checks
            for f in filenames:
                self._files.add((parent, f))
                file_count += 1

        # Build surname index from top-level author folders
        root_folders = self._folders.get(root, {})
        for actual_name in root_folders.values():
            surname = _extract_surname(actual_name)
            if surname:
                self._authors_by_surname.setdefault(surname, []).append(actual_name)

        log.info(
            f"Library index built: {folder_count} folders, "
            f"{file_count} files under {root}",
        )

    def reuse_existing(self, parent: Path, desired: str) -> str:
        """O(1) folder name lookup -- replaces per-call iterdir().

        Returns the existing folder name if a near-match exists
        under parent, otherwise returns desired unchanged.
        """
        folder_map = self._folders.get(parent)
        if folder_map is None:
            return desired

        # Exact match fast path
        if desired in folder_map.values():
            return desired

        # Normalized lookup
        desired_norm = _normalize_for_compare(desired)
        existing = folder_map.get(desired_norm)
        return existing if existing is not None else desired

    def file_exists(self, dest_dir: Path, filename: str) -> bool:
        """Check if a file exists at dest_dir/filename (O(1))."""
        return (dest_dir, filename) in self._files

    def mark_processed(self, source_stem: str) -> bool:
        """Mark a source stem as processed. Returns True if already seen.

        Used for cross-source dedup within a batch -- prevents
        processing the same book from multiple source directories.
        """
        if source_stem in self._processed:
            return True
        self._processed.add(source_stem)
        return False

    def register_new_folder(self, parent: Path, folder_name: str) -> None:
        """Register a newly created folder in the index."""
        if parent not in self._folders:
            self._folders[parent] = {}
        norm = _normalize_for_compare(folder_name)
        self._folders[parent][norm] = folder_name

    def register_new_file(self, dest_dir: Path, filename: str) -> None:
        """Register a newly added file in the index."""
        self._files.add((dest_dir, filename))

    def is_correctly_placed(self, source_path: Path, dest_path: Path) -> bool:
        """Check if a file is already in its correct destination.

        For reorganize mode: if source is already at the computed
        destination, skip it entirely.
        """
        try:
            return source_path.resolve() == dest_path.resolve()
        except OSError:
            return False

    def match_author(self, desired: str) -> str:
        """Canonicalize an author name against existing library folders.

        Extracts the surname from the desired name, looks up all existing
        author folders with that surname, then picks the best match using
        normalized comparison. Returns the existing folder name if a match
        is found, otherwise returns desired unchanged.
        """
        if not desired:
            return desired

        surname = _extract_surname(desired)
        if not surname:
            return desired

        candidates = self._authors_by_surname.get(surname, [])
        if not candidates:
            return desired

        # Exact match -- fast path
        if desired in candidates:
            return desired

        # Normalized comparison
        desired_norm = _normalize_for_compare(desired)
        for existing in candidates:
            if _normalize_for_compare(existing) == desired_norm:
                log.debug(f"Author canonicalized: '{desired}' -> '{existing}'")
                return existing

        # Single candidate with same surname -- use it
        # (covers "R.A. Salvatore" vs "R. A. Salvatore")
        if len(candidates) == 1:
            log.debug(
                f"Author canonicalized (sole surname match): "
                f"'{desired}' -> '{candidates[0]}'"
            )
            return candidates[0]

        # Multiple candidates, can't disambiguate -- return as-is
        log.debug(
            f"Author '{desired}' has {len(candidates)} surname matches, "
            f"keeping as-is: {candidates}"
        )
        return desired

    def register_author(self, author_name: str) -> None:
        """Register a new author folder in the surname index."""
        surname = _extract_surname(author_name)
        if surname:
            existing = self._authors_by_surname.setdefault(surname, [])
            if author_name not in existing:
                existing.append(author_name)

    @property
    def folder_count(self) -> int:
        """Total number of indexed folders."""
        return sum(len(v) for v in self._folders.values())

    @property
    def file_count(self) -> int:
        """Total number of indexed files."""
        return len(self._files)


def _extract_surname(name: str) -> str:
    """Extract the surname (last word) from an author name.

    Handles multi-author: "Margaret Weis, Tracy Hickman" -> "hickman"
    Handles initials: "R.A. Salvatore" -> "salvatore"
    Handles "and": "Margaret Weis and Tracy Hickman" -> "hickman"
    """
    if not name:
        return ""
    # Take the last author if comma or "and" separated
    parts = re.split(r",\s*|\s+and\s+", name)
    last_author = parts[-1].strip()
    # Take the last word (surname)
    words = last_author.split()
    if not words:
        return ""
    surname = words[-1].lower()
    # Strip trailing punctuation
    surname = surname.rstrip(".,;:")
    return surname
