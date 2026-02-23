"""Pre-built index of a Plex audiobook library for O(1) lookups.

Scans the destination library once at batch start using os.walk(),
replacing per-call iterdir() with dict lookups. Supports:
- Fast folder name reuse (normalized near-match detection)
- File existence checks for early-skip
- Cross-source dedup within a batch
- Dynamic registration as new content is added
- Correctly-placed detection for reorganize mode
- Author name canonicalization via surname matching + persistent alias DB
"""

import json
import os
import re
from pathlib import Path

from loguru import logger

from .ops.organize import _is_near_match, _normalize_for_compare

log = logger.bind(stage="index")

AUTHOR_ALIASES_FILE = ".author_aliases.json"


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
        # Persistent author alias DB: variant -> canonical name
        self._author_aliases: dict[str, str] = {}
        self._load_aliases()
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
        Uses token-based similarity to catch redundant author prefixes.
        """
        folder_map = self._folders.get(parent)
        if folder_map is None:
            return desired

        # Exact match fast path
        if desired in folder_map.values():
            return desired

        # Normalized exact lookup (O(1))
        desired_norm = _normalize_for_compare(desired)
        existing = folder_map.get(desired_norm)
        if existing is not None:
            return existing

        # Token-based near-match scan (O(n) but rare -- only when exact fails)
        for existing_norm, existing_name in folder_map.items():
            if _is_near_match(desired_norm, existing_norm):
                log.debug(f"Near-match found: '{desired}' -> '{existing_name}'")
                return existing_name

        return desired

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

        Checks persistent alias DB first (O(1)), then surname matching
        with initials-aware normalization. When a match is found, saves
        the alias for future runs.
        """
        if not desired:
            return desired

        # 1. Persistent alias DB -- instant lookup
        canonical = self._author_aliases.get(desired)
        if canonical:
            log.debug(f"Author alias hit: '{desired}' -> '{canonical}'")
            return canonical

        # 2. Clean the desired name (strip role suffixes, co-editors)
        cleaned = _clean_author_name(desired)

        # 3. Check alias for cleaned version too
        if cleaned != desired:
            canonical = self._author_aliases.get(cleaned)
            if canonical:
                self._save_alias(desired, canonical)
                log.debug(f"Author alias hit (cleaned): '{desired}' -> '{canonical}'")
                return canonical

        # 4. Surname matching against library folders
        surname = _extract_surname(cleaned)
        if not surname:
            return desired

        candidates = self._authors_by_surname.get(surname, [])
        if not candidates:
            return desired

        # Exact match -- fast path
        if desired in candidates:
            return desired
        if cleaned in candidates:
            self._save_alias(desired, cleaned)
            return cleaned

        # Initials-aware normalized comparison
        desired_norm = _normalize_author(cleaned)
        for existing in candidates:
            if _normalize_author(existing) == desired_norm:
                log.debug(f"Author canonicalized: '{desired}' -> '{existing}'")
                self._save_alias(desired, existing)
                return existing

        # Fallback: standard normalization
        desired_norm_std = _normalize_for_compare(cleaned)
        for existing in candidates:
            if _normalize_for_compare(existing) == desired_norm_std:
                log.debug(f"Author canonicalized (std): '{desired}' -> '{existing}'")
                self._save_alias(desired, existing)
                return existing

        # Single candidate with same surname -- use it
        if len(candidates) == 1:
            log.debug(
                f"Author canonicalized (sole surname match): "
                f"'{desired}' -> '{candidates[0]}'"
            )
            self._save_alias(desired, candidates[0])
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

    def _load_aliases(self) -> None:
        """Load persistent author alias DB from library root."""
        alias_file = self.library_root / AUTHOR_ALIASES_FILE
        if not alias_file.exists():
            return
        try:
            data = json.loads(alias_file.read_text())
            # Format: {canonical: [alias1, alias2, ...]}
            for canonical, aliases in data.items():
                for alias in aliases:
                    self._author_aliases[alias] = canonical
            log.debug(f"Loaded {len(self._author_aliases)} author aliases")
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Failed to load author aliases: {e}")

    def _save_alias(self, variant: str, canonical: str) -> None:
        """Save a new author alias to the persistent DB."""
        if variant == canonical:
            return
        self._author_aliases[variant] = canonical

        # Rebuild canonical -> [aliases] format for saving
        canonical_map: dict[str, list[str]] = {}
        for alias, canon in self._author_aliases.items():
            canonical_map.setdefault(canon, []).append(alias)
        # Sort for stable output
        for key in canonical_map:
            canonical_map[key] = sorted(canonical_map[key])

        alias_file = self.library_root / AUTHOR_ALIASES_FILE
        try:
            alias_file.write_text(json.dumps(canonical_map, indent=2, sort_keys=True))
            log.info(f"Author alias saved: '{variant}' -> '{canonical}'")
        except OSError as e:
            log.warning(f"Failed to save author alias: {e}")

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

    Cleans the name first to strip role suffixes before extraction.
    """
    if not name:
        return ""
    cleaned = _clean_author_name(name)
    # Take the first author (primary) if comma or "and" separated
    parts = re.split(r",\s*|\s+and\s+", cleaned)
    first_author = parts[0].strip()
    # Take the last word (surname)
    words = first_author.split()
    if not words:
        return ""
    surname = words[-1].lower()
    # Strip trailing punctuation
    surname = surname.rstrip(".,;:")
    return surname


def _clean_author_name(name: str) -> str:
    """Strip role suffixes and co-author annotations from author names.

    "J. R. R. Tolkien, Christopher Tolkien - editor" -> "J. R. R. Tolkien"
    "Brandon Sanderson (Author)" -> "Brandon Sanderson"
    """
    # Strip " - editor", " - translator", etc.
    cleaned = re.sub(
        r",?\s+\w+\s*-\s*(editor|translator|narrator|foreword|introduction)\b.*$",
        "",
        name,
        flags=re.IGNORECASE,
    )
    # Strip "(Author)", "(Editor)", etc.
    cleaned = re.sub(
        r"\s*\((Author|Editor|Translator|Narrator)\)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip()


def _normalize_author(name: str) -> str:
    """Normalize an author name for comparison, handling initials.

    "J.R.R. Tolkien" -> "j r r tolkien"
    "J. R. R. Tolkien" -> "j r r tolkien"
    "J. R.R. Tolkien" -> "j r r tolkien"
    """
    s = name.lower().strip()
    # Expand concatenated initials: "j.r.r." -> "j. r. r."
    s = re.sub(r"([a-z])\.([a-z])", r"\1. \2", s)
    # Repeat for triple+ initials
    s = re.sub(r"([a-z])\.([a-z])", r"\1. \2", s)
    # Strip all periods
    s = s.replace(".", "")
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s
