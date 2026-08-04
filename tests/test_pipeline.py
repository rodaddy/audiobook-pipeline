"""Tests for the spine: stage ordering, resumability, and failure handling."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from mutagen import MutagenError

from audiobook_pipeline.config import EncodingSettings, Settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.db.rows import BookRow
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.models.lifecycle import (
    ArchivedSource,
    CleanupResult,
    ValidatedBook,
)
from audiobook_pipeline.models.metadata import BookMetadata, CoverArt
from audiobook_pipeline.models.stage import (
    PipelineLevel,
    PipelineMode,
    Stage,
    StageStatus,
)
from audiobook_pipeline.services import (
    archive,
    audible,
    cleanup,
    concat,
    convert,
    organize,
    parse,
    pipeline,
    validate,
)
from audiobook_pipeline.services.pipeline import RunContext, book_hash, process_book
from audiobook_pipeline.utils.ffmpeg import FfmpegError

MINUTE_MS = 60 * 1000


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """An initialized database."""
    with connect(tmp_path / "pipeline.db") as conn:
        yield conn


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings pointed at the test's own directories.

    Env vars are cleared rather than passing ``_env_file=None``: a real
    AUDIOBOOK_* in the developer's shell outranks a file, so without this a
    test could pass or fail depending on whose machine it runs on.
    """
    for key in list(os.environ):
        if key.startswith("AUDIOBOOK_"):
            monkeypatch.delenv(key, raising=False)

    config = Settings()
    config.paths.work_dir = tmp_path / "work"
    config.paths.library_dir = tmp_path / "library"
    return config


@pytest.fixture
def context(db: sqlite3.Connection, settings: Settings) -> RunContext:
    """A run context with a client that never reaches the network."""
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(404, json={}))
    )
    return RunContext(config=settings, conn=db, client=client)


class StageFakes:
    """Lightweight stand-ins that expose the pipeline's stage calls."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {
            "validate": 0,
            "concat": 0,
            "convert": 0,
            "tag": 0,
            "place": 0,
            "archive": 0,
            "cleanup": 0,
        }

    def validate_book(
        self, book: BookDirectory, _: object, __: object, book_hash: str
    ) -> ValidatedBook:
        self.calls["validate"] += 1
        handoff = book.path / "handoff" / f"{book_hash}.txt"
        handoff.parent.mkdir(parents=True, exist_ok=True)
        handoff.write_text("ready")
        return ValidatedBook(book=book, file_list=handoff, target_bitrate_kbps=64)

    def concat_files(self, _: BookDirectory, output: Path) -> Path:
        self.calls["concat"] += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"joined")
        return output

    def convert_to_m4b(
        self, _: Path, output: Path, __: ChapterSet, ___: object, **_kwargs: object
    ) -> Path:
        self.calls["convert"] += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"converted")
        return output

    def write_tags(
        self, _: Path, __: BookMetadata, *, cover: CoverArt | None = None
    ) -> None:
        self.calls["tag"] += 1

    def place_book(self, source: Path, destination: Path, **_: object) -> Path:
        self.calls["place"] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return destination

    def archive_source(
        self, source: Path, _: Path, root: Path, **_kwargs: object
    ) -> ArchivedSource:
        self.calls["archive"] += 1
        return ArchivedSource(
            source_path=source,
            archive_path=root / source.name,
            original_count=1,
        )

    def cleanup_work_dir(
        self, work_root: Path, book_hash: str, *, enabled: bool, dry_run: bool
    ) -> CleanupResult:
        self.calls["cleanup"] += 1
        return CleanupResult(
            work_dir=work_root / book_hash, removed=False, attempted=True
        )

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(concat, "concat_files", self.concat_files)
        monkeypatch.setattr(convert, "convert_to_m4b", self.convert_to_m4b)
        monkeypatch.setattr(validate, "validate_book", self.validate_book)
        monkeypatch.setattr(archive, "archive_source", self.archive_source)
        monkeypatch.setattr(cleanup, "cleanup_work_dir", self.cleanup_work_dir)
        monkeypatch.setattr(pipeline, "write_tags", self.write_tags)
        monkeypatch.setattr(organize, "place_book", self.place_book)
        monkeypatch.setattr(concat, "build_chapters", self.build_chapters)

    @staticmethod
    def build_chapters(_: BookDirectory) -> ChapterSet:
        return ChapterSet(
            chapters=(Chapter(start_ms=0, end_ms=MINUTE_MS, title="One"),),
            source="files",
        )


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace external stages with counters so the spine remains observable."""
    fakes = StageFakes()
    fakes.install(monkeypatch)
    return fakes.calls


