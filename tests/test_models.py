"""Tests for the model layer: what each shape accepts, and what it refuses.

The refusals carry more weight than the acceptances. Every model here replaces
a `dict[str, Any]` that accepted anything, so the value is the malformed input
that now fails at construction naming a field, instead of surfacing three
frames later as a KeyError or a silently wrong number.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from audiobook_pipeline.models.book import (
    SEPARATE_BOOK_MIN_MS,
    SOURCE_EXTENSIONS,
    AudioFile,
    BookDirectory,
)
from audiobook_pipeline.models.chapter import Chapter, ChapterSet
from audiobook_pipeline.models.media import AudioStream, ProbeResult
from audiobook_pipeline.models.metadata import (
    AudibleResult,
    AudnexusChapter,
    AudnexusChapters,
    BookMetadata,
)
from audiobook_pipeline.models.stage import (
    PRE_COMPLETED_STAGES,
    STAGE_ORDER,
    ErrorCategory,
    PipelineLevel,
    PipelineMode,
    Stage,
    StageResult,
    StageStatus,
    stages_for,
)

HOUR_MS = 3_600_000


def _chapter(start_ms: int, end_ms: int, title: str = "Chapter") -> Chapter:
    return Chapter(start_ms=start_ms, end_ms=end_ms, title=title)


class TestChapter:
    def test_duration_derives_from_bounds(self) -> None:
        assert _chapter(0, 30_467).duration_ms == 30_467

    def test_rejects_end_before_start(self) -> None:
        """A refusal, not a repair. Clamping writes a plausible wrong table."""
        with pytest.raises(ValidationError, match="must end after it starts"):
            Chapter(start_ms=5000, end_ms=1000, title="Backwards")

    def test_rejects_zero_length(self) -> None:
        with pytest.raises(ValidationError, match="must end after it starts"):
            Chapter(start_ms=5000, end_ms=5000, title="Instant")

    def test_rejects_empty_title(self) -> None:
        """A player showing a blank entry is worse than a generic label."""
        with pytest.raises(ValidationError):
            Chapter(start_ms=0, end_ms=1000, title="")

    def test_rejects_negative_start(self) -> None:
        with pytest.raises(ValidationError):
            Chapter(start_ms=-1, end_ms=1000, title="Before time")

    def test_is_immutable(self) -> None:
        chapter = _chapter(0, 1000)
        with pytest.raises(ValidationError):
            chapter.start_ms = 500


class TestChapterSet:
    def test_empty_set_is_explicit(self) -> None:
        """Defect 10: 160 chapters became 0 and nothing said so.

        An empty table is now a distinct inspectable state rather than an
        outcome that reads identically to success.
        """
        empty = ChapterSet()
        assert empty.is_empty
        assert empty.total_duration_ms == 0

    def test_total_duration_is_last_chapter_end(self) -> None:
        chapters = ChapterSet(
            chapters=(_chapter(0, 30_467, "A"), _chapter(30_467, 60_000, "B"))
        )
        assert chapters.total_duration_ms == 60_000
        assert not chapters.is_empty

    def test_rejects_overlapping_chapters(self) -> None:
        """Players disagree about overlaps, so the bug presents as 'weird'."""
        with pytest.raises(ValidationError, match="before"):
            ChapterSet(
                chapters=(_chapter(0, 30_000, "A"), _chapter(20_000, 40_000, "B"))
            )

    def test_rejects_out_of_order_chapters(self) -> None:
        """Deliberately not sorted -- sorting hides a bad offset calculation."""
        with pytest.raises(ValidationError, match="before"):
            ChapterSet(
                chapters=(_chapter(30_000, 60_000, "B"), _chapter(0, 30_000, "A"))
            )

    def test_abutting_chapters_are_valid(self) -> None:
        """End == next start is normal: chapters abut, they do not overlap."""
        chapters = ChapterSet(
            chapters=(_chapter(0, 30_000, "A"), _chapter(30_000, 60_000, "B"))
        )
        assert len(chapters.chapters) == 2


class TestAudioStream:
    def test_identical_streams_are_compatible(self) -> None:
        stream = AudioStream(codec="aac", sample_rate=44100, channels=2)
        assert stream.is_compatible_with(stream)

    def test_differing_sample_rate_is_incompatible(self) -> None:
        """Stream-copying mismatched rates plays as noise after the boundary."""
        a = AudioStream(codec="aac", sample_rate=44100, channels=2)
        b = AudioStream(codec="aac", sample_rate=22050, channels=2)
        assert not a.is_compatible_with(b)

    def test_differing_codec_is_incompatible(self) -> None:
        a = AudioStream(codec="aac", sample_rate=44100, channels=2)
        b = AudioStream(codec="mp3", sample_rate=44100, channels=2)
        assert not a.is_compatible_with(b)

    def test_differing_bitrate_is_still_compatible(self) -> None:
        """VBR varies per file and does not affect whether streams join."""
        a = AudioStream(codec="aac", sample_rate=44100, channels=2, bit_rate=64000)
        b = AudioStream(codec="aac", sample_rate=44100, channels=2, bit_rate=128000)
        assert a.is_compatible_with(b)

    def test_bitrate_is_optional_not_zero(self) -> None:
        """None, not 0 -- a 0 would read as 'silent' to anything doing math."""
        stream = AudioStream(codec="flac", sample_rate=44100, channels=2)
        assert stream.bit_rate is None


class TestProbeResult:
    def test_carries_chapters_and_duration(self) -> None:
        probe = ProbeResult(
            path=Path("book.m4b"),
            duration_ms=10 * HOUR_MS,
            stream=AudioStream(codec="aac", sample_rate=44100, channels=2),
            chapters=ChapterSet(chapters=(_chapter(0, 1000, "One"),)),
        )
        assert probe.duration_hours == pytest.approx(10.0)
        assert not probe.chapters.is_empty

    def test_rejects_zero_duration(self) -> None:
        with pytest.raises(ValidationError):
            ProbeResult(
                path=Path("empty.m4b"),
                duration_ms=0,
                stream=AudioStream(codec="aac", sample_rate=44100, channels=1),
            )


class TestBookClassification:
    def test_forty_novels_are_separate_books(self) -> None:
        """Defect 4: this folder was queued as ONE 510-hour concat."""
        novels = BookDirectory(
            path=Path("Salvatore"),
            files=tuple(
                AudioFile(path=Path(f"book{i}.m4b"), duration_ms=12 * HOUR_MS)
                for i in range(40)
            ),
        )
        assert novels.holds_separate_books
        assert not novels.is_multi_file_book

    def test_hour_long_chapters_are_one_book(self) -> None:
        chapters = BookDirectory(
            path=Path("The Martian"),
            files=tuple(
                AudioFile(path=Path(f"ch{i:02d}.mp3"), duration_ms=HOUR_MS)
                for i in range(12)
            ),
        )
        assert not chapters.holds_separate_books
        assert chapters.is_multi_file_book

    def test_median_ignores_a_short_outlier(self) -> None:
        """A 3-minute credits track must not drag the verdict.

        A mean would move; the median does not shift at all.
        """
        files = [
            AudioFile(path=Path(f"book{i}.m4b"), duration_ms=12 * HOUR_MS)
            for i in range(11)
        ]
        files.append(AudioFile(path=Path("credits.mp3"), duration_ms=180_000))
        assert BookDirectory(path=Path("x"), files=tuple(files)).holds_separate_books

    def test_median_ignores_a_long_outlier(self) -> None:
        """One 12-hour omnibus among real chapters must not flip the verdict."""
        files = [
            AudioFile(path=Path(f"ch{i:02d}.mp3"), duration_ms=HOUR_MS)
            for i in range(11)
        ]
        files.append(AudioFile(path=Path("omnibus.m4b"), duration_ms=12 * HOUR_MS))
        directory = BookDirectory(path=Path("x"), files=tuple(files))
        assert not directory.holds_separate_books

    def test_single_file_is_never_a_concat_candidate(self) -> None:
        one = BookDirectory(
            path=Path("x"),
            files=(AudioFile(path=Path("book.m4b"), duration_ms=HOUR_MS),),
        )
        assert one.holds_separate_books
        assert not one.is_multi_file_book

    def test_boundary_duration_counts_as_a_book(self) -> None:
        """Exactly at the threshold is a book, per the >= in the property."""
        at_boundary = BookDirectory(
            path=Path("x"),
            files=tuple(
                AudioFile(path=Path(f"b{i}.m4b"), duration_ms=SEPARATE_BOOK_MIN_MS)
                for i in range(3)
            ),
        )
        assert at_boundary.holds_separate_books

    def test_empty_directory_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BookDirectory(path=Path("x"), files=())


class TestSourceExtensions:
    def test_covers_the_formats_users_actually_have(self) -> None:
        """Defect 1: filtering to .m4b alone made 76 source files invisible."""
        for suffix in (".mp3", ".flac", ".m4a", ".m4b", ".ogg", ".wma", ".wav"):
            assert suffix in SOURCE_EXTENSIONS

    def test_excludes_non_audio(self) -> None:
        assert ".txt" not in SOURCE_EXTENSIONS
        assert ".pdf" not in SOURCE_EXTENSIONS
        assert ".jpg" not in SOURCE_EXTENSIONS


class TestAudnexusTranslation:
    def test_api_field_names_are_accepted(self) -> None:
        """The external vocabulary stops at this model."""
        chapter = AudnexusChapter.model_validate({
            "startOffsetMs": 0,
            "lengthMs": 30_467,
            "title": "Opening Credits",
        })
        assert chapter.end_ms == 30_467
        assert chapter.to_chapter().title == "Opening Credits"

    def test_length_converts_to_end_offset(self) -> None:
        chapter = AudnexusChapter.model_validate({
            "startOffsetMs": 30_467,
            "lengthMs": 1000,
            "title": "One",
        })
        assert chapter.end_ms == 31_467

    def test_unknown_api_fields_are_ignored_not_fatal(self) -> None:
        """A third-party service adding a field is not our error to raise."""
        chapter = AudnexusChapter.model_validate({
            "startOffsetMs": 0,
            "lengthMs": 1000,
            "title": "A",
            "newField": "x",
        })
        assert chapter.length_ms == 1000

    def test_converts_a_whole_table(self) -> None:
        table = AudnexusChapters.model_validate({
            "chapters": [
                {"startOffsetMs": 0, "lengthMs": 30_000, "title": "A"},
                {"startOffsetMs": 30_000, "lengthMs": 30_000, "title": "B"},
            ],
            "runtimeLengthMs": 60_000,
            "isAccurate": True,
        })
        converted = table.to_chapter_set()
        assert len(converted.chapters) == 2
        assert converted.source == "audnexus"
        assert converted.total_duration_ms == 60_000


class TestWrongEditionGuard:
    def test_matching_duration_is_accepted(self) -> None:
        table = AudnexusChapters(runtimeLengthMs=10 * HOUR_MS)
        assert table.matches_duration(
            10 * HOUR_MS, tolerance_pct=1.0, tolerance_ms=600_000
        )

    def test_abridged_edition_is_rejected(self) -> None:
        """A 5-hour abridgement against 10 hours of audio: same ASIN shape."""
        table = AudnexusChapters(runtimeLengthMs=5 * HOUR_MS)
        assert not table.matches_duration(
            10 * HOUR_MS, tolerance_pct=1.0, tolerance_ms=600_000
        )

    def test_flat_floor_saves_a_short_book(self) -> None:
        """1% of a 90-minute novella is 54s -- too tight for a publisher intro."""
        ninety_min = 90 * 60 * 1000
        table = AudnexusChapters(runtimeLengthMs=ninety_min)
        assert table.matches_duration(
            ninety_min + 60_000, tolerance_pct=1.0, tolerance_ms=600_000
        )

    def test_missing_runtime_fails_closed(self) -> None:
        """No runtime means no check is possible, so the table is not trusted."""
        assert not AudnexusChapters().matches_duration(
            HOUR_MS, tolerance_pct=1.0, tolerance_ms=600_000
        )


class TestAudibleResult:
    def test_primary_author_is_the_first(self) -> None:
        """Defect 5: the author was never passed to the scorer at all."""
        result = AudibleResult(
            asin="B00TEST", title="Timeless", authors=("R.A. Salvatore", "Other")
        )
        assert result.primary_author == "R.A. Salvatore"

    def test_missing_author_is_empty_not_an_error(self) -> None:
        result = AudibleResult(asin="B00TEST", title="Timeless")
        assert result.primary_author == ""

    def test_rejects_missing_asin(self) -> None:
        with pytest.raises(ValidationError):
            AudibleResult(asin="", title="Timeless")


class TestBookMetadata:
    def test_series_book_sorts_by_position(self) -> None:
        """What makes a series list in reading order rather than A-Z."""
        meta = BookMetadata(title="Timeless", series="Generations", series_position="1")
        assert meta.sort_title == "Generations 1 - Timeless"
        assert meta.has_series

    def test_standalone_sorts_by_title(self) -> None:
        meta = BookMetadata(title="The Martian")
        assert meta.sort_title == "The Martian"
        assert not meta.has_series

    def test_series_without_position_falls_back_to_title(self) -> None:
        meta = BookMetadata(title="Timeless", series="Generations")
        assert meta.sort_title == "Timeless"

    def test_rejects_unknown_field(self) -> None:
        """extra='forbid' on OUR model: a typo here is our bug, not an API's."""
        with pytest.raises(ValidationError):
            BookMetadata(title="X", authr="typo")  # type: ignore[call-arg]


