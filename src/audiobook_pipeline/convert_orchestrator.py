"""CPU-aware parallel batch processor for audiobook conversion.

This module orchestrates the conversion of multiple audiobooks simultaneously,
monitoring CPU load and dynamically allocating resources across parallel jobs.
"""

import os
import shutil
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

import psutil

import click
from loguru import logger

from .config import PipelineConfig
from .pipeline_db import PipelineDB
from .models import BatchResult, PipelineMode, Stage, StageStatus
from .ops.organize import build_plex_path
from .sanitize import generate_book_hash
from .stages import get_stage_runner

log = logger.bind(stage="orchestrator")


class ConvertOrchestrator:
    """CPU-aware parallel batch processor for audiobook conversion.

    Manages parallel conversion of multiple audiobooks while monitoring system
    CPU load and dynamically adjusting thread allocation to prevent overload.

    Attributes:
        config: Pipeline configuration with resource limits
        db: PipelineDB instance for tracking conversion state
    """

    def __init__(self, config: PipelineConfig) -> None:
        """Initialize orchestrator with configuration.

        Args:
            config: Pipeline configuration including max workers and CPU ceiling
        """
        self.config = config
        self.db = PipelineDB(config.db_path)

    def clean_state(self, book_dirs: list[Path]) -> None:
        """Reset book records and work dirs for books in the batch.

        Called by default before each run so books process from scratch.
        Always resumes by default with SQLite (no --resume flag needed).
        """
        cleaned_books = 0
        cleaned_work = 0
        for book_path in book_dirs:
            book_hash = generate_book_hash(book_path)
            if self.db.read(book_hash) is not None:
                self.db.reset_book(book_hash)
                cleaned_books += 1
            work_dir = self.config.work_dir / book_hash
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
                cleaned_work += 1
        if cleaned_books or cleaned_work:
            log.info(
                f"Cleaned state: {cleaned_books} book records, "
                f"{cleaned_work} work dirs"
            )

    def run_batch(self, book_dirs: list[Path]) -> BatchResult:
        """Process a list of book directories in parallel with CPU monitoring.

        Dynamically schedules conversion jobs based on available CPU resources,
        preventing system overload while maximizing throughput.

        Args:
            book_dirs: List of directories containing audiobook files

        Returns:
            BatchResult with counts of completed, failed, and total books
        """
        if not book_dirs:
            log.warning("No books to process")
            return BatchResult(completed=0, failed=0, total=0)

        # Ensure work/manifest/output directories exist
        self.config.ensure_dirs()

        max_workers = self._calculate_max_workers()
        log.info(
            f"Starting batch conversion: {len(book_dirs)} books, "
            f"max_workers={max_workers}, cpu_ceiling={self.config.cpu_ceiling}%"
        )

        queued = list(book_dirs)
        active: dict[Future, Path] = {}
        completed: list[Path] = []
        failed: list[Path] = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while queued or active:
                # Check CPU and submit new jobs if below ceiling
                # Ramp up gradually: measure CPU with a real interval between
                # submissions so readings reflect actual load from new workers
                cpu_load = self._cpu_load_pct()
                while (
                    queued
                    and len(active) < max_workers
                    and cpu_load < self.config.cpu_ceiling
                ):
                    book_path = queued.pop(0)
                    threads = self._threads_per_worker(len(active) + 1)
                    future = executor.submit(self._run_single_safe, book_path, threads)
                    active[future] = book_path
                    log.debug(
                        f"Submitted {book_path.name} (threads={threads}, "
                        f"active={len(active)}/{max_workers})"
                    )
                    # Let CPU readings stabilize before submitting the next job
                    time.sleep(2.0)
                    cpu_load = psutil.cpu_percent(interval=1.0)

                # Display status
                self._display_status(
                    cpu_load, len(active), len(queued), completed, failed
                )

                # Wait for at least one job to complete
                if active:
                    done, pending = wait(
                        active.keys(), timeout=5.0, return_when=FIRST_COMPLETED
                    )

                    # Process completed jobs
                    for future in done:
                        book_path = active.pop(future)
                        success = future.result()
                        if success:
                            completed.append(book_path)
                            log.info(f"Completed: {book_path.name}")
                        else:
                            failed.append(book_path)
                            log.error(f"Failed: {book_path.name}")

        # Final summary
        self._display_summary(completed, failed, len(book_dirs))

        return BatchResult(
            completed=len(completed), failed=len(failed), total=len(book_dirs)
        )

    def _run_single_safe(self, source_path: Path, threads: int) -> bool:
        """Wrapper for _run_single that catches exceptions.

        Cleans up the work directory on failure to avoid orphaned artifacts.

        Args:
            source_path: Directory containing audiobook files
            threads: Number of threads to allocate for ffmpeg

        Returns:
            True if conversion succeeded, False otherwise
        """
        try:
            self._run_single(source_path, threads)
            return True
        except Exception as e:
            log.error(f"Error converting {source_path.name}: {e}")
            # Clean up work dir on failure to avoid orphaned artifacts
            book_hash = generate_book_hash(source_path)
            work_dir = self.config.work_dir / book_hash
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)
                log.debug(f"Cleaned up work dir for failed: {source_path.name}")
            return False

    def _run_single(self, source_path: Path, threads: int) -> None:
        """Process a single audiobook through all conversion stages.

        Args:
            source_path: Directory containing audiobook files
            threads: Number of threads to allocate for ffmpeg conversion

        Raises:
            RuntimeError: If any stage fails
        """
        book_hash = generate_book_hash(source_path)
        log.info(f"Starting conversion: {source_path.name} (hash={book_hash[:8]})")

        # Create or load manifest
        existing = self.db.read(book_hash)
        if existing is None:
            self.db.create(book_hash, str(source_path), PipelineMode.CONVERT)
            log.debug(f"Created manifest for {source_path.name}")

        # MVP stages (skip ARCHIVE)
        mvp_stages = [
            Stage.VALIDATE,
            Stage.CONCAT,
            Stage.CONVERT,
            Stage.ASIN,
            Stage.METADATA,
            Stage.ORGANIZE,
            Stage.CLEANUP,
        ]

        for stage in mvp_stages:
            # In dry-run, skip metadata/organize (no output file)
            # ASIN can run in dry-run (metadata-only, no file changes)
            # Cleanup still runs to remove work dirs created by validate/concat
            if self.config.dry_run and stage in (
                Stage.METADATA,
                Stage.ORGANIZE,
            ):
                log.debug(f"Skipping {stage.value} in dry-run mode")
                continue

            # Check if already completed
            stage_status = self.db.read_field(book_hash, f"stages.{stage.value}.status")
            if stage_status == StageStatus.COMPLETED.value and not self.config.force:
                if self._is_stage_stale(book_hash, stage, source_path):
                    self.db.set_stage(book_hash, stage, StageStatus.PENDING)
                else:
                    log.debug(
                        f"Skipping {stage.value} for {source_path.name} "
                        f"(already completed)"
                    )
                    continue

            # Get stage runner
            stage_runner = get_stage_runner(stage)

            # Build kwargs for stage
            # Each thread gets its own PipelineDB connection automatically
            # (PipelineDB uses threading.local for per-thread connections)
            kwargs = {
                "source_path": source_path,
                "book_hash": book_hash,
                "config": self.config,
                "manifest": self.db,
                "dry_run": self.config.dry_run,
                "verbose": self.config.verbose,
            }

            # Pass threads to convert stage
            if stage == Stage.CONVERT:
                kwargs["threads"] = threads
                log.debug(
                    f"Convert stage for {source_path.name} using {threads} threads"
                )

            # Run stage
            log.info(f"Running {stage.value} for {source_path.name}")
            stage_runner(**kwargs)

            # Check for failure
            post_status = self.db.read_field(book_hash, f"stages.{stage.value}.status")
            if post_status == StageStatus.FAILED.value:
                raise RuntimeError(
                    f"Stage '{stage.value}' failed for {source_path.name}"
                )

            # After metadata resolves, check if destination already has the file.
            # If so, re-tag in place and skip organize+cleanup.
            if stage == Stage.METADATA:
                if self._retag_existing_destination(book_hash, source_path):
                    break

        log.info(f"Successfully converted: {source_path.name}")

    def _is_stage_stale(self, book_hash: str, stage: Stage, source_path: Path) -> bool:
        """Check if a 'completed' stage is actually stale.

        Validates that expected artifacts still exist. A stage is stale if:
        - validate/concat: work dir missing (no audio_files.txt/files.txt)
        - convert: output M4B file missing
        - metadata: convert output missing (metadata tags the convert output)

        Returns True if stale (needs re-run), False if genuinely complete.
        """
        work_dir = self.config.work_dir / book_hash

        if stage == Stage.VALIDATE:
            audio_list = work_dir / "audio_files.txt"
            if not audio_list.exists():
                log.warning(
                    f"Stale: audio_files.txt missing for {source_path.name}, "
                    f"re-running from validate"
                )
                return True

        elif stage == Stage.CONCAT:
            files_txt = work_dir / "files.txt"
            if not files_txt.exists():
                log.warning(
                    f"Stale: files.txt missing for {source_path.name}, "
                    f"re-running from concat"
                )
                return True

        elif stage == Stage.CONVERT:
            output_file = self.db.read_field(
                book_hash, "stages.convert.output_file"
            ) or self.db.read_field(book_hash, "metadata.output_file")
            if not output_file or not Path(output_file).is_file():
                log.warning(
                    f"Stale: convert output missing for {source_path.name}, "
                    f"re-running from convert"
                )
                return True

        elif stage == Stage.METADATA:
            # Metadata tags the convert output -- if that's gone, re-run
            output_file = self.db.read_field(
                book_hash, "stages.convert.output_file"
            ) or self.db.read_field(book_hash, "metadata.output_file")
            if output_file and not Path(output_file).is_file():
                log.warning(
                    f"Stale: convert output missing for {source_path.name}, "
                    f"re-running metadata"
                )
                return True

        return False

    def _retag_existing_destination(self, book_hash: str, source_path: Path) -> bool:
        """Check if the book already exists at its destination and re-tag it.

        After ASIN+metadata resolve, compute the Plex destination path.
        If the M4B file already exists there, re-tag it in place with
        updated metadata and skip organize+cleanup.

        Returns True if destination was found and re-tagged (caller should
        skip remaining stages), False to continue normally.
        """
        data = self.db.read(book_hash)
        if not data:
            return False

        meta = data.get("metadata", {})
        metadata = {
            "author": meta.get("parsed_author", ""),
            "title": meta.get("parsed_title", ""),
            "series": meta.get("parsed_series", ""),
            "position": meta.get("parsed_position", ""),
        }

        # Can't compute destination without at least a title
        if not metadata["title"]:
            return False

        dest_dir = build_plex_path(self.config.nfs_output_dir, metadata)
        if not dest_dir.is_dir():
            return False

        # Look for an existing M4B in the destination
        m4b_files = list(dest_dir.glob("*.m4b"))
        if not m4b_files:
            return False

        existing_file = m4b_files[0]
        log.info(
            f"Destination file exists: {existing_file.name} -- "
            f"re-tagging in place, skipping organize"
        )

        # Re-tag the existing file with updated metadata
        from .stages.metadata import _build_album, _download_cover, _write_tags

        narrator = meta.get("parsed_narrator", "")
        year = meta.get("parsed_year", "")
        series = meta.get("parsed_series", "")
        position = meta.get("parsed_position", "")
        cover_url = meta.get("cover_url", "")

        album = _build_album(metadata["title"], series, position)
        tags: dict[str, str] = {
            "artist": metadata["author"],
            "album_artist": metadata["author"],
            "album": album,
            "title": metadata["title"],
            "genre": "Audiobook",
            "media_type": "2",
        }
        if narrator:
            tags["composer"] = narrator
        if year:
            tags["date"] = year
        if series:
            tags["show"] = series
            tags["grouping"] = series

        if self.config.dry_run:
            click.echo(f"  [DRY-RUN] Would re-tag {existing_file.name} in place")
            self.db.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)
            self.db.set_stage(book_hash, Stage.CLEANUP, StageStatus.COMPLETED)
            return True

        # Download cover (non-fatal)
        cover_path = None
        if cover_url:
            cover_path = _download_cover(cover_url, existing_file.parent)

        success = _write_tags(existing_file, tags, cover_path=cover_path)

        if cover_path and cover_path.exists():
            cover_path.unlink(missing_ok=True)

        if not success:
            click.echo(f"  WARNING: Re-tag failed for {existing_file.name}")
            return False

        click.echo(
            f"  Re-tagged in place: {existing_file.name} "
            f"(artist={metadata['author']!r})"
        )

        # Mark organize as completed (skipped -- file already at destination)
        data = self.db.read(book_hash)
        if data:
            data["stages"]["organize"]["output_file"] = str(existing_file)
            data["stages"]["organize"]["dest_dir"] = str(dest_dir)
            self.db.update(book_hash, data)
        self.db.set_stage(book_hash, Stage.ORGANIZE, StageStatus.COMPLETED)

        # Clean up work dir (would normally be done by cleanup stage)
        work_dir = self.config.work_dir / book_hash
        if work_dir.exists() and self.config.cleanup_work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
            log.debug(f"Cleaned work dir after retag: {source_path.name}")
        self.db.set_stage(book_hash, Stage.CLEANUP, StageStatus.COMPLETED)
        return True

    def _calculate_max_workers(self) -> int:
        """Calculate maximum parallel workers based on config and CPU count.

        Returns:
            Number of worker threads to use
        """
        cpu_count = os.cpu_count() or 1
        if self.config.max_parallel_converts > 0:
            max_workers = self.config.max_parallel_converts
            log.debug(f"Using configured max_parallel_converts: {max_workers}")
        else:
            # Balance workers vs threads: enough parallelism to keep CPU busy
            # but enough threads per worker for ffmpeg to work efficiently
            max_workers = max(1, min(4, cpu_count // 3))
            log.debug(
                f"Auto-calculated max_workers: {max_workers} "
                f"(cpu_count={cpu_count})"
            )
        return max_workers

    def _threads_per_worker(self, active_count: int) -> int:
        """Calculate thread allocation for each ffmpeg job.

        When only one book is active, use all available cores (0 = ffmpeg default).
        For multiple books, divide available cores among active workers.

        Args:
            active_count: Number of currently active conversion jobs

        Returns:
            Number of threads to allocate (0 means use all cores)
        """
        cpu_count = os.cpu_count() or 1
        if active_count <= 1:
            return 0  # single book: all cores
        return max(1, (cpu_count - 1) // active_count)

    def _cpu_load_pct(self) -> float:
        """Get CPU utilization as a percentage (0-100).

        Uses a blocking 1-second psutil sample for accurate system-wide
        measurement. Non-blocking (interval=None) underreports when the
        calling thread spends most of its time sleeping in wait().
        """
        return psutil.cpu_percent(interval=1.0)

    def _display_status(
        self,
        cpu_load: float,
        active: int,
        queued: int,
        completed: list[Path],
        failed: list[Path],
    ) -> None:
        """Display current conversion status.

        Args:
            cpu_load: Current CPU load percentage
            active: Number of active jobs
            queued: Number of queued jobs
            completed: List of completed book paths
            failed: List of failed book paths
        """
        status = (
            f"  [CPU: {cpu_load:.0f}%] "
            f"active={active} queued={queued} "
            f"done={len(completed)} failed={len(failed)}"
        )
        click.echo(f"\r{status}", nl=False)

    def _display_summary(
        self, completed: list[Path], failed: list[Path], total: int
    ) -> None:
        """Display final conversion summary.

        Args:
            completed: List of successfully converted books
            failed: List of failed books
            total: Total number of books in batch
        """
        click.echo("\n")  # Clear status line
        click.echo(
            f"\nBatch conversion complete: "
            f"{len(completed)}/{total} succeeded, {len(failed)} failed"
        )

        if failed:
            click.echo("\nFailed books:")
            for book_path in failed:
                click.echo(f"  - {book_path.name}")
