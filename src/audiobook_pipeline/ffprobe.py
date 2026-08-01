"""FFprobe subprocess wrappers for audio file inspection."""

import json
import subprocess
from pathlib import Path

from loguru import logger

log = logger.bind(stage="ffprobe")


def _run_ffprobe(args: list[str]) -> subprocess.CompletedProcess:
    """Run ffprobe with common flags."""
    log.debug(f"Running ffprobe with args: {args}")
    return subprocess.run(
        ["ffprobe", "-v", "error"] + args,
        capture_output=True,
        text=True,
    )


def get_duration(file: Path) -> float:
    """Get duration in seconds."""
    result = _run_ffprobe(
        [
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file),
        ]
    )
    output = result.stdout.strip()
    if not output:
        log.error(f"ffprobe returned empty duration for {file}")
        raise ValueError(f"ffprobe returned empty duration for {file}")
    duration = float(output)
    log.debug(f"Duration for {file.name}: {duration:.2f}s")
    return duration


def get_bitrate(file: Path) -> int:
    """Get bitrate in bits/sec."""
    result = _run_ffprobe(
        [
            "-show_entries",
            "format=bit_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file),
        ]
    )
    output = result.stdout.strip()
    if not output:
        log.error(f"ffprobe returned empty bitrate for {file}")
        raise ValueError(f"ffprobe returned empty bitrate for {file}")
    bitrate = int(output)
    log.debug(f"Bitrate for {file.name}: {bitrate} bits/sec")
    return bitrate


def get_codec(file: Path) -> str:
    """Get audio codec name."""
    result = _run_ffprobe(
        [
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file),
        ]
    )
    codec = result.stdout.strip()
    log.debug(f"Codec for {file.name}: {codec}")
    return codec


def get_channels(file: Path) -> int:
    """Get audio channel count."""
    result = _run_ffprobe(
        [
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=channels",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file),
        ]
    )
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"ffprobe returned empty channel count for {file}")
    channels = int(output)
    log.debug(f"Channels for {file.name}: {channels}")
    return channels


def get_sample_rate(file: Path) -> int:
    """Get sample rate in Hz."""
    result = _run_ffprobe(
        [
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file),
        ]
    )
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"ffprobe returned empty sample rate for {file}")
    sample_rate = int(output)
    log.debug(f"Sample rate for {file.name}: {sample_rate} Hz")
    return sample_rate


def validate_audio_file(file: Path) -> bool:
    """Check if file is a valid audio file with at least one audio stream."""
    if not file.is_file():
        log.debug(f"File not found: {file}")
        return False
    result = _run_ffprobe([str(file)])
    if result.returncode != 0:
        log.debug(f"Invalid audio file (ffprobe failed): {file.name}")
        return False
    codec = get_codec(file)
    valid = bool(codec)
    log.debug(f"Audio validation for {file.name}: {'valid' if valid else 'invalid'}")
    return valid


def duration_to_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def get_tags(file: Path) -> dict:
    """Get format-level metadata tags from an audio file.

    Returns dict with lowercase keys. Common keys: artist, album_artist,
    title, album, genre, date, comment.
    """
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format_tags",
            "-of",
            "json",
            str(file),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
        raw = data.get("format", {}).get("tags", {})
        # Normalize keys to lowercase
        tags = {k.lower(): v for k, v in raw.items()}
        log.debug(f"Extracted {len(tags)} tags from {file.name}")
        return tags
    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"Failed to parse tags from {file.name}: {e}")
        return {}


def extract_author_from_tags(tags: dict) -> str:
    """Extract a clean author name from embedded tags.

    Checks album_artist first (more reliable), then artist.
    Strips narrator credits, role annotations, and junk.
    Returns empty string if no usable author found.
    """
    # Prefer album_artist over artist (less likely to have narrator)
    for key in ("album_artist", "artist"):
        raw = tags.get(key, "")
        if not raw:
            continue
        cleaned = _clean_author_tag(raw)
        if cleaned:
            log.debug(f"Extracted author from {key}: {cleaned}")
            return cleaned
    log.debug("No usable author found in tags")
    return ""


# Role/credit indicators that mean the rest isn't the author
_ROLE_WORDS = frozenset(
    {
        "introduction",
        "narrator",
        "narrated",
        "read",
        "performed",
        "foreword",
        "afterword",
        "translated",
        "edited",
        "abridged",
        "unabridged",
        "producer",
        "director",
    }
)


def _clean_author_tag(raw: str) -> str:
    """Clean an artist/album_artist tag into a usable author name.

    Strips:
      - "Unknown", "Various", "Various Artists" -> empty
      - "Author - introduction" -> "Author"
      - "Author, Narrator Name" -> "Author" (if second part has role words)
      - "Author; Narrator" -> "Author"
    """
    if not raw or not raw.strip():
        return ""

    name = raw.strip()

    # Reject useless placeholder values
    if name.lower() in ("unknown", "various", "various artists", "n/a", "none"):
        return ""

    # Split on " - " and check if right side is a role
    if " - " in name:
        parts = name.split(" - ", 1)
        right_lower = parts[1].strip().lower()
        # If right side starts with a role word, keep only left
        if any(right_lower.startswith(w) for w in _ROLE_WORDS):
            name = parts[0].strip()

    # Split on ", " and check if any part after first is a role
    if ", " in name:
        parts = name.split(", ")
        # Keep parts that don't look like roles or other people's names
        # Simple heuristic: if second part has a role word, drop it and after
        clean_parts = [parts[0]]
        for part in parts[1:]:
            part_lower = part.strip().lower()
            if any(w in part_lower for w in _ROLE_WORDS):
                break  # Stop at first role credit
            clean_parts.append(part)
        name = ", ".join(clean_parts)

    # Split on "; " (multiple artists) -- take first only
    if "; " in name:
        name = name.split("; ", 1)[0].strip()

    # Final validation: reject if too short or has weird chars
    if len(name) < 3:
        return ""

    return name


