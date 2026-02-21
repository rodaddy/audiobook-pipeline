"""Stage registry -- maps Stage enum values to run functions."""

from ..models import Stage


def get_stage_runner(stage: Stage):
    """Return the run function for a given stage.

    Returns None for stages not yet implemented.
    """
    if stage == Stage.ORGANIZE:
        from .organize import run
        return run
    # Other stages return None until implemented
    return None
