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
from .pipeline_db import PipelineDB
from .models import (
    AUDIO_EXTENSIONS,
    CONVERTIBLE_EXTENSIONS,
    STAGE_ORDER,
    PipelineLevel,
    PipelineMode,
    Stage,
    StageStatus,
)
from .stages import get_stage_runner

if TYPE_CHECKING:
    from .library_index import LibraryIndex

log = logger.bind(stage="runner")


def _find_book_directories(
    root: Path,
    extensions: frozenset[str] = AUDIO_EXTENSIONS,
    include_chaptered_m4b: bool = False,
) -> list[Path]:
    """Find book root directories that contain matching audio files.

    A "book directory" is the first directory in a subtree that contains
    audio files. Once found, its children are pruned (not descended into)
    so multi-disc structures like CD1/CD2 are treated as one book.

    Args:
        root: Root directory to search
        extensions: File extensions to match (default: all audio types).
            Use CONVERTIBLE_EXTENSIONS to skip dirs with only .m4b files.
        include_chaptered_m4b: When True (used with CONVERTIBLE_EXTENSIONS),
            also include directories with multiple .m4b files (chaptered
            books that need concatenation).

    Single-pass O(n) -- no redundant rglob calls.
    """
    book_dirs: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        has_audio = any(Path(f).suffix.lower() in extensions for f in filenames)
        # Also detect chaptered m4b: multiple .m4b files = needs concat
        if not has_audio and include_chaptered_m4b:
            m4b_count = sum(1 for f in filenames if f.lower().endswith(".m4b"))
            has_audio = m4b_count > 1
        if has_audio:
            book_dirs.append(Path(dirpath))
            # Prune children so os.walk doesn't descend into subdirs
            # (treats this as the book root for multi-disc/nested structures)
            dirnames.clear()
    return sorted(book_dirs)