def get_format_name(file: Path) -> str:
    """Get container format name (e.g. 'mov,mp4,m4a,3gp,3g2,mj2')."""
    result = _run_ffprobe(
        [
            "-show_entries",
            "format=format_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(file),
        ]
    )
    fmt = result.stdout.strip()
    log.debug(f"Format for {file.name}: {fmt}")
    return fmt


def count_chapters(file: Path) -> int:
    """Count embedded chapters in an audio file."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(file)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    try:
        data = json.loads(result.stdout)
        count = len(data.get("chapters", []))
        log.debug(f"Chapter count for {file.name}: {count}")
        return count
    except (json.JSONDecodeError, KeyError):
        return 0


def read_chapters(file: Path) -> list[dict]:
    """Read embedded chapter marks as [{start_ms, end_ms, title}, ...].

    Returns [] when the file has no chapters or cannot be probed.

    Exists because a book that arrives as ONE already-chaptered M4B carries its
    chapter marks inside the container rather than as separate files, and the
    concat stage derives chapters from file boundaries alone. Measured
    2026-08-01: "The Martian.m4b" holds 160 chapters and the pipeline wrote a
    metadata file with zero, flattening an 11-hour book into one unnavigable
    block. Times are normalised to milliseconds here so callers do not have to
    reason about ffprobe's per-file timebase.
    """
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(file)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.warning(f"Could not read chapters from {file.name}")
        return []

    try:
        raw = json.loads(result.stdout).get("chapters", [])
    except (json.JSONDecodeError, KeyError):
        log.warning(f"Unparseable chapter data in {file.name}")
        return []

    chapters: list[dict] = []
    for idx, ch in enumerate(raw, start=1):
        try:
            start_ms = int(float(ch["start_time"]) * 1000)
            end_ms = int(float(ch["end_time"]) * 1000)
        except (KeyError, TypeError, ValueError):
            log.warning(f"Skipping malformed chapter {idx} in {file.name}")
            continue
        if end_ms <= start_ms:
            log.warning(f"Skipping zero-length chapter {idx} in {file.name}")
            continue
        title = (ch.get("tags") or {}).get("title", "") or f"Chapter {idx}"
        chapters.append({"start_ms": start_ms, "end_ms": end_ms, "title": title})

    log.debug(f"Read {len(chapters)} chapters from {file.name}")
    return chapters


# A finished audiobook is hours long; a chapter of one is not. Books shorter
# than this exist (novellas, kids' titles) but are rare, and the cost of the
# two errors is not symmetric -- see are_separate_books below.
SEPARATE_BOOK_MIN_DURATION = 2 * 3600.0


def are_separate_books(files: list[Path]) -> bool:
    """True when a set of M4B files are whole books, not chapters of one book.

    Multiple .m4b files in one directory are ambiguous: either a chaptered
    book that needs concatenating, or a folder holding a whole series. Counting
    them cannot tell the difference. Duration can.

    Measured 2026-08-01 on the real trees:
        40 Legend of Drizzt books   10.19-15.71h each (median 12.75h)
         8 Noobtown books            9.07-17.39h each
        19 Promise of Blood chapters  0.95-1.01h each

    Median, not mean or min: an intro/outro track of a few minutes must not
    drag a set of real books below the line, and one long file must not lift a
    set of chapters above it.

    Fails SAFE. On any probe failure this returns True (treat as separate
    books), because the two mistakes are not equally costly: leaving books
    unconcatenated is visible and reversible, while concatenating a 40-book
    series produces one 510-hour file and destroys the boundaries -- which is
    exactly what the previous `count > 1` rule planned to do.
    """
    if len(files) < 2:
        return True

    durations = []
    for f in files:
        try:
            d = get_duration(f)
        except Exception as exc:  # noqa: BLE001 -- fail safe, see docstring
            log.warning(f"Could not probe {f.name}, treating as separate book: {exc}")
            return True
        if d <= 0:
            log.warning(f"Zero/unknown duration for {f.name}, treating as separate")
            return True
        durations.append(d)

    durations.sort()
    mid = len(durations) // 2
    median = (
        durations[mid]
        if len(durations) % 2
        else (durations[mid - 1] + durations[mid]) / 2
    )
    separate = median >= SEPARATE_BOOK_MIN_DURATION
    log.debug(
        f"{len(files)} m4b files, median {median / 3600:.2f}h -> "
        f"{'separate books' if separate else 'chapters of one book'}"
    )
    return separate
