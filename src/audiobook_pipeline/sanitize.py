"""Filename sanitization and book hash generation."""

import hashlib
import re
from pathlib import Path

from loguru import logger

from .models import AUDIO_EXTENSIONS

log = logger.bind(stage="sanitize")


def sanitize_filename(filename: str) -> str:
    """Sanitize a filename component (not a full path).

    Replaces unsafe chars with underscores, removes leading dots,
    collapses repeated underscores, truncates to 255 bytes preserving extension.
    """
    log.debug(f"sanitize_filename(filename='{filename}')")

    # Replace unsafe characters
    sanitized = re.sub(r'[/\\:"*?<>|;]+', '_', filename)
    # Remove leading dots/underscores
    sanitized = re.sub(r'^[._]+', '', sanitized)
    # Remove trailing dots/underscores
    sanitized = re.sub(r'[._]+$', '', sanitized)
    # Collapse repeated underscores
    sanitized = re.sub(r'__+', '_', sanitized)

    # Truncate to 255 bytes preserving extension
    original_len = len(sanitized.encode('utf-8'))
    if original_len > 255:
        p = Path(sanitized)
        ext = p.suffix
        stem = p.stem
        if ext:
            while len((stem + ext).encode('utf-8')) > 255 and stem:
                stem = stem[:-1]
            sanitized = stem + ext
        else:
            while len(sanitized.encode('utf-8')) > 255 and sanitized:
                sanitized = sanitized[:-1]
        log.debug(f"Truncated filename from {original_len} to {len(sanitized.encode('utf-8'))} bytes: '{sanitized}'")

    return sanitized


def sanitize_chapter_title(title: str) -> str:
    """Sanitize a chapter title (more permissive -- uses spaces)."""
    log.debug(f"sanitize_chapter_title(title='{title}')")
    sanitized = re.sub(r'[/\\:"*?<>|;]+', ' ', title)
    sanitized = re.sub(r'  +', ' ', sanitized)
    return sanitized.strip()


def generate_book_hash(source_path: Path) -> str:
    """Generate a 16-char hex hash for idempotency.

    For files: hash(path + file_size)
    For directories: hash(path + sorted audio file list)
    """
    log.debug(f"generate_book_hash(source_path={source_path})")

    h = hashlib.sha256()

    if source_path.is_file():
        h.update(f"{source_path}\n".encode())
        h.update(f"{source_path.stat().st_size}\n".encode())
    else:
        h.update(f"{source_path}\n".encode())
        audio_files = sorted(
            f for f in source_path.rglob("*")
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
        )
        for f in audio_files:
            h.update(f"{f}\n".encode())

    result = h.hexdigest()[:16]
    log.debug(f"Generated hash: {result}")

    return result
