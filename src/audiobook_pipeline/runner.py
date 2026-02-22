"""Pipeline runner -- orchestrates stage execution."""

import subprocess
from pathlib import Path

import click
from loguru import logger

from .config import PipelineConfig
from .errors import ExternalToolError
from .manifest import Manifest
from .models import AUDIO_EXTENSIONS, STAGE_ORDER, PipelineMode, Stage, StageStatus
from .stages import get_stage_runner

log = logger.bind(stage="runner")


class PipelineRunner:
    """Runs the audiobook pipeline for a given source and mode."""

    def __init__(self, config: PipelineConfig, mode: PipelineMode) -> None:
        self.config = config
        self.mode = mode
        self.manifest = Manifest(config.manifest_dir)

    def run(
        self,
        source_path: Path,
        override_asin: str | None = None,
        skip_lock: bool = False,
    ) -> None:
        """Run the pipeline for a source path.

        For organize mode with a directory: walks the tree and processes
        each audiobook file found.
        """
        # Batch mode: directory + organize = process all audiobooks inside
        if source_path.is_dir() and self.mode == PipelineMode.ORGANIZE:
            audio_files = sorted(
                f for f in source_path.rglob("*")
                if f.suffix.lower() in AUDIO_EXTENSIONS
            )
            click.echo(
                f"Batch organize: {len(audio_files)} audiobooks "
                f"in {source_path}"
            )
            if self.config.dry_run:
                click.echo("[DRY-RUN] No changes will be made")

            ok = 0
            errors = 0
            for f in audio_files:
                try:
                    self._run_single(f, override_asin, skip_lock)
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
            stage_runner(
                source_path=source_path,
                book_hash=book_hash,
                config=self.config,
                manifest=self.manifest,
                dry_run=self.config.dry_run,
                verbose=self.config.verbose,
            )

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
