"""Tests for the spine: stage ordering, resumability, and failure handling."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from audiobook_pipeline.config import EncodingSettings, Settings
from audiobook_pipeline.db import queries
from audiobook_pipeline.db.connection import connect
from audiobook_pipeline.models.book import AudioFile, BookDirectory
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.models.metadata import BookMetadata
from audiobook_pipeline.models.stage import Stage
from audiobook_pipeline.services import audible, concat, convert, organize, pipeline
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
    return RunContext(settings, db, client)


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Replace every real stage with a counter.

    The spine's job is ORDER, RESUMPTION and ERROR HANDLING. Running real
    ffmpeg here would test the stages again and make "did this stage run"
    impossible to assert directly.
    """
    calls = {"concat": 0, "convert": 0, "tag": 0, "place": 0}

    def fake_concat(book: BookDirectory, output: Path) -> Path:
        calls["concat"] += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"joined")
        return output

    def fake_convert(
        source: Path, output: Path, chapters: ChapterSet, settings: object, **_: object
    ) -> Path:
        calls["convert"] += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"converted")
        return output

    def fake_tags(path: Path, metadata: BookMetadata) -> None:
        calls["tag"] += 1

    def fake_place(source: Path, destination: Path, **_: object) -> Path:
        calls["place"] += 1
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
        return destination

    monkeypatch.setattr(concat, "concat_files", fake_concat)
    monkeypatch.setattr(convert, "convert_to_m4b", fake_convert)
    monkeypatch.setattr(pipeline, "write_tags", fake_tags)
    monkeypatch.setattr(organize, "place_book", fake_place)
    monkeypatch.setattr(
        concat,
        "build_chapters",
        lambda book: ChapterSet(
            chapters=(Chapter(start_ms=0, end_ms=MINUTE_MS, title="One"),),
            source="files",
        ),
    )
    return calls


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
    assert stubbed == {"concat": 1, "convert": 1, "tag": 1, "place": 1}
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

    assert stubbed == {"concat": 1, "convert": 1, "tag": 1, "place": 1}


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


def test_the_author_comes_from_the_level_under_the_source_root() -> None:
    """A tree filed Done/<Author>/<Book> states the author out loud."""
    root = Path("/src/Done")
    book = book_at(root / "Brian McClellan" / "Servant of the Crown", multi=True)

    assert pipeline._author_hint(book, root) == "Brian McClellan"


def test_a_single_file_book_takes_the_author_from_its_folder() -> None:
    root = Path("/src/Done")
    book = book_at(root / "Brian McClellan" / "Hrusch Avenue.mp3", multi=False)

    assert pipeline._author_hint(book, root) == "Brian McClellan"


def test_a_book_at_the_root_names_no_author() -> None:
    """Nothing sits above it, so there is nothing to infer -- not a guess."""
    root = Path("/src/Done")
    book = book_at(root / "Loose Book.mp3", multi=False)

    assert pipeline._author_hint(book, root) == ""


def test_a_book_outside_the_source_root_names_no_author() -> None:
    """The climb is bounded so an unrelated parent cannot leak in."""
    book = book_at(Path("/elsewhere/Someone/A Book"), multi=True)

    assert pipeline._author_hint(book, Path("/src/Done")) == ""


def client_returning(payload: dict[str, object]) -> httpx.Client:
    """A client whose every request resolves to one canned response."""
    return httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )


def test_a_book_the_catalogue_cannot_find_keeps_its_authors_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Unknown Author defect, at the level that actually decides it.

    Testing _author_hint alone proves nothing: the 2026-08-02 live run failed
    because _identify DISCARDED the hint it was given, and a hint-only test
    passes happily against that bug.
    """
    root = Path("/src/Done")
    book = book_at(root / "Brian McClellan" / "Servant of the Crown", multi=True)
    monkeypatch.setattr(audible, "search", lambda *a, **k: [])

    with client_returning({}) as client:
        metadata, _ = pipeline._identify(
            client, book, ChapterSet(), pipeline._author_hint(book, root)
        )

    assert metadata.author == "Brian McClellan"


def test_a_match_whose_runtime_disagrees_is_not_adopted_as_the_identity(
    monkeypatch: pytest.MonkeyPatch,
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
            client,
            book,
            ChapterSet(source="file-boundary"),
            pipeline._author_hint(book, root),
        )

    assert metadata.title != "The Powder Mage Novella Collection #1"
    assert metadata.asin == ""
    assert metadata.author == "Brian McClellan"
