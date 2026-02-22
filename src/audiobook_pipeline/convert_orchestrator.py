"""CPU-aware parallel batch processor for audiobook conversion.

This module orchestrates the conversion of multiple audiobooks simultaneously,
monitoring CPU load and dynamically allocating resources across parallel jobs.
"""

import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

import click
from loguru import logger

from .config import PipelineConfig
from .manifest import Manifest
from .models import BatchResult, PipelineMode, Stage, StageStatus
from .sanitize import generate_book_hash
from .stages import get_stage_runner

log = logger.bind(stage="orchestrator")


class ConvertOrchestrator:
    """CPU-aware parallel batch processor for audiobook conversion.

    Manages parallel conversion of multiple audiobooks while monitoring system
    CPU load and dynamically adjusting thread allocation to prevent overload.

    Attributes:
        config: Pipeline configuration with resource limits
        manifest: Manifest manager for tracking conversion state
    """

    def __init__(self, config: PipelineConfig) -> None:
        """Initialize orchestrator with configuration.

        Args:
            config: Pipeline configuration including max workers and CPU ceiling
        """
        self.config = config
        self.manifest = Manifest(config.manifest_dir)

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
                    cpu_load = self._cpu_load_pct()

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
        existing = self.manifest.read(book_hash)
        if existing is None:
            self.manifest.create(book_hash, str(source_path), PipelineMode.CONVERT)
            log.debug(f"Created manifest for {source_path.name}")

        # MVP stages only (skip ASIN, METADATA, ARCHIVE)
        mvp_stages = [
            Stage.VALIDATE,
            Stage.CONCAT,
            Stage.CONVERT,
            Stage.ORGANIZE,
            Stage.CLEANUP,
        ]

        for stage in mvp_stages:
            # Check if already completed
            stage_status = self.manifest.read_field(
                book_hash, f"stages.{stage.value}.status"
            )
            if stage_status == StageStatus.COMPLETED.value and not self.config.force:
                log.debug(
                    f"Skipping {stage.value} for {source_path.name} (already completed)"
                )
                continue

            # Get stage runner
            stage_runner = get_stage_runner(stage)

            # Build kwargs for stage
            kwargs = {
                "source_path": source_path,
                "book_hash": book_hash,
                "config": self.config,
                "manifest": self.manifest,
                "dry_run": self.config.dry_run,
                "verbose": self.config.verbose,
            }

            # Pass threads to convert stage
            if stage == Stage.CONVERT:
                kwargs["threads"] = threads
                log.debug(
                    f"Convert stage for {source_path.name} using {threads} threads"
                )

            # Pass organize stage the output file path from convert
            if stage == Stage.ORGANIZE:
                data = self.manifest.read(book_hash)
                output_file = (
                    data.get("metadata", {}).get("output_file") if data else None
                )
                if output_file:
                    kwargs["source_path"] = Path(output_file)
                    log.debug(
                        f"Organize stage for {source_path.name} using {output_file}"
                    )

            # Run stage
            log.info(f"Running {stage.value} for {source_path.name}")
            stage_runner(**kwargs)

            # Check for failure
            post_status = self.manifest.read_field(
                book_hash, f"stages.{stage.value}.status"
            )
            if post_status == StageStatus.FAILED.value:
                raise RuntimeError(
                    f"Stage '{stage.value}' failed for {source_path.name}"
                )

        log.info(f"Successfully converted: {source_path.name}")

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
            max_workers = max(1, cpu_count // 2)  # auto: half the cores
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
        """Get current CPU load as a percentage.

        Returns:
            CPU load percentage (0-100+)
        """
        return os.getloadavg()[0] / (os.cpu_count() or 1) * 100

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