def make_book(tmp_path: Path, *names: str, minutes: int = 45) -> BookDirectory:
    """A book on disk with declared durations."""
    folder = tmp_path / "source" / "Some Book"
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for name in names:
        path = folder / name
        path.write_bytes(b"audio")
        files.append(AudioFile(path=path, duration_ms=minutes * MINUTE_MS))
    return BookDirectory(path=folder, files=tuple(files))


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_hash_is_stable_for_the_same_book(tmp_path: Path) -> None:
    book = make_book(tmp_path, "a.mp3", "b.mp3")
    assert book_hash(book) == book_hash(book)


def test_hash_changes_when_the_audio_changes(tmp_path: Path) -> None:
    """A re-rip at the same path is a different book, not one already done."""
    short = make_book(tmp_path, "a.mp3", minutes=45)
    longer = make_book(tmp_path, "a.mp3", minutes=50)

    assert book_hash(short) != book_hash(longer)


def test_split_books_get_distinct_hashes(tmp_path: Path) -> None:
    """19 novels sharing a folder must not collapse into one identity."""
    folder = tmp_path / "Collection"
    folder.mkdir(parents=True)
    hashes = set()
    for index in range(3):
        path = folder / f"novel{index}.m4b"
        path.write_bytes(b"x")
        hashes.add(
            book_hash(
                BookDirectory(
                    path=folder,
                    files=(AudioFile(path=path, duration_ms=12 * 60 * MINUTE_MS),),
                )
            )
        )

    assert len(hashes) == 3


# ---------------------------------------------------------------------------
# a full run
# ---------------------------------------------------------------------------