class TestStageOrder:
    def test_every_mode_has_an_order(self) -> None:
        """A missing entry means a mode that silently runs nothing."""
        for mode in PipelineMode:
            assert mode in STAGE_ORDER
            assert STAGE_ORDER[mode], f"{mode} has an empty stage order"

    def test_convert_runs_every_stage(self) -> None:
        assert len(STAGE_ORDER[PipelineMode.CONVERT]) == len(Stage)

    def test_enrich_skips_the_expensive_early_stages(self) -> None:
        """Enrich operates on already-converted audio; re-encoding is the cost."""
        stages = STAGE_ORDER[PipelineMode.ENRICH]
        assert Stage.VALIDATE not in stages
        assert Stage.CONCAT not in stages
        assert Stage.CONVERT not in stages
        assert Stage.ASIN in stages

    def test_organize_only_places_an_already_tagged_book(self) -> None:
        assert STAGE_ORDER[PipelineMode.ORGANIZE] == (Stage.ORGANIZE,)

    def test_stages_appear_in_canonical_order(self) -> None:
        """A mode's order must never contradict the canonical stage sequence."""
        canonical = list(Stage)
        for mode, stages in STAGE_ORDER.items():
            positions = [canonical.index(stage) for stage in stages]
            assert positions == sorted(positions), f"{mode} runs stages out of order"

    def test_simple_only_removes_filing_and_source_archival(self) -> None:
        stages = stages_for(PipelineMode.CONVERT, PipelineLevel.SIMPLE)
        assert Stage.ORGANIZE not in stages
        assert Stage.ARCHIVE not in stages
        assert Stage.CLEANUP in stages

    def test_ai_and_full_have_identical_lifecycle_stages(self) -> None:
        for mode in PipelineMode:
            assert stages_for(mode, PipelineLevel.AI) == stages_for(
                mode, PipelineLevel.FULL
            )


