"""Pipeline runner -- orchestrates stage execution."""

from __future__ import annotations

import os
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


def _find_book_directories(root: Path) -> list[Path]:
    """Find leaf directories that contain audio files.

    A "book directory" is any directory containing audio files where
    no subdirectory also contains audio files. This groups multi-chapter
    MP3 books as a single unit instead of processing each file separately.
    """
    book_dirs: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        has_audio = any(Path(f).suffix.lower() in AUDIO_EXTENSIONS for f in filenames)
        if has_audio:
            # Check if any child dir also has audio (making this a parent, not a leaf)
            child_has_audio = False
            for sub in dirnames:
                sub_path = Path(dirpath) / sub
                if any(
                    f.suffix.lower() in AUDIO_EXTENSIONS
                    for f in sub_path.rglob("*")
                    if f.is_file()
                ):
                    child_has_audio = True
                    break
            if not child_has_audio:
                book_dirs.append(Path(dirpath))
    return sorted(book_dirs)


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
            # Group by book directory -- each leaf dir with audio files
            # is one book (handles both single .m4b and multi-chapter .mp3)
            book_dirs = _find_book_directories(source_path)

            total = len(book_dirs)
            label = "reorganize" if self.reorganize else "organize"
            click.echo(f"Batch {label}: {total} books in {source_path}")
            if self.config.dry_run:
                click.echo("[DRY-RUN] No changes will be made")

            # Build library index once for the entire batch
            from .library_index import LibraryIndex

            index = LibraryIndex(self.config.nfs_output_dir)

            ok = 0
            errors = 0
            with click.progressbar(
                book_dirs,
                label=f"Processing",
                show_eta=True,
                show_pos=True,
                item_show_func=lambda d: d.name if d else "",
            ) as bar:
                for d in bar:
                    try:
                        self._run_single(
                            d,
                            override_asin,
                            skip_lock,
                            index=index,
                        )
                        ok += 1
                    except Exception as e:
                        errors += 1
                        click.echo(f"  ERROR: {d.name}: {e}")

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
            f"\nPipeline: {source_path.name} " f"(mode={self.mode}, hash={book_hash})"
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
                book_hash,
                f"stages.{stage.value}.status",
            )
            if stage_status == "completed" and not self.config.force:
                click.echo(
                    f"  SKIP {stage.value} -- already completed (use --force to redo)"
                )
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

            # Check if stage failed (stages may set FAILED without raising)
            post_status = self.manifest.read_field(
                book_hash,
                f"stages.{stage.value}.status",
            )
            if post_status == StageStatus.FAILED.value:
                raise RuntimeError(
                    f"Stage '{stage.value}' failed for {source_path.name}"
                )

    def run_cmd(
        self,
        args: list[str],
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        """Run a subprocess command, respecting dry-run mode."""
        args_str = " ".join(args)
        if len(args_str) > 100:
            args_str = args_str[:97] + "..."
        log.debug(f"run_cmd args={args_str}")
        if self.config.dry_run:
            log.debug("dry-run skip")
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="",
                stderr="",
            )
        result = subprocess.run(args, capture_output=True, text=True)
        if check and result.returncode != 0:
            raise ExternalToolError(
                tool=args[0],
                exit_code=result.returncode,
                stderr=result.stderr,
            )
        return result