def test_every_stage_runs_and_is_recorded(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    book = make_book(tmp_path, "01.mp3", "02.mp3")

    row = process_book(book, context)

    assert row.status == "completed"
    assert stubbed == {
        "validate": 1,
        "concat": 1,
        "convert": 1,
        "tag": 1,
        "place": 1,
        "archive": 1,
        "cleanup": 1,
    }
    done = queries.completed_stages(context.conn, row.book_hash)
    assert {s.value for s in Stage} <= done


def test_a_single_file_book_is_not_concatenated(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    process_book(make_book(tmp_path, "solo.mp3"), context)

    assert stubbed["concat"] == 0
    assert stubbed["convert"] == 1


def test_the_record_carries_what_the_run_learned(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    row = process_book(make_book(tmp_path, "01.mp3", "02.mp3"), context)

    assert row.chapter_count == 1
    assert row.chapter_source == "files"
    assert row.parsed_title  # falls back to the folder name with no catalogue


# ---------------------------------------------------------------------------
# resumability -- the defect the live re-run found
# ---------------------------------------------------------------------------


def test_a_second_run_does_no_work(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    """Measured 2026-08-02: this re-ran everything and duplicated the library."""
    book = make_book(tmp_path, "01.mp3", "02.mp3")

    process_book(book, context)
    process_book(book, context)

    assert stubbed["validate"] == 1
    assert stubbed["archive"] == 1
    assert stubbed["cleanup"] == 1


def test_simple_leaves_the_m4b_outside_the_library_and_source_archive(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    context.config.level = PipelineLevel.SIMPLE

    process_book(make_book(tmp_path, "solo.mp3"), context)

    assert stubbed["place"] == 1
    assert stubbed["archive"] == 0
    assert stubbed["cleanup"] == 1
    assert not context.config.paths.library_dir.exists()


def test_validate_failure_blocks_archive_and_cleanup(
    tmp_path: Path,
    context: RunContext,
    stubbed: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def invalid(*_: object) -> ValidatedBook:
        raise FfmpegError("ffprobe", "source is corrupt")

    monkeypatch.setattr(validate, "validate_book", invalid)

    row = process_book(make_book(tmp_path, "solo.mp3"), context)

    assert row.status == "failed"
    assert stubbed["archive"] == 0
    assert stubbed["cleanup"] == 0


def test_archive_failure_is_recorded_without_running_cleanup(
    tmp_path: Path,
    context: RunContext,
    stubbed: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused archive preserves its incomplete stage instead of cleaning evidence."""

    def refuse(
        _: Path, __: Path, archive_root: Path, **_kwargs: object
    ) -> ArchivedSource:
        raise archive.ArchiveError(archive_root)

    monkeypatch.setattr(archive, "archive_source", refuse)

    row = process_book(make_book(tmp_path, "solo.mp3"), context)

    assert row.status == "failed"
    assert stubbed["cleanup"] == 0
    done = queries.completed_stages(context.conn, row.book_hash)
    assert Stage.ARCHIVE.value not in done
    assert Stage.CLEANUP.value not in done


def test_the_library_gains_exactly_one_file_across_repeated_runs(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    book = make_book(tmp_path, "01.mp3", "02.mp3")

    for _ in range(3):
        process_book(book, context)

    assert len(list((tmp_path / "library").rglob("*.m4b"))) == 1


def test_stage_rows_survive_the_upsert_at_the_start_of_a_run(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    """The root cause: REPLACE cascaded the stage rows away."""
    book = make_book(tmp_path, "01.mp3", "02.mp3")
    process_book(book, context)
    before = queries.completed_stages(context.conn, book_hash(book))

    process_book(book, context)

    assert queries.completed_stages(context.conn, book_hash(book)) == before


def test_requested_mode_replaces_a_persisted_mode_plan(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    """A new command's requested mode, not an old row, chooses its stages."""
    book = make_book(tmp_path, "solo.mp3")

    process_book(book, context, mode=PipelineMode.METADATA)
    process_book(book, context, mode=PipelineMode.CONVERT)

    row = queries.get_book(context.conn, book_hash(book))
    assert row is not None
    assert row.mode == PipelineMode.CONVERT.value
    assert stubbed["validate"] == 1
    assert stubbed["convert"] == 1
    assert stubbed["place"] == 1


def test_archive_retry_reuses_the_organized_handoff(
    tmp_path: Path,
    context: RunContext,
    stubbed: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed archive cannot force an encode from the cleaned scratch tree."""
    book = make_book(tmp_path, "solo.mp3")

    def refuse(*_: object, **__: object) -> ArchivedSource:
        raise archive.ArchiveError(context.config.paths.archive_dir)

    with monkeypatch.context() as patch:
        patch.setattr(archive, "archive_source", refuse)
        assert process_book(book, context).status == "failed"

    assert process_book(book, context).status == "completed"
    assert stubbed["convert"] == 1
    assert stubbed["tag"] == 1
    assert stubbed["place"] == 1


def test_organize_mode_does_not_rewrite_tags_or_metadata(
    tmp_path: Path, context: RunContext, stubbed: dict[str, int]
) -> None:
    """Organizing a known M4B is a placement operation, not enrichment."""
    book = make_book(tmp_path, "solo.m4b")
    queries.upsert_book(
        context.conn,
        BookRow(
            book_hash=book_hash(book),
            source_path=str(book.identity_path),
            mode=PipelineMode.ORGANIZE.value,
            parsed_title="Known Book",
            parsed_author="Known Author",
        ),
    )

    row = process_book(book, context, mode=PipelineMode.ORGANIZE)

    assert row.status == "completed"
    assert stubbed["tag"] == 0
    assert stubbed["place"] == 1
    assert {
        stage.stage: stage.status
        for stage in queries.get_stages(context.conn, row.book_hash)
    }[Stage.METADATA.value] == StageStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# failure
# ---------------------------------------------------------------------------


def test_a_failed_book_is_recorded_and_does_not_raise(
    tmp_path: Path,
    context: RunContext,
    stubbed: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One unreadable source out of 700 must not stop the run."""

    def explode(*_: object, **__: object) -> Path:
        raise FfmpegError("ffmpeg", "encoder blew up")

    monkeypatch.setattr(convert, "convert_to_m4b", explode)

    row = process_book(make_book(tmp_path, "01.mp3", "02.mp3"), context)

    assert row.status == "failed"
    assert "encoder blew up" in (row.error_message or "")


def test_a_tagging_mutagen_error_is_a_recorded_book_failure(
    tmp_path: Path,
    context: RunContext,
    stubbed: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tag parsing failures have the same one-book boundary as ffmpeg failures."""
    monkeypatch.setattr(
        pipeline,
        "write_tags",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(MutagenError("bad tag")),
    )

    row = process_book(make_book(tmp_path, "solo.mp3"), context)

    assert row.status == "failed"
    assert row.error_message == "bad tag"
    assert Stage.METADATA.value not in queries.completed_stages(
        context.conn, row.book_hash
    )


def test_a_failed_book_can_be_retried(
    tmp_path: Path,
    context: RunContext,
    stubbed: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure must not record the stages that never ran."""
    book = make_book(tmp_path, "01.mp3", "02.mp3")

    calls = {"n": 0}
    real = convert.convert_to_m4b

    def fail_once(
        source: Path,
        output: Path,
        chapters: ChapterSet,
        settings: EncodingSettings,
        *,
        work_dir: Path | None = None,
    ) -> Path:
        calls["n"] += 1
        if calls["n"] == 1:
            raise FfmpegError("ffmpeg", "transient")
        return real(source, output, chapters, settings, work_dir=work_dir)

    monkeypatch.setattr(convert, "convert_to_m4b", fail_once)

    assert process_book(book, context).status == "failed"
    assert process_book(book, context).status == "completed"
    assert stubbed["place"] == 1


# ---------------------------------------------------------------------------
# the author the source tree already knows
# ---------------------------------------------------------------------------


def book_at(path: Path, *, multi: bool) -> BookDirectory:
    """A discovered book rooted at ``path``.

    ``is_multi_file_book`` is DERIVED from the file count, so the shape is set
    by how many files the book has rather than by a flag.
    """
    if multi:
        files = tuple(
            AudioFile(path=path / f"{n:02d}.mp3", duration_ms=60_000) for n in (1, 2)
        )
        return BookDirectory(path=path, files=files)
    return BookDirectory(
        path=path.parent,
        files=(AudioFile(path=path, duration_ms=60_000),),
    )


def client_returning(payload: dict[str, object]) -> httpx.Client:
    """A client whose every request resolves to one canned response."""
    return httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )


def test_a_book_the_catalogue_cannot_find_keeps_its_authors_name(
    monkeypatch: pytest.MonkeyPatch, context: RunContext
) -> None:
    """The Unknown Author defect, at the level that actually decides it.

    Testing the parser alone proves nothing: the 2026-08-02 live run failed
    because _identify DISCARDED what it was given, and a parser-only test
    passes happily against that bug.
    """
    root = Path("/src/Done")
    book = book_at(root / "Brian McClellan" / "Servant of the Crown", multi=True)
    monkeypatch.setattr(audible, "search", lambda *a, **k: [])

    with client_returning({}) as client:
        metadata, _ = pipeline._identify(
            context.model_copy(update={"client": client}),
            book,
            ChapterSet(),
            parse.parse_path(book.identity_path, root),
        )

    assert metadata.author == "Brian McClellan"


def test_a_match_whose_runtime_disagrees_is_not_adopted_as_the_identity(
    monkeypatch: pytest.MonkeyPatch, context: RunContext
) -> None:
    """A wrong ASIN's title and series are wrong too, not just its chapters.

    The live run wrote a 19-hour "Promise of Blood" into the library as the
    10.8-hour "Powder Mage Novella Collection #1" -- the mismatch was detected
    and then used anyway.
    """
    root = Path("/src/Done")
    book = book_at(root / "Brian McClellan" / "Promise of Blood", multi=True)
    wrong = BookMetadata(
        title="The Powder Mage Novella Collection #1",
        author="Brian McClellan",
        asin="B01LYLUQ2U",
    )
    monkeypatch.setattr(audible, "search", lambda *a, **k: [wrong])

    # Audnexus reports a runtime nothing like the local audio.
    with client_returning({
        "runtimeLengthMs": 10 * 3_600_000,
        "chapters": [],
    }) as client:
        metadata, _ = pipeline._identify(
            context.model_copy(update={"client": client}),
            book,
            ChapterSet(source="file-boundary"),
            parse.parse_path(book.identity_path, root),
        )

    assert metadata.title != "The Powder Mage Novella Collection #1"
    assert metadata.asin == ""
    assert metadata.author == "Brian McClellan"
