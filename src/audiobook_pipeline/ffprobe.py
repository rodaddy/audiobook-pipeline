"""FFprobe subprocess wrappers for audio file inspection."""

import json
import subprocess
from pathlib import Path


def _run_ffprobe(args: list[str]) -> subprocess.CompletedProcess:
    """Run ffprobe with common flags."""
    return subprocess.run(
        ["ffprobe", "-v", "error"] + args,
        capture_output=True,
        text=True,
    )


def get_duration(file: Path) -> float:
    """Get duration in seconds."""
    result = _run_ffprobe([
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file),
    ])
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"ffprobe returned empty duration for {file}")
    return float(output)


def get_bitrate(file: Path) -> int:
    """Get bitrate in bits/sec."""
    result = _run_ffprobe([
        "-show_entries", "format=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file),
    ])
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"ffprobe returned empty bitrate for {file}")
    return int(output)


def get_codec(file: Path) -> str:
    """Get audio codec name."""
    result = _run_ffprobe([
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file),
    ])
    return result.stdout.strip()


def get_channels(file: Path) -> int:
    """Get audio channel count."""
    result = _run_ffprobe([
        "-select_streams", "a:0",
        "-show_entries", "stream=channels",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file),
    ])
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"ffprobe returned empty channel count for {file}")
    return int(output)


def get_sample_rate(file: Path) -> int:
    """Get sample rate in Hz."""
    result = _run_ffprobe([
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(file),
    ])
    output = result.stdout.strip()
    if not output:
        raise ValueError(f"ffprobe returned empty sample rate for {file}")
    return int(output)


def validate_audio_file(file: Path) -> bool:
    """Check if file is a valid audio file with at least one audio stream."""
    if not file.is_file():
        return False
    result = _run_ffprobe([str(file)])
    if result.returncode != 0:
        return False
    codec = get_codec(file)
    return bool(codec)


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
        ["ffprobe", "-v", "error", "-show_entries", "format_tags",
         "-of", "json", str(file)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
        raw = data.get("format", {}).get("tags", {})
        # Normalize keys to lowercase
        return {k.lower(): v for k, v in raw.items()}
    except (json.JSONDecodeError, KeyError):
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
            return cleaned
    return ""


# Role/credit indicators that mean the rest isn't the author
_ROLE_WORDS = frozenset({
    "introduction", "narrator", "narrated", "read", "performed",
    "foreword", "afterword", "translated", "edited", "abridged",
    "unabridged", "producer", "director",
})


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
        return len(data.get("chapters", []))
    except (json.JSONDecodeError, KeyError):
        return 0
