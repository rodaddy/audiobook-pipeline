"""Validate current source audio and create the durable next-stage handoff."""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from audiobook_pipeline.config import EncodingSettings, PathSettings
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.lifecycle import ValidatedBook
from audiobook_pipeline.utils.ffmpeg import probe

log = logger.bind(stage="validate")


class ValidationHandoffError(ValueError):
    """The saved validation receipt cannot safely drive a resumed concat."""

    def __init__(self) -> None:
        """Build the stable error used for a changed source tree."""
        super().__init__("validated handoff no longer matches discovered audio")


def _natural_key(path: Path) -> list[int | str]:
    """Sort chapter names in reader order rather than lexical order."""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", path.name)
    ]


def _require_current_source(book: BookDirectory) -> None:
    """Refuse stale discovery entries before creating any handoff artifact."""
    if not book.path.exists():
        raise FileNotFoundError(book.path)
    if not book.path.is_dir():
        raise NotADirectoryError(book.path)
    for audio in book.files:
        if not audio.path.is_file() or audio.path.stat().st_size == 0:
            raise FileNotFoundError(audio.path)
    if book.holds_separate_books and len(book.files) > 1:
        msg = f"{book.path} contains separate books, not chapters"
        raise ValueError(msg)


def _refresh_files(book: BookDirectory) -> tuple[AudioFile, ...]:
    """Probe every input now, so a changed source cannot reuse old durations."""
    return tuple(
        AudioFile(path=path, duration_ms=probe(path).duration_ms)
        for path in sorted((audio.path for audio in book.files), key=_natural_key)
    )


def _target_bitrate(files: tuple[AudioFile, ...], settings: EncodingSettings) -> int:
    """Use the first source's bitrate as a ceiling when it is available."""
    bit_rate = probe(files[0].path).stream.bit_rate
    return (
        min(settings.max_bitrate, bit_rate // 1000)
        if bit_rate
        else settings.max_bitrate
    )


def validate_book(
    book: BookDirectory,
    paths: PathSettings,
    settings: EncodingSettings,
    book_hash: str,
) -> ValidatedBook:
    """Validate audio and persist its ordered file list for a resumable run."""
    _require_current_source(book)
    refreshed = _refresh_files(book)
    validated = book.model_copy(update={"files": refreshed})
    file_list = paths.work_dir / book_hash / "audio_files.txt"
    file_list.parent.mkdir(parents=True, exist_ok=True)
    file_list.write_text("\n".join(str(audio.path) for audio in refreshed) + "\n")
    result = ValidatedBook(
        book=validated,
        file_list=file_list,
        target_bitrate_kbps=_target_bitrate(refreshed, settings),
    )
    log.info("validated {} source file(s) for {}", len(refreshed), book.path.name)
    return result


def load_validated_book(
    book: BookDirectory, paths: PathSettings, book_hash: str
) -> BookDirectory:
    """Rebuild a completed validation stage's natural file order from its handoff."""
    handoff = paths.work_dir / book_hash / "audio_files.txt"
    if not handoff.is_file():
        raise FileNotFoundError(handoff)
    order = tuple(Path(line) for line in handoff.read_text().splitlines() if line)
    known = {audio.path: audio for audio in book.files}
    if set(order) != set(known):
        raise ValidationHandoffError
    return book.model_copy(update={"files": tuple(known[path] for path in order)})
