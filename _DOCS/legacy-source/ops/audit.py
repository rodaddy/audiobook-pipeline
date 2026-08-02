"""Library audit checks for the audiobook pipeline.

Five categories of deep library health checks that extend the basic
verify_library() in ops/verify.py:

1. check_metadata_tags -- ffprobe M4B files for missing/suspicious tags
2. check_duplicates -- cross-author dupes, near-matches, multi-M4B dirs
3. check_structure -- folder hierarchy validation
4. check_leftover_sources -- non-M4B audio files in organized library
5. check_stale_plex -- Plex items with missing metadata (optional, network)
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz

log = logger.bind(stage="audit")

# Extensions considered "source audio" (should not be in organized library)
SOURCE_EXTENSIONS = {".mp3", ".flac", ".ogg", ".wma", ".wav", ".aac"}

# Mandatory M4B tags for Plex compatibility
MANDATORY_TAGS = {"artist", "album_artist", "album", "title", "genre", "sort_album"}

# Recommended but not critical
RECOMMENDED_TAGS = {"composer", "date", "comment", "description"}

# Suspicious values that indicate broken/missing metadata
SUSPICIOUS_VALUES = {"unknown", "unknown artist", "various artists", "untitled", ""}


@dataclass
class AuditFinding:
    """A single issue found during library audit."""

    check: str  # "tags", "duplicates", "structure", "sources", "stale"
    severity: str  # "critical", "warning", "info"
    path: str  # Relative path from library root
    message: str  # Human-readable description
    fixable: bool = False  # Can --fix handle this?
    fix_action: str = ""  # What --fix would do (e.g., "delete", "touch")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AuditReport:
    """Aggregated audit results."""

    library_root: str
    total_files: int = 0
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "info")

    @property
    def fixable_count(self) -> int:
        return sum(1 for f in self.findings if f.fixable)

    def to_dict(self) -> dict:
        return {
            "library_root": self.library_root,
            "total_files": self.total_files,
            "summary": {
                "total_issues": len(self.findings),
                "critical": self.critical_count,
                "warning": self.warning_count,
                "info": self.info_count,
                "fixable": self.fixable_count,
            },
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Check 1: Metadata tags
# ---------------------------------------------------------------------------


def _ffprobe_tags(m4b_path: Path) -> dict[str, str] | None:
    """Extract format tags from an M4B file via ffprobe.

    Returns lowercased tag dict, or None on probe failure.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(m4b_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        raw_tags = data.get("format", {}).get("tags", {})
        # Lowercase all keys for consistent lookup
        return {k.lower(): v for k, v in raw_tags.items()}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None


def check_metadata_tags(library_root: Path) -> list[AuditFinding]:
    """Probe every M4B file for missing or suspicious metadata tags."""
    findings: list[AuditFinding] = []

    for m4b in sorted(library_root.rglob("*.m4b")):
        rel = str(m4b.relative_to(library_root))
        tags = _ffprobe_tags(m4b)

        if tags is None:
            findings.append(
                AuditFinding(
                    check="tags",
                    severity="critical",
                    path=rel,
                    message="ffprobe failed -- file may be corrupt",
                )
            )
            continue

        # Check mandatory tags
        for tag in MANDATORY_TAGS:
            val = tags.get(tag, "").strip()
            if not val:
                findings.append(
                    AuditFinding(
                        check="tags",
                        severity="critical",
                        path=rel,
                        message=f"Missing mandatory tag: {tag}",
                    )
                )

        # Check suspicious values
        for tag in ("artist", "album_artist", "title", "album"):
            val = tags.get(tag, "").strip().lower()
            if val in SUSPICIOUS_VALUES:
                findings.append(
                    AuditFinding(
                        check="tags",
                        severity="critical",
                        path=rel,
                        message=f"Suspicious value for '{tag}': '{tags.get(tag, '')}'",
                    )
                )

        # Title == album_artist is a sign of truncated/wrong tags
        title_val = tags.get("title", "").strip().lower()
        artist_val = tags.get("album_artist", "").strip().lower()
        if title_val and artist_val and title_val == artist_val:
            findings.append(
                AuditFinding(
                    check="tags",
                    severity="warning",
                    path=rel,
                    message=f"Title matches album_artist ('{tags.get('title', '')}') -- possible tag error",
                )
            )

        # Genre == hardcoded "Audiobook" with no real genre
        genre = tags.get("genre", "").strip().lower()
        if genre == "audiobook":
            findings.append(
                AuditFinding(
                    check="tags",
                    severity="warning",
                    path=rel,
                    message="Genre is 'Audiobook' -- should be actual genre from Audible",
                )
            )

        # album_artist with too many comma-separated names (likely multiple authors dumped)
        aa = tags.get("album_artist", "")
        if aa.count(",") > 1:
            findings.append(
                AuditFinding(
                    check="tags",
                    severity="warning",
                    path=rel,
                    message=f"album_artist has {aa.count(',') + 1} names: '{aa}'",
                )
            )

        # media_type should be 2 for audiobooks
        media_type = tags.get("media_type", "").strip()
        if not media_type:
            findings.append(
                AuditFinding(
                    check="tags",
                    severity="warning",
                    path=rel,
                    message="Missing media_type tag (should be '2' for audiobooks)",
                )
            )

        # Recommended tags (info-level)
        for tag in RECOMMENDED_TAGS:
            val = tags.get(tag, "").strip()
            if not val:
                findings.append(
                    AuditFinding(
                        check="tags",
                        severity="info",
                        path=rel,
                        message=f"Missing recommended tag: {tag}",
                    )
                )

    return findings


# ---------------------------------------------------------------------------
# Check 2: Duplicates
# ---------------------------------------------------------------------------


def check_duplicates(library_root: Path) -> list[AuditFinding]:
    """Find duplicate and near-duplicate books across the library."""
    findings: list[AuditFinding] = []

    # Collect all M4B files grouped by parent dir and by normalized title
    title_paths: dict[str, list[str]] = defaultdict(list)
    dir_m4b_count: dict[str, list[str]] = defaultdict(list)
    all_m4b_names: list[tuple[str, str]] = []  # (normalized_stem, rel_path)

    for m4b in sorted(library_root.rglob("*.m4b")):
        rel = str(m4b.relative_to(library_root))
        parent_rel = str(m4b.parent.relative_to(library_root))
        dir_m4b_count[parent_rel].append(rel)

        # Normalize: strip series prefix like "Book N - " and lowercase
        stem = m4b.stem.lower()
        norm = _normalize_for_dedup(stem)
        title_paths[norm].append(rel)
        all_m4b_names.append((norm, rel))

    # Same normalized title appearing multiple times
    for norm_title, paths in title_paths.items():
        if len(paths) > 1:
            findings.append(
                AuditFinding(
                    check="duplicates",
                    severity="warning",
                    path=paths[0],
                    message=f"Duplicate title '{norm_title}' in {len(paths)} locations: "
                    + ", ".join(paths),
                )
            )

    # Multiple M4B files in the same directory
    for dir_path, m4bs in dir_m4b_count.items():
        if len(m4bs) > 1:
            # Check if these are multi-part files (Part 1, Part 2, etc.)
            names = [Path(p).stem for p in m4bs]
            part_pattern = re.compile(r"[,\s]*part\s+\d+\s*$", re.IGNORECASE)
            all_parts = all(part_pattern.search(n) for n in names)

            if all_parts:
                findings.append(
                    AuditFinding(
                        check="duplicates",
                        severity="info",
                        path=dir_path,
                        message=f"Multi-part book ({len(m4bs)} parts): "
                        + ", ".join(Path(p).name for p in m4bs),
                    )
                )
            else:
                findings.append(
                    AuditFinding(
                        check="duplicates",
                        severity="warning",
                        path=dir_path,
                        message=f"Directory contains {len(m4bs)} M4B files (expected 1): "
                        + ", ".join(Path(p).name for p in m4bs),
                    )
                )

    # Near-duplicate detection via rapidfuzz (O(n^2) but library size is manageable)
    seen_pairs: set[tuple[str, str]] = set()
    for i, (norm_a, path_a) in enumerate(all_m4b_names):
        for norm_b, path_b in all_m4b_names[i + 1 :]:
            if norm_a == norm_b:
                continue  # Already caught as exact duplicate
            pair = (min(path_a, path_b), max(path_a, path_b))
            if pair in seen_pairs:
                continue
            ratio = fuzz.ratio(norm_a, norm_b)
            if ratio >= 85:
                seen_pairs.add(pair)
                findings.append(
                    AuditFinding(
                        check="duplicates",
                        severity="info",
                        path=path_a,
                        message=f"Near-duplicate ({ratio}% similar): '{Path(path_a).name}' <-> '{Path(path_b).name}' ({path_b})",
                    )
                )

    return findings


def _normalize_for_dedup(stem: str, author: str = "") -> str:
    """Normalize a filename stem for duplicate detection.

    Strips series prefixes, part suffixes, ASIN codes, noise words,
    and optionally the author-name prefix from Audible downloads.
    """
    s = stem.lower()
    # Strip "Book N - " or "N - " prefix
    s = re.sub(r"^(book\s+)?\d+\s*-\s*", "", s)
    # Strip ASIN codes like [B00AAI79WY] or [B0...]
    s = re.sub(r"\[B0[A-Z0-9]+\]", "", s, flags=re.IGNORECASE)
    # Strip bracket content like [01]
    s = re.sub(r"\[.*?\]", "", s)
    # Strip (Unabridged) / (Abridged)
    s = re.sub(r"\(\s*(?:un)?abridged\s*\)", "", s, flags=re.IGNORECASE)
    # Strip remaining parenthesized content
    s = re.sub(r"\(.*?\)", "", s)
    # Strip ", Part N" or "Part N" suffix (multi-part fragments)
    s = re.sub(r",?\s*part\s+\d+\s*$", "", s, flags=re.IGNORECASE)
    # Strip trailing " - Book N" pattern (Audible naming)
    s = re.sub(r"\s*-\s*book\s+\d+\s*$", "", s, flags=re.IGNORECASE)
    # Strip series suffixes: "- Series Name, Volume One" / "- Series, Book N"
    s = re.sub(r"\s*-\s*dragonlance[^-]*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*-?\s*,?\s*volume\s+\w+\s*$", "", s, flags=re.IGNORECASE)
    # Strip "The Ender Saga - book 1" style suffix
    s = re.sub(r"\s*-\s*the\s+\w+\s+saga.*$", "", s, flags=re.IGNORECASE)
    # Strip trailing underscored sub-book: "_Book I - subtitle"
    s = re.sub(r"_book\s+[\w]+\s*-.*$", "", s, flags=re.IGNORECASE)
    # Strip " - Author Name" suffix (common in downloads)
    # Do this BEFORE prefix stripping since suffixes are more common
    s = re.sub(r"\s*-\s*j\.?\s*r\.?\s*r\.?\s*tolkien.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*-\s*christopher\s+tolkien.*$", "", s, flags=re.IGNORECASE)
    # Generic "- Author Name" suffix: match " - FirstName LastName" at end
    # Only if it looks like a person name (2-4 capitalized words)
    s = re.sub(r"\s+-\s+[a-z]\.\s*[a-z]\.\s*[a-z]+\s*$", "", s)
    # Strip author-name prefix ("Author Name - Title" -> "Title")
    if author:
        norm_auth = _normalize_author(author)
        # Try stripping "Author - " or "Author Name - " from start
        prefix_pat = re.escape(author.lower()).replace(r"\ ", r"\s*")
        s = re.sub(rf"^{prefix_pat}\s*-\s*", "", s)
        # Also try normalized form for initials like "B. T. Narro"
        prefix_pat2 = re.escape(norm_auth).replace(r"\ ", r"\s*")
        s = re.sub(rf"^{prefix_pat2}\s*-\s*", "", s)
        # Also try stripping " - Author" suffix using author name
        suffix_pat = re.escape(author.lower()).replace(r"\ ", r"\s*")
        s = re.sub(rf"\s*-\s*{suffix_pat}.*$", "", s)
        suffix_pat2 = re.escape(norm_auth).replace(r"\ ", r"\s*")
        s = re.sub(rf"\s*-\s*{suffix_pat2}.*$", "", s)
    # Strip leading "Vampire Chronicles NN_" style prefix
    s = re.sub(r"^[\w\s]+\d+_", "", s)
    # Replace underscores and hyphens with spaces
    s = s.replace("_", " ")
    s = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", s)  # word-word hyphens only
    # Strip commas (interferes with token matching)
    s = s.replace(",", "")
    # Strip leading "Dragonlance" / "DragonLance" prefix
    s = re.sub(r"^dragonlance\s*[-:]?\s*", "", s, flags=re.IGNORECASE)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


# Known franchise folders that consolidate multiple authors
FRANCHISE_FOLDERS: dict[str, str] = {
    "dragonlance": "dragonlance",
    "forgotten realms": "forgotten realms",
    "star wars": "star wars",
    "warhammer": "warhammer",
    "dungeons & dragons": "dungeons & dragons",
    "magic the gathering": "magic the gathering",
}


def _normalize_author(author: str) -> str:
    """Normalize an author name for cross-library matching.

    Handles initials (R.A. -> ra), and/& equivalence, franchise folders,
    and common prefixes like 'Edited by'.
    """
    s = author.strip().lower()
    # Strip "Edited by" / "edited by" prefix
    s = re.sub(r"^edited\s+by\s+", "", s)
    # Normalize & <-> and
    s = s.replace(" & ", " and ")
    # Strip periods (R.A. -> RA, J.R.R. -> JRR)
    s = s.replace(".", "")
    # Collapse single-letter sequences (r a -> ra, j r r -> jrr)
    s = re.sub(r"\b([a-z])\s+(?=[a-z]\b)", r"\1", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_franchise_folder(name: str) -> bool:
    """Check if a folder name is a known franchise umbrella."""
    return _normalize_author(name) in FRANCHISE_FOLDERS


# ---------------------------------------------------------------------------
# Check 3: Structure
# ---------------------------------------------------------------------------


def check_structure(library_root: Path) -> list[AuditFinding]:
    """Validate folder structure matches Author/[Series/]Book/file.m4b."""
    findings: list[AuditFinding] = []

    for m4b in sorted(library_root.rglob("*.m4b")):
        rel = m4b.relative_to(library_root)
        parts = rel.parts
        rel_str = str(rel)

        # Minimum: Author/Book/file.m4b (3 parts)
        if len(parts) < 3:
            # File directly under author folder
            if len(parts) == 2:
                findings.append(
                    AuditFinding(
                        check="structure",
                        severity="warning",
                        path=rel_str,
                        message="M4B file directly under author folder (missing book subfolder)",
                    )
                )
            elif len(parts) == 1:
                findings.append(
                    AuditFinding(
                        check="structure",
                        severity="critical",
                        path=rel_str,
                        message="M4B file at library root (no author folder)",
                    )
                )
            continue

        # Max depth: Author/Series/Book/file.m4b (4 parts)
        if len(parts) > 4:
            findings.append(
                AuditFinding(
                    check="structure",
                    severity="warning",
                    path=rel_str,
                    message=f"Nested too deep ({len(parts)} levels, expected 3-4)",
                )
            )

        # Check for bracket patterns in filename (raw download names)
        if "[" in m4b.name and "]" in m4b.name:
            findings.append(
                AuditFinding(
                    check="structure",
                    severity="warning",
                    path=rel_str,
                    message=f"Filename contains brackets (possible raw download name): {m4b.name}",
                )
            )

    # Check for files that are NOT m4b but are loose in the tree (non-audio)
    # Note: audio sources are handled by check_leftover_sources
    for item in sorted(library_root.rglob("*")):
        if not item.is_file():
            continue
        if item.suffix.lower() in {".m4b", *SOURCE_EXTENSIONS}:
            continue
        # Skip known marker files
        if item.name in {".author-override", ".DS_Store", "Thumbs.db"}:
            continue
        # Skip cover images
        if item.suffix.lower() in {".jpg", ".jpeg", ".png"}:
            continue
        rel_str = str(item.relative_to(library_root))
        # Skip _unsorted and hidden dirs
        if any(p.startswith(("_", ".")) for p in item.relative_to(library_root).parts):
            continue
        findings.append(
            AuditFinding(
                check="structure",
                severity="info",
                path=rel_str,
                message=f"Unexpected file type in library: {item.suffix or '(no extension)'}",
            )
        )

    # Note .author-override files (info, not an issue)
    for marker in sorted(library_root.rglob(".author-override")):
        rel_str = str(marker.relative_to(library_root))
        findings.append(
            AuditFinding(
                check="structure",
                severity="info",
                path=rel_str,
                message="Author override marker present",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Check 4: Leftover sources
# ---------------------------------------------------------------------------


def check_leftover_sources(library_root: Path) -> list[AuditFinding]:
    """Find non-M4B audio files that shouldn't be in the organized library."""
    findings: list[AuditFinding] = []

    for src_file in sorted(library_root.rglob("*")):
        if not src_file.is_file():
            continue
        if src_file.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        # Skip _unsorted and hidden dirs
        if any(
            p.startswith(("_", ".")) for p in src_file.relative_to(library_root).parts
        ):
            continue

        rel = str(src_file.relative_to(library_root))

        # Check if an M4B exists alongside
        sibling_m4b = any(
            f.suffix.lower() == ".m4b" for f in src_file.parent.iterdir() if f.is_file()
        )

        if sibling_m4b:
            findings.append(
                AuditFinding(
                    check="sources",
                    severity="warning",
                    path=rel,
                    message=f"Leftover source file ({src_file.suffix}) alongside M4B -- safe to delete",
                    fixable=True,
                    fix_action="delete",
                )
            )
        else:
            findings.append(
                AuditFinding(
                    check="sources",
                    severity="critical",
                    path=rel,
                    message=f"Source file ({src_file.suffix}) with no M4B -- unconverted book",
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Check 5: Stale Plex entries
# ---------------------------------------------------------------------------


def check_stale_plex(
    library_root: Path,
    plex_url: str = "http://10.71.1.35:32400",
    plex_token: str = "",
) -> list[AuditFinding]:
    """Check Plex for items with missing/broken metadata.

    Queries the Plex API for the AudioBooks library section and flags
    items where the metadata appears empty or broken.

    Requires network access and a valid Plex token.
    """
    findings: list[AuditFinding] = []

    if not plex_token:
        findings.append(
            AuditFinding(
                check="stale",
                severity="info",
                path="",
                message="Skipped: no PLEX_TOKEN configured (set env var or store in vaultwarden)",
            )
        )
        return findings

    try:
        import httpx
    except ImportError:
        findings.append(
            AuditFinding(
                check="stale",
                severity="info",
                path="",
                message="Skipped: httpx not available",
            )
        )
        return findings

    headers = {"X-Plex-Token": plex_token, "Accept": "application/json"}

    try:
        # Find the AudioBooks library section
        client = httpx.Client(timeout=30)
        sections_resp = client.get(f"{plex_url}/library/sections", headers=headers)
        sections_resp.raise_for_status()
        sections = sections_resp.json()

        audiobook_key = None
        for section in sections.get("MediaContainer", {}).get("Directory", []):
            if section.get("title", "").lower() in ("audiobooks", "audio books"):
                audiobook_key = section["key"]
                break

        if not audiobook_key:
            findings.append(
                AuditFinding(
                    check="stale",
                    severity="info",
                    path="",
                    message="No AudioBooks library section found in Plex",
                )
            )
            return findings

        # Get all items in the section
        items_resp = client.get(
            f"{plex_url}/library/sections/{audiobook_key}/all",
            headers=headers,
        )
        items_resp.raise_for_status()
        items = items_resp.json()

        for item in items.get("MediaContainer", {}).get("Metadata", []):
            title = item.get("title", "")
            parent_title = item.get("parentTitle", "")
            grandparent_title = item.get("grandparentTitle", "")

            # Flag items with missing artist/author
            if not grandparent_title and not parent_title:
                # Try to get the file path
                media = item.get("Media", [{}])
                parts = media[0].get("Part", [{}]) if media else [{}]
                file_path = parts[0].get("file", "") if parts else ""
                rel = file_path
                if file_path and library_root:
                    try:
                        rel = str(Path(file_path).relative_to(library_root))
                    except ValueError:
                        pass

                findings.append(
                    AuditFinding(
                        check="stale",
                        severity="warning",
                        path=rel,
                        message=f"Plex shows '{title}' with no artist -- may need rescan",
                        fixable=True,
                        fix_action="touch",
                    )
                )

        client.close()

    except httpx.HTTPError as e:
        findings.append(
            AuditFinding(
                check="stale",
                severity="info",
                path="",
                message=f"Plex API error: {e}",
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

ALL_CHECKS = ("tags", "duplicates", "structure", "sources", "stale")


def run_audit(
    library_root: Path,
    checks: tuple[str, ...] = ALL_CHECKS,
    plex_url: str = "http://10.71.1.35:32400",
    plex_token: str = "",
) -> AuditReport:
    """Run selected audit checks and return aggregated report."""
    report = AuditReport(library_root=str(library_root))

    # Count total M4B files
    report.total_files = sum(1 for _ in library_root.rglob("*.m4b"))
    log.info(f"Scanning {report.total_files} M4B files in {library_root}")

    if "tags" in checks:
        log.info("Running metadata tag check...")
        report.findings.extend(check_metadata_tags(library_root))

    if "duplicates" in checks:
        log.info("Running duplicate check...")
        report.findings.extend(check_duplicates(library_root))

    if "structure" in checks:
        log.info("Running structure check...")
        report.findings.extend(check_structure(library_root))

    if "sources" in checks:
        log.info("Running leftover source check...")
        report.findings.extend(check_leftover_sources(library_root))

    if "stale" in checks:
        log.info("Running Plex stale check...")
        report.findings.extend(
            check_stale_plex(library_root, plex_url=plex_url, plex_token=plex_token)
        )

    log.info(
        f"Audit complete: {len(report.findings)} issues "
        f"({report.critical_count} critical, {report.warning_count} warning, "
        f"{report.info_count} info)"
    )

    return report


# ---------------------------------------------------------------------------
# Fix actions
# ---------------------------------------------------------------------------


def apply_fixes(
    library_root: Path, findings: list[AuditFinding], dry_run: bool = False
) -> list[str]:
    """Apply auto-fixes for fixable findings.

    Returns list of actions taken (or would-take in dry_run mode).
    """
    actions: list[str] = []

    for finding in findings:
        if not finding.fixable:
            continue

        target = library_root / finding.path
        if not target.exists():
            continue

        if finding.fix_action == "delete":
            label = (
                f"{'[DRY-RUN] Would delete' if dry_run else 'Deleted'}: {finding.path}"
            )
            if not dry_run:
                target.unlink()
            actions.append(label)
            log.info(label)

        elif finding.fix_action == "touch":
            label = (
                f"{'[DRY-RUN] Would touch' if dry_run else 'Touched'}: {finding.path}"
            )
            if not dry_run:
                target.touch()
            actions.append(label)
            log.info(label)

    return actions