class TestPreCompletedStages:
    def test_convert_pre_completes_nothing(self) -> None:
        assert PipelineMode.CONVERT not in PRE_COMPLETED_STAGES

    def test_modes_that_skip_conversion_record_it_as_done(self) -> None:
        """Recorded, not merely skipped.

        A resumed run has to tell "never needed to happen" apart from "has not
        happened yet" -- otherwise it re-converts a finished book.
        """
        early = {Stage.VALIDATE, Stage.CONCAT, Stage.CONVERT}
        for mode in (PipelineMode.ENRICH, PipelineMode.METADATA):
            assert set(PRE_COMPLETED_STAGES[mode]) == early
        assert set(PRE_COMPLETED_STAGES[PipelineMode.ORGANIZE]) == early | {
            Stage.ASIN,
            Stage.METADATA,
        }

    def test_pre_completed_never_overlaps_the_run_order(self) -> None:
        """A stage cannot be both pre-completed and scheduled to run."""
        for mode, pre_done in PRE_COMPLETED_STAGES.items():
            assert not set(pre_done) & set(STAGE_ORDER[mode])


class TestStageResult:
    def test_completed_result_succeeded(self) -> None:
        result = StageResult(stage=Stage.CONVERT, status=StageStatus.COMPLETED)
        assert result.succeeded
        assert not result.is_retryable

    def test_transient_failure_is_retryable(self) -> None:
        result = StageResult(
            stage=Stage.ASIN,
            status=StageStatus.FAILED,
            message="audible search timed out",
            error_category=ErrorCategory.TRANSIENT,
        )
        assert not result.succeeded
        assert result.is_retryable

    def test_permanent_failure_is_not_retryable(self) -> None:
        """Retrying a permanent failure just delays the report by three tries."""
        result = StageResult(
            stage=Stage.ASIN,
            status=StageStatus.FAILED,
            message="no ASIN match above threshold",
            error_category=ErrorCategory.PERMANENT,
        )
        assert not result.is_retryable
