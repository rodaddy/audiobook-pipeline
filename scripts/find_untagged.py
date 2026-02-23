#!/usr/bin/env bash
# /// script
# requires-python = ">=3.12"
# ///
"""Find m4b files in the audiobook library with missing artist tags.

Scans the NFS audiobook library and uses ffprobe to check each m4b file
for an artist tag. Files with empty/missing artist are written to the
report file for batch re-tagging.

Usage:
    uv run scripts/find_untagged.py [--library PATH] [--output PATH]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_LIBRARY = Path("/Volumes/media_files/AudioBooks")
DEFAULT_OUTPUT = Path(".reports/untagged-m4b-files.txt")


def get_artist_tag(filepath: Path) -> str:
    """Extract artist tag from an m4b file via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_entries",
                "format_tags=artist",
                str(filepath),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout)
        return data.get("format", {}).get("tags", {}).get("artist", "")
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def scan_library(library: Path) -> list[Path]:
    """Find all m4b files with missing artist tags."""
    untagged: list[Path] = []
    m4b_files = sorted(library.rglob("*.m4b"))
    total = len(m4b_files)

    print(f"Scanning {total} m4b files in {library}...")

    for i, filepath in enumerate(m4b_files, 1):
        artist = get_artist_tag(filepath)
        if not artist.strip():
            untagged.append(filepath)
            print(f"  [{i}/{total}] UNTAGGED: {filepath.relative_to(library)}")
        elif i % 50 == 0:
            print(f"  [{i}/{total}] scanned...")

    return untagged


def main() -> None:
    parser = argparse.ArgumentParser(description="Find untagged m4b files")
    parser.add_argument(
        "--library",
        type=Path,
        default=DEFAULT_LIBRARY,
        help=f"Library path (default: {DEFAULT_LIBRARY})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output report path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if not args.library.exists():
        print(f"ERROR: Library path does not exist: {args.library}", file=sys.stderr)
        sys.exit(1)

    untagged = scan_library(args.library)

    # Write report
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        for filepath in untagged:
            f.write(f"{filepath}\n")

    print(
        f"\nFound {len(untagged)} untagged files out of {len(list(args.library.rglob('*.m4b')))} total"
    )
    print(f"Report written to: {args.output}")


if __name__ == "__main__":
    main()
