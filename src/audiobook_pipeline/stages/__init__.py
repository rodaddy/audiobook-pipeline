"""Stage registry -- maps Stage enum values to run functions.

Stages:
    organize -- Copy/move audiobooks to Plex-compatible NFS library. Accepts
                optional LibraryIndex for O(1) batch lookups and reorganize flag
                for in-place library cleanup (move instead of copy). Logs audio
                file discovery, Audible search strategies, AI resolution decisions,
                cross-source dedup, and correctly-placed detection.
"""

from ..models import Stage


def get_stage_runner(stage: Stage):
    """Return the run function for a given stage.

    Raises NotImplementedError for stages not yet implemented.
    """
    if stage == Stage.ORGANIZE:
        from .organize import run
        return run

    # Raise clear error for unimplemented stages
    raise NotImplementedError(
        f"Stage '{stage.value}' is not yet implemented. "
        f"Only 'organize' mode is currently supported."
    )
