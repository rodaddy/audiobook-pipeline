"""Pipeline runner -- orchestrates stage execution."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
from loguru import logger

from .config import PipelineConfig
from .errors import ExternalToolError
from .manifest import Manifest
from .models import AUDIO_EXTENSIONS, STAGE_ORDER, PipelineMode, Stage, StageStatus
from .stages import get_stage_runner

if TYPE_CHECKING:
    from .library_index import LibraryIndex

log = logger.bind(stage="runner")


class PipelineRunner:
    """Runs the audiobook pipeline for a given source and mode."""

    def __init__(
        self,
        config: PipelineConfig,
        mode: PipelineMode,
        reorganize: bool = False,
    ) -> None:
        self.config = config
        self.mode = mode
        self.reorganize = reorganize
        self.manifest = Manifest(config.manifest_dir)

    def run(
        self,
        source_path: Path,
        override_asin: str | None = None,
        skip_lock: bool = False,
    ) -> None:
        """Run the pipeline for a source path.

        For organize mode with a directory: walks the tree and processes
        each audiobook file found. Builds a LibraryIndex once for batch
        mode to enable O(1) lookups instead of per-file iterdir() scans.
        """
        # Batch mode: directory + organize = process all audiobooks inside
        if source_path.is_dir() and self.mode == PipelineMode.ORGANIZE:
            audio_files = sorted(
                f for f in source_path.rglob("*")
                if f.suffix.lower() in AUDIO_EXTENSIONS
            )

            total = len(audio_files)
            label = "reorganize" if self.reorganize else "organize"
            click.echo(
                f"Batch {label}: {total} audiobooks in {source_path}"
            )
            if self.config.dry_run:
                click.echo("[DRY-RUN] No changes will be made")

            # Build library index once for the entire batch
            from .library_index import LibraryIndex
            index = LibraryIndex(self.config.nfs_output_dir)

            ok = 0
            errors = 0
            with click.progressbar(
                audio_files, label=f"Processing", show_eta=True,
                show_pos=True, item_show_func=lambda f: f.name if f else "",
            ) as bar:
                for f in bar:
                    try:
                        self._run_single(
                            f, override_asin, skip_lock, index=index,
                        )
                        ok += 1
                    except Exception as e:
                        errors += 1
                        click.echo(f"  ERROR: {f.name}: {e}")

            click.echo(f"\nBatch complete: {ok} succeeded, {errors} failed")
            return

        self._run_single(source_path, override_asin, skip_lock)

    def _run_single(
        self,
        source_path: Path,
        override_asin: str | None = None,
        skip_lock: bool = False,
        index: LibraryIndex | None = None,
    ) -> None:
        """Run the pipeline for a single source file/directory."""
        from .sanitize import generate_book_hash

        stages = STAGE_ORDER.get(self.mode, [])
        book_hash = generate_book_hash(source_path)

        click.echo(
            f"\nPipeline: {source_path.name} "
            f"(mode={self.mode}, hash={book_hash})"
        )

        log.debug(f"Stages: {' -> '.join(s.value for s in stages)}")
        log.debug(f"nfs_output_dir: {self.config.nfs_output_dir}")

        # Create or load manifest
        existing = self.manifest.read(book_hash)
        if existing is None:
            self.manifest.create(book_hash, str(source_path), self.mode)

        # Execute each stage in order
        for stage in stages:
            stage_status = self.manifest.read_field(
                book_hash, f"stages.{stage.value}.status",
            )
            if stage_status == "completed" and not self.config.force:
                log.debug(f"[{stage.value}] already completed, skipping")
                continue

            stage_runner = get_stage_runner(stage)
            kwargs = {
                "source_path": source_path,
                "book_hash": book_hash,
                "config": self.config,
                "manifest": self.manifest,
                "dry_run": self.config.dry_run,
                "verbose": self.config.verbose,
            }
            # Pass index and reorganize to organize stage
            if stage == Stage.ORGANIZE:
                kwargs["index"] = index
                kwargs["reorganize"] = self.reorganize
            stage_runner(**kwargs)

    def run_cmd(
        self, args: list[str], check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a subprocess command, respecting dry-run mode."""
        if self.config.dry_run:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr="",
            )
        result = subprocess.run(args, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise ExternalToolError(
                tool=args[0],
                exit_code=result.returncode,
                stderr=result.stderr,
            )
        return result
