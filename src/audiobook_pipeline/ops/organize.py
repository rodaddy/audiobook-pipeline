"""Parse audiobook paths and build Plex-compatible folder structure.

Absorbs python/path_builder.py logic into the pipeline package.
Handles copying/moving files to NFS output with proper Author/Series/Title layout.
"""

import re
import shutil
from pathlib import Path

from ..sanitize import sanitize_filename

# Label suffixes that are not real series names (e.g., "Title - Audiobook")
_LABEL_SUFFIXES = frozenset({
    "audiobook", "audio", "unabridged", "abridged",
})

# Basenames that are useless -- use parent dir name instead
_GENERIC_BASENAMES = frozenset({
    "file", "mp3", "audiobook", "audio", "book", "track", "output",
    "part1", "part2", "part 1", "part 2", "disc1", "disc2",
})


# ---------------------------------------------------------------------------
# Path parsing
# ---------------------------------------------------------------------------

def parse_path(source_path: str) -> dict:
    """Parse a source path into structured metadata components.

    Returns dict with keys: author, title, series, position.
    Any field may be empty string if not determined.
    """
    p = Path(source_path)

    # Get basename without extension, strip pipeline hash suffix
    basename = p.stem if p.is_file() or p.suffix else p.name
    basename = _strip_hash(basename)

    # Pattern F: recover from generic basenames (file.m4b, MP3.m4b)
    if basename.lower() in _GENERIC_BASENAMES:
        parent_raw = _strip_hash(p.parent.name) if p.parent != Path("/") else ""
        if parent_raw and parent_raw.lower() not in _GENERIC_BASENAMES:
            basename = _strip_label_suffix(parent_raw)
        else:
            gp_raw = (
                _strip_hash(p.parent.parent.name)
                if p.parent.parent not in (Path("/"), Path("."))
                else ""
            )
            if gp_raw:
                basename = _strip_label_suffix(gp_raw)

    # Parent and grandparent
    parent = p.parent
    parent_name = _strip_hash(parent.name) if parent != Path("/") else ""

    grandparent = parent.parent
    gp_name = (
        _strip_hash(grandparent.name)
        if grandparent not in (Path("/"), Path("."))
        else ""
    )

    # Great-grandparent for deeper nesting
    ggp = grandparent.parent
    ggp_name = (
        _strip_hash(ggp.name)
        if ggp not in (Path("/"), Path("."))
        else ""
    )

    author = ""
    title = ""
    series = ""
    position = ""

    # Use parent dir for primary parsing (it has the richest info)
    parse_target = parent_name if parent_name else basename

    # Pattern A: "Author-Series-#N-Title" or nested subseries
    # Normalize malformed markers: "#-N" -> "#N", "#N " -> "#N-"
    parse_target = re.sub(r"-#-(\d+)", r"-#\1", parse_target)
    parse_target = re.sub(r"-#(\d+) ", r"-#\1-", parse_target)
    if re.search(r"-#\d+-", parse_target):
        normalized = parse_target

        last_match = list(re.finditer(r"-#(\d+)-", normalized))[-1]
        position = last_match.group(1)
        title = normalized[last_match.end():].strip()

        prefix = normalized[:last_match.start()]
        first_match = re.search(r"-(.+?)-#\d+", prefix)
        if first_match:
            author_end = prefix.index("-")
            author = prefix[:author_end].strip()
            series = prefix[author_end + 1:].strip()
            series = re.split(r"-#\d+-", series)[0].strip()
        else:
            parts = prefix.rsplit("-", 1)
            if len(parts) == 2:
                author = parts[0].strip()
                series = parts[1].strip()
            else:
                author = prefix.strip()
                series = ""

        return _build_result(author, title, series, position)

    # Pattern B2: "Name N - Title" (e.g., "Deathgate Cycle 1 - Dragon Wing")
    match_b2 = re.match(r"^(.+?)\s+(\d{1,3})\s+-\s+(.+)$", parse_target)
    if match_b2:
        series = match_b2.group(1).strip()
        position = match_b2.group(2).strip()
        title = match_b2.group(3).strip()

    # Pattern B: "SeriesName NN Title" (e.g., "The First Law 04 Best Served Cold")
    if not title:
        match_b = re.match(r"^(.+?)\s+(\d{1,3})\s+(.+)$", parse_target)
        if match_b:
            potential_series = match_b.group(1).strip()
            potential_pos = match_b.group(2).strip()
            potential_title = match_b.group(3).strip()
            if len(potential_title) >= 3:
                series = potential_series
                position = potential_pos
                title = potential_title

    # Pattern G: "Series [NN] Title" (e.g., "Mistborn [01] The Final Empire")
    if not title:
        match_g = re.match(r"^(.+?)\s+\[(\d+)\]\s+(.+)$", parse_target)
        if match_g:
            series = match_g.group(1).strip()
            position = match_g.group(2).strip()
            title = match_g.group(3).strip()

    # Pattern E: split "Author - Series" grandparents
    if gp_name and " - " in gp_name:
        parts = gp_name.split(" - ", 1)
        gp_author = parts[0].strip()
        gp_series = parts[1].strip()
        # Skip if right side is a label word
        if gp_series.lower() not in _LABEL_SUFFIXES:
            if not re.search(r"\d", gp_author):
                if not author:
                    author = gp_author
                if not series:
                    series = gp_series

    # Pattern C: grandparent as author
    if parent_name == basename and gp_name:
        if not author:
            extracted = _extract_author(gp_name)
            if _looks_like_author(extracted):
                author = extracted
    elif gp_name and not author:
        if _looks_like_author(gp_name):
            author = _extract_author(gp_name)
        elif ggp_name and _looks_like_author(ggp_name):
            author = _extract_author(ggp_name)
            if not series:
                series = _clean_collection_suffix(gp_name)

    # Author-Title split from parent: "Author-Title" or "Author - Title"
    if not author and not series and "-" in parent_name and not title:
        if not re.search(r"-#\d+", parent_name):
            parts = parent_name.split("-", 1)
            candidate_author = parts[0].strip()
            candidate_title = parts[1].strip()
            if _looks_like_author(candidate_author) and len(candidate_title) >= 3:
                author = candidate_author
                title = candidate_title

    # Dedup: if author == series, the path didn't have a real author
    if author and series and author.lower() == series.lower():
        author = ""

    # Pattern D: clean up title from basename if not set
    if not title:
        title = basename
        if author and title.lower().startswith(author.lower()):
            title = title[len(author):].lstrip(" -").strip()
        if series and title.lower().startswith(series.lower()):
            title = title[len(series):].lstrip(" -").strip()
        title = re.sub(r"\[\d+\]", "", title)
        bracket_match = re.match(r"^\[(.+)\]$", title.strip())
        if bracket_match:
            title = bracket_match.group(1)
        title = re.sub(r"^\d+\s*[-\u2013]?\s*", "", title)
        title = re.sub(r"\s+", " ", title).strip()

    # Clean metadata junk from title
    title = re.sub(r"\s*\{[^}]+\}", "", title)
    title = re.sub(r"\s*\([^)]*\b\d+k\b[^)]*\)", "", title)
    title = re.sub(r"\s*\([A-Z][a-z]+\)\s+\d+k\s+[\d.]+", "", title)
    # Strip "(The AudioBook)", "(Audiobook)", "(Unabridged)", etc.
    title = re.sub(r"\s*\((?:The\s+)?Audio\s*Book\)", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(Unabridged\)", "", title, flags=re.IGNORECASE)
    # Strip dash artifacts: "Food- A Love Story" -> "Food A Love Story"
    title = re.sub(r"(\w)-\s", r"\1 ", title)
    title = re.sub(r"-$", "", title).strip()

    # Extract parenthesized series info from title:
    # "Title - (Series Name - Day 1)" or "Title (Series, Book 2.5)"
    if not series:
        paren_match = re.search(
            r"\s*-?\s*\(([^)]+?)"
            r"(?:\s*[-,]\s*(?:Book|Day|#)\s*([\d.]+))?"
            r"\)", title,
        )
        if paren_match:
            candidate_series = paren_match.group(1).strip().rstrip(" -,")
            if len(candidate_series) >= 3 and candidate_series.lower() not in _LABEL_SUFFIXES:
                series = candidate_series
                if paren_match.group(2) and not position:
                    position = paren_match.group(2)
                # Remove the parenthesized part from title
                title = title[:paren_match.start()].strip().rstrip(" -")


    if not title:
        title = _clean_title_fallback(basename)

    return _build_result(author, title, series, position)


# ---------------------------------------------------------------------------
# Plex path building
# ---------------------------------------------------------------------------

def build_plex_path(
    nfs_output_dir: Path,
    metadata: dict,
) -> Path:
    """Build the Plex-compatible destination path.

    Structure:
      - With author + series: Author/Series/Title/
      - With author, no series: Author/Title/
      - No author, with series: Series/Title/
      - No author, no series: _unsorted/Title/

    Checks for existing near-duplicate folders and reuses them
    to prevent duplicates like "Food A Love Story" vs "Food- A Love Story".
    """
    author = sanitize_filename(metadata["author"]) if metadata["author"] else ""
    title = sanitize_filename(metadata["title"]) if metadata["title"] else "Unknown"
    series_name = sanitize_filename(metadata["series"]) if metadata["series"] else ""

    # Skip series folder when series == title (avoids Author/Title/Title/)
    if series_name and series_name.lower() == title.lower():
        series_name = ""

    # Top level is always author. No author = _unsorted.
    if author and series_name:
        base = nfs_output_dir / author
        series_name = _reuse_existing(base, series_name)
        title_dir = base / series_name
        title = _reuse_existing(title_dir, title)
        return title_dir / title
    elif author:
        base = nfs_output_dir / author
        title = _reuse_existing(base, title)
        return base / title
    elif series_name:
        base = nfs_output_dir / "_unsorted"
        series_name = _reuse_existing(base, series_name)
        title_dir = base / series_name
        title = _reuse_existing(title_dir, title)
        return title_dir / title
    else:
        base = nfs_output_dir / "_unsorted"
        title = _reuse_existing(base, title)
        return base / title


def copy_to_library(
    source_file: Path,
    dest_dir: Path,
    dry_run: bool = False,
) -> Path:
    """Copy an audiobook file to its library destination.

    Creates the destination directory tree and copies the file.
    Returns the destination file path.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / source_file.name

    if dry_run:
        return dest_file

    if dest_file.exists():
        # Skip if same size (already copied)
        if dest_file.stat().st_size == source_file.stat().st_size:
            return dest_file

    shutil.copy2(source_file, dest_file)
    return dest_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_for_compare(name: str) -> str:
    """Normalize a folder name for duplicate comparison.

    Strips punctuation, years, edition markers, and whitespace
    so "Food- A Love Story" matches "Food A Love Story (2014)".
    """
    s = name.lower()
    # Strip year suffixes: "(2014)", "(2009)"
    s = re.sub(r"\s*\(\d{4}\)", "", s)
    # Strip edition markers: "(Unabridged)", "(The AudioBook)"
    s = re.sub(r"\s*\([^)]*\)", "", s)
    # Collapse punctuation and whitespace
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip single trailing 's' for singular/plural matching
    # "Chronicles" -> "Chronicle", but not "Mass" -> "Ma"
    # This is imperfect (affects "James" -> "Jame") but better than
    # the old .rstrip("s") which would turn "Ross" -> "Ro"
    if s.endswith("s"):
        s = s[:-1]
    return s


def _reuse_existing(parent: Path, desired: str) -> str:
    """Check if parent contains a folder that's a near-match for desired.

    Returns the existing folder name if found, otherwise returns desired unchanged.
    """
    if not parent.is_dir():
        return desired
    # Exact match -- fast path
    if (parent / desired).exists():
        return desired
    # Normalize and compare against existing siblings
    desired_norm = _normalize_for_compare(desired)
    for existing in parent.iterdir():
        if not existing.is_dir():
            continue
        if _normalize_for_compare(existing.name) == desired_norm:
            return existing.name
    return desired


def _strip_hash(name: str) -> str:
    """Strip pipeline hash suffix (e.g., ' - a7edd490030561fb')."""
    return re.sub(r"\s+-\s+[a-f0-9]{16}$", "", name)


def _strip_label_suffix(name: str) -> str:
    """Strip label suffixes like ' - Audiobook' from dir names."""
    return re.sub(
        r"\s+-\s+(?:Audiobook|Audio|Unabridged|Abridged)$",
        "", name, flags=re.IGNORECASE,
    )


def _extract_author(name: str) -> str:
    """Extract author from a directory name, splitting off series info."""
    # Strip parenthetical suffixes: "Tad Williams (All Chaptered)" -> "Tad Williams"
    cleaned = re.sub(r"\s*\(.*?\)", "", name).strip()
    # Strip bracketed suffixes: "Name [1-5]" -> "Name"
    cleaned = re.sub(r"\s*\[.*?\]", "", cleaned).strip()
    if " - " in cleaned:
        parts = cleaned.split(" - ", 1)
        candidate = parts[0].strip()
        if not re.search(r"\d", candidate):
            return candidate
    return cleaned if cleaned else name


def _looks_like_author(name: str) -> bool:
    """Heuristic: does this directory name look like an author?"""
    lower = name.lower()
    collection_words = [
        "trilogy", "series", "saga", "collection", "volumes", "books",
        "chronicle", "chronicles",
        "standalones", "chaptered", "audiobook", "all chaptered",
        "stuff", "random", "newbooks", "output", "input", "incoming",
        "processing", "completed", "failed", "queue", "pipeline",
    ]
    for word in collection_words:
        if word in lower:
            return False
    if re.search(r"\d", name):
        return False
    if len(name) > 50:
        return False
    # Reject titles masquerading as authors -- too many words
    words = name.split()
    if len(words) > 5:
        return False
    # Reject names starting with articles (titles, not people)
    if lower.startswith(("the ", "a ", "an ")):
        return False
    # Single word is suspicious -- could be series name not author
    if len(words) == 1:
        return False
    return True


def _clean_collection_suffix(name: str) -> str:
    """Clean collection suffixes like [1-5], (All Chaptered)."""
    cleaned = re.sub(r"\s*\[.*?\]", "", name)
    cleaned = re.sub(r"\s*\(.*?\)", "", cleaned)
    return cleaned.strip()


def _clean_title_fallback(basename: str) -> str:
    """Last-resort title cleaning."""
    title = basename
    title = re.sub(r"\[\d+\]", "", title)
    title = re.sub(r"^\d+\s*[-\u2013]?\s*", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title if title else basename


def _build_result(author: str, title: str, series: str, position: str) -> dict:
    """Build clean result dict."""
    pos = position.strip()
    # Normalize leading zeros: "01" -> "1", "003" -> "3"
    if pos and pos.isdigit():
        pos = str(int(pos))
    return {
        "author": author.strip(),
        "title": title.strip().lstrip("- "),
        "series": series.strip().rstrip("- "),
        "position": pos,
    }
