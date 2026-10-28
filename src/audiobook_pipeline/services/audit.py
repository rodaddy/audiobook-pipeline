"""Read-only health checks for a finished audiobook library."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from pathlib import Path

from loguru import logger
from rapidfuzz import fuzz

from audiobook_pipeline.models.book import SOURCE_EXTENSIONS
from audiobook_pipeline.models.library import AuditFinding, AuditReport
from audiobook_pipeline.services.library import TARGET_EXTENSIONS
from audiobook_pipeline.services.matching import normalize_title
from audiobook_pipeline.utils.ffmpeg import FfmpegError, probe

ALL_CHECKS = ("tags", "duplicates", "structure", "sources", "stale")
log = logger.bind(stage="audit")
_MANDATORY_TAGS = frozenset({
    "artist",
    "album_artist",
    "album",
    "title",
    "genre",
    "sort_album",
})
_RECOMMENDED_TAGS = frozenset({"composer", "date", "comment", "description"})
_SUSPICIOUS_VALUES = frozenset({
    "",
    "unknown",
    "unknown artist",
    "various artists",
    "untitled",
})


def run_audit(root: Path, *, checks: tuple[str, ...] = ALL_CHECKS) -> AuditReport:
    """Run selected checks without changing a library file or its metadata."""
    check_map = {
        "tags": check_metadata_tags,
        "duplicates": check_duplicates,
        "structure": check_structure,
        "sources": check_leftover_sources,
        "stale": check_stale,
    }
    findings = tuple(finding for check in checks for finding in check_map[check](root))
    total_files = (
        sum(1 for path in root.rglob("*.m4b") if path.is_file()) if root.is_dir() else 0
    )
    return AuditReport(library_root=root, total_files=total_files, findings=findings)


def check_metadata_tags(root: Path) -> tuple[AuditFinding, ...]:
    """Report missing, suspicious, and incomplete M4B metadata tags."""
    findings: list[AuditFinding] = []
    for path in _m4bs(root):
        try:
            tags = {key.lower(): value for key, value in probe(path).tags.items()}
        except FfmpegError:
            log.warning("metadata tag probe failed; recording critical finding")
            findings.append(
                _finding(
                    "tags",
                    "critical",
                    path,
                    root,
                    "ffprobe failed -- file may be corrupt",
                )
            )
            continue
        findings.extend(_tag_findings(path, root, tags))
    return tuple(findings)


def check_duplicates(root: Path) -> tuple[AuditFinding, ...]:
    """Report exact, near, and same-directory M4B duplicates."""
    paths_by_title: dict[str, list[Path]] = defaultdict(list)
    paths_by_directory: dict[Path, list[Path]] = defaultdict(list)
    for path in _m4bs(root):
        paths_by_title[normalize_title(path.stem)].append(path)
        paths_by_directory[path.parent].append(path)
    findings = (
        _exact_duplicate_findings(paths_by_title, root)
        + _directory_duplicate_findings(paths_by_directory, root)
        + _near_duplicate_findings(paths_by_title, root)
    )
    return tuple(findings)


def check_structure(root: Path) -> tuple[AuditFinding, ...]:
    """Report invalid M4B depth, raw filenames, and unexpected loose files."""
    findings: list[AuditFinding] = []
    for path in _m4bs(root):
        parts = path.relative_to(root).parts
        if len(parts) == 1:
            findings.append(
                _finding(
                    "structure",
                    "critical",
                    path,
                    root,
                    "M4B file at library root (no author folder)",
                )
            )
        elif len(parts) == 2:
            findings.append(
                _finding(
                    "structure",
                    "warning",
                    path,
                    root,
                    "M4B file directly under author folder (missing book subfolder)",
                )
            )
        elif len(parts) > 4:
            findings.append(
                _finding(
                    "structure",
                    "warning",
                    path,
                    root,
                    f"Nested too deep ({len(parts)} levels, expected 3-4)",
                )
            )
        if "[" in path.name and "]" in path.name:
            findings.append(
                _finding(
                    "structure",
                    "warning",
                    path,
                    root,
                    "Filename contains brackets (possible raw download name)",
                )
            )
    return tuple(findings)


def check_leftover_sources(root: Path) -> tuple[AuditFinding, ...]:
    """Report source audio left beside a converted book or unconverted alone."""
    findings: list[AuditFinding] = []
    for path in _audio_sources(root):
        sibling_m4b = any(
            sibling.suffix.lower() == ".m4b"
            for sibling in path.parent.iterdir()
            if sibling.is_file()
        )
        severity = "warning" if sibling_m4b else "critical"
        message = (
            f"Leftover source file ({path.suffix}) alongside M4B"
            if sibling_m4b
            else f"Source file ({path.suffix}) with no M4B -- unconverted book"
        )
        findings.append(_finding("sources", severity, path, root, message))
    return tuple(findings)


def check_stale(root: Path) -> tuple[AuditFinding, ...]:
    """Record that external Plex inspection is intentionally not implicit."""
    return (_finding("stale", "info", None, root, "Skipped: no Plex query requested"),)


def _m4bs(root: Path) -> tuple[Path, ...]:
    """List target M4Bs in deterministic order."""
    return (
        tuple(
            sorted(
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in TARGET_EXTENSIONS
            )
        )
        if root.is_dir()
        else ()
    )


def _audio_sources(root: Path) -> tuple[Path, ...]:
    """List visible source audio, excluding hidden and staging folders."""
    return (
        tuple(
            path
            for path in sorted(root.rglob("*"))
            if path.is_file()
            and path.suffix.lower() in SOURCE_EXTENSIONS - TARGET_EXTENSIONS
            and not any(
                part.startswith(("_", ".")) for part in path.relative_to(root).parts
            )
        )
        if root.is_dir()
        else ()
    )


def _tag_findings(path: Path, root: Path, tags: dict[str, str]) -> list[AuditFinding]:
    """Build metadata findings for one already-probed file."""
    findings = _required_tag_findings(path, root, tags)
    findings.extend(_warning_tag_findings(path, root, tags))
    return findings


def _required_tag_findings(
    path: Path, root: Path, tags: dict[str, str]
) -> list[AuditFinding]:
    """Build critical and informational findings from required tag fields."""
    findings: list[AuditFinding] = [
        _finding("tags", "critical", path, root, f"Missing mandatory tag: {tag}")
        for tag in _MANDATORY_TAGS
        if not tags.get(tag, "").strip()
    ]
    findings.extend(
        _finding(
            "tags",
            "critical",
            path,
            root,
            f"Suspicious value for '{tag}': '{tags.get(tag, '')}'",
        )
        for tag in ("artist", "album_artist", "title", "album")
        if tags.get(tag, "").strip().lower() in _SUSPICIOUS_VALUES
    )
    findings.extend(
        _finding("tags", "info", path, root, f"Missing recommended tag: {tag}")
        for tag in _RECOMMENDED_TAGS
        if not tags.get(tag, "").strip()
    )
    return findings


def _warning_tag_findings(
    path: Path, root: Path, tags: dict[str, str]
) -> list[AuditFinding]:
    """Build warning-level findings from related tag values."""
    findings: list[AuditFinding] = []
    if not tags.get("media_type", "").strip():
        findings.append(
            _finding(
                "tags",
                "warning",
                path,
                root,
                "Missing media_type tag (should be '2' for audiobooks)",
            )
        )
    if tags.get("genre", "").strip().lower() == "audiobook":
        findings.append(
            _finding(
                "tags",
                "warning",
                path,
                root,
                "Genre is 'Audiobook' -- should be actual genre from Audible",
            )
        )
    if (
        tags.get("title", "").strip().lower()
        == tags.get("album_artist", "").strip().lower()
        and tags.get("title", "").strip()
    ):
        findings.append(
            _finding(
                "tags",
                "warning",
                path,
                root,
                "Title matches album_artist -- possible tag error",
            )
        )
    return findings


def _exact_duplicate_findings(
    grouped: dict[str, list[Path]], root: Path
) -> list[AuditFinding]:
    """Describe repeated normalized titles."""
    return [
        _finding(
            "duplicates",
            "warning",
            paths[0],
            root,
            f"Duplicate title '{title}' in {len(paths)} locations",
        )
        for title, paths in grouped.items()
        if title and len(paths) > 1
    ]


def _directory_duplicate_findings(
    grouped: dict[Path, list[Path]], root: Path
) -> list[AuditFinding]:
    """Describe multiple distinct M4Bs inside one book directory."""
    return [
        _finding(
            "duplicates",
            "warning",
            paths[0],
            root,
            f"Directory contains {len(paths)} M4B files (expected 1)",
        )
        for paths in grouped.values()
        if len(paths) > 1 and not all("part" in path.stem.lower() for path in paths)
    ]


def _near_duplicate_findings(
    grouped: dict[str, list[Path]], root: Path
) -> list[AuditFinding]:
    """Describe non-identical titles that are highly similar across folders."""
    titles = tuple(title for title in grouped if title)
    return [
        _finding(
            "duplicates",
            "info",
            grouped[left][0],
            root,
            f"Near-duplicate ({fuzz.ratio(left, right):.0f}% similar): '{left}' <-> '{right}'",
        )
        for left, right in combinations(titles, 2)
        if fuzz.ratio(left, right) >= 85
    ]


def _finding(
    check: str, severity: str, path: Path | None, root: Path, message: str
) -> AuditFinding:
    """Make a finding with a root-relative path when a file is involved."""
    return AuditFinding(
        check=check,
        severity=severity,
        path=path.relative_to(root) if path else None,
        message=message,
    )
