"""CLI entry point for the audiobook pipeline."""

import os
from pathlib import Path

import click
from loguru import logger

from .config import PipelineConfig
from .models import PipelineMode
from .runner import PipelineRunner

log = logger.bind(stage="cli")


def _find_config_file() -> Path | None:
    """Look for .env next to the package or in cwd."""
    pkg_dir = Path(__file__).resolve().parent
    for candidate in [
        pkg_dir.parent.parent / ".env",  # dev: src/../.env
        Path.cwd() / ".env",
    ]:
        if candidate.is_file():
            return candidate
    return None


def _load_env_file(env_file: Path) -> None:
    """Load a shell-style .env file into os.environ (without overriding)."""
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Handle VAR=value and VAR="value" and VAR=${VAR:-default}
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip quotes
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        # Skip bash variable expansions like ${VAR:-default}
        if "${" in value:
            continue
        # Don't override existing env vars (CLI > env > file)
        if key not in os.environ:
            os.environ[key] = value


@click.command()
@click.argument("source_path", type=click.Path(exists=True))
@click.option(
    "-m",
    "--mode",
    type=click.Choice(["convert", "enrich", "metadata", "organize"]),
    default=None,
    help="Pipeline mode. Auto-detected if omitted.",
)
@click.option("--asin", default=None, help="Override ASIN discovery.")
@click.option(
    "--dry-run", is_flag=True, help="Show what would happen without doing it."
)
@click.option("--force", is_flag=True, help="Re-process even if already completed.")
@click.option("--no-lock", is_flag=True, help="Skip file locking.")
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
@click.option(
    "--ai-all", is_flag=True, help="Run AI validation on all books, not just conflicts."
)
@click.option(
    "--reorganize",
    is_flag=True,
    help="Reorganize library in-place: move (not copy) misplaced books. Implies --ai-all.",
)
@click.option(
    "--verify",
    is_flag=True,
    help="Run data quality checks on the library after processing.",
)
@click.option(
    "--author-override",
    default=None,
    help="Force all books into this folder instead of per-author folders.",
)
@click.option(
    "-c",
    "--config",
    "config_file",
    type=click.Path(exists=True),
    default=None,
    help="Path to .env file.",
)
def main(
    source_path: str,
    mode: str | None,
    asin: str | None,
    dry_run: bool,
    force: bool,
    no_lock: bool,
    verbose: bool,
    ai_all: bool,
    reorganize: bool,
    verify: bool,
    author_override: str | None,
    config_file: str | None,
) -> None:
    """Convert, enrich, and organize audiobooks into tagged M4B files."""
    source = Path(source_path).resolve()

    # --reorganize requires a directory and implies organize mode + ai-all
    if reorganize:
        if not source.is_dir():
            raise click.UsageError("--reorganize requires a directory path.")
        mode = "organize"
        ai_all = True
        log.info("Reorganize mode: implies --mode organize --ai-all")

    # Load .env into environment before PipelineConfig reads env vars
    env_file = Path(config_file) if config_file else _find_config_file()
    if env_file and env_file.is_file():
        _load_env_file(env_file)
        log.debug(f"Loaded env from {env_file}")
    else:
        log.debug("No .env found")

    # Auto-detect mode
    auto_detected = mode is None
    if mode is None:
        if source.is_dir():
            mode = "convert"
        elif source.suffix.lower() == ".m4b":
            mode = "enrich"
        else:
            raise click.UsageError("Cannot auto-detect mode. Use --mode.")

    pipeline_mode = PipelineMode(mode)

    if auto_detected:
        log.debug(f"Mode: {mode} (auto-detected)")
    else:
        log.debug(f"Mode: {mode} (explicit)")

    # Pass CLI flags as kwargs to avoid env pollution
    config_kwargs: dict[str, bool | str] = {
        "dry_run": dry_run,
        "force": force,
        "verbose": verbose,
        "ai_all": ai_all,
    }
    if verbose:
        config_kwargs["log_level"] = "DEBUG"

    config = PipelineConfig(**config_kwargs)  # type: ignore[arg-type]
    config.setup_logging()

    runner = PipelineRunner(
        config=config,
        mode=pipeline_mode,
        reorganize=reorganize,
        author_override=author_override,
    )

    log.info(
        f"Starting pipeline: source={source} mode={mode} "
        f"dry_run={dry_run} force={force}"
    )
    runner.run(source_path=source, override_asin=asin, skip_lock=no_lock)

    # Post-run data quality verification
    if verify and mode == "organize":
        from .ops.verify import print_report, verify_library

        click.echo("")
        library_root = config.nfs_output_dir
        results = verify_library(library_root)
        print_report(results)