class PipelineRunner:
    """Runs the audiobook pipeline for a given source and mode."""

    def __init__(
        self,
        config: PipelineConfig,
        mode: PipelineMode,
        reorganize: bool = False,
        author_override: str | None = None,
    ) -> None:
        self.config = config
        self.mode = mode
        self.reorganize = reorganize
        self.author_override = author_override
        self.db = PipelineDB(config.db_path)

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
        # Batch mode: directory + convert = parallel conversion
        if source_path.is_dir() and self.mode == PipelineMode.CONVERT:
            from .convert_orchestrator import ConvertOrchestrator

            book_dirs = _find_book_directories(
                source_path,
                extensions=CONVERTIBLE_EXTENSIONS,
                include_chaptered_m4b=True,
            )
            click.echo(f"Batch convert: {len(book_dirs)} books in {source_path}")
            if self.config.dry_run:
                click.echo("[DRY-RUN] No changes will be made")

            orchestrator = ConvertOrchestrator(self.config)
            result = orchestrator.run_batch(book_dirs)

            if result.failed > 0:
                log.warning(f"Batch had {result.failed} failures out of {result.total}")
            return

        # Batch mode: directory + organize = process all audiobooks inside
        if source_path.is_dir() and self.mode == PipelineMode.ORGANIZE:
            book_dirs = _find_book_directories(source_path)

            # Auto-detect .author-override marker file (CLI flag takes priority)
            if not self.author_override:
                marker = source_path / ".author-override"
                if marker.is_file():
                    self.author_override = marker.read_text().strip()
                    log.info(f"Author override from marker: {self.author_override}")

            total = len(book_dirs)
            label = "reorganize" if self.reorganize else "organize"
            click.echo(f"Batch {label}: {total} books in {source_path}")
            if self.author_override:
                click.echo(f"Author override: {self.author_override}")
            if self.config.dry_run:
                click.echo("[DRY-RUN] No changes will be made")

            # Acquire reorganize lock for batch operations
            if self.reorganize and not skip_lock:
                if not self.db.acquire_reorganize_lock():
                    click.echo("ERROR: Another reorganize is already running")
                    return

            try:
                # Build library index once for the entire batch
                from .library_index import LibraryIndex

                index = LibraryIndex(self.config.nfs_output_dir, db=self.db)

                ok = 0
                errors = 0
                with click.progressbar(
                    book_dirs,
                    label="Processing",
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
            finally:
                if self.reorganize and not skip_lock:
                    self.db.release_reorganize_lock()
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

        effective_mode = self.mode

        # Auto-promote: organize mode dirs with convertible audio but no .m4b
        # need the full convert pipeline (validate -> concat -> convert -> ...)
        if effective_mode == PipelineMode.ORGANIZE and source_path.is_dir():
            has_m4b = any(source_path.rglob("*.m4b"))
            if not has_m4b:
                has_convertible = any(
                    f
                    for f in source_path.rglob("*")
                    if f.is_file() and f.suffix.lower() in CONVERTIBLE_EXTENSIONS
                )
                if has_convertible:
                    effective_mode = PipelineMode.CONVERT
                    log.info(
                        f"Auto-promote to convert: {source_path.name} "
                        f"(has convertible audio, no .m4b)"
                    )

        stages = list(STAGE_ORDER.get(effective_mode, []))

        # Simple level: strip organize and archive -- output stays in source dir
        if self.config.level == PipelineLevel.SIMPLE:
            stages = [s for s in stages if s not in (Stage.ORGANIZE, Stage.ARCHIVE)]

        book_hash = generate_book_hash(source_path)

        click.echo(
            f"\nPipeline: {source_path.name} " f"(mode={self.mode}, hash={book_hash})"
        )

        log.debug(f"Stages: {' -> '.join(s.value for s in stages)}")
        log.debug(f"nfs_output_dir: {self.config.nfs_output_dir}")

        # Create or load book record
        existing = self.db.read(book_hash)
        if existing is None:
            self.db.create(book_hash, str(source_path), self.mode)

        # Execute each stage in order
        for stage in stages:
            stage_status = self.db.read_field(
                book_hash,
                f"stages.{stage.value}.status",
            )
            if stage_status == "completed" and not self.config.force:
                click.echo(
                    f"  SKIP {stage.value} -- already completed (use --force to redo)"
                )
                continue

            try:
                stage_runner = get_stage_runner(stage)
            except NotImplementedError:
                log.debug(f"Skipping unimplemented stage: {stage.value}")
                continue
            kwargs = {
                "source_path": source_path,
                "book_hash": book_hash,
                "config": self.config,
                "manifest": self.db,
                "dry_run": self.config.dry_run,
                "verbose": self.config.verbose,
            }
            # Pass index to ASIN (author normalization) and ORGANIZE
            if stage in (Stage.ASIN, Stage.ORGANIZE):
                kwargs["index"] = index
            if stage == Stage.ORGANIZE:
                kwargs["reorganize"] = self.reorganize
                if self.author_override:
                    kwargs["author_override"] = self.author_override
            # Pass threads to convert stage (0 = all cores for single book)
            if stage == Stage.CONVERT:
                kwargs["threads"] = 0
            stage_runner(**kwargs)

            # Check if stage failed (stages may set FAILED without raising)
            post_status = self.db.read_field(
                book_hash,
                f"stages.{stage.value}.status",
            )
            if post_status == StageStatus.FAILED.value:
                raise RuntimeError(
                    f"Stage '{stage.value}' failed for {source_path.name}"
                )

        # Simple level: copy tagged m4b back to source directory
        if self.config.level == PipelineLevel.SIMPLE:
            self._copy_output_to_source(book_hash, source_path)

    def _copy_output_to_source(self, book_hash: str, source_path: Path) -> None:
        """Copy the tagged m4b from work dir back to source directory (simple level)."""
        import shutil

        data = self.db.read(book_hash)
        if not data:
            return

        # Find the output file from metadata or convert stage
        output_file = ""
        for stage_name in ("metadata", "convert"):
            output_file = (
                data.get("stages", {}).get(stage_name, {}).get("output_file", "")
            )
            if output_file:
                break

        if not output_file or not Path(output_file).exists():
            log.warning("Simple level: no output file found to copy back")
            return

        dest_dir = source_path if source_path.is_dir() else source_path.parent
        dest = dest_dir / Path(output_file).name

        if self.config.dry_run:
            click.echo(f"  [DRY-RUN] Would copy {Path(output_file).name} -> {dest}")
            return

        shutil.copy2(output_file, dest)
        click.echo(f"  Output: {dest}")
        log.info(f"Simple level: copied output to {dest}")

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
