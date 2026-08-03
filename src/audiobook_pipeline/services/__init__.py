"""The work itself. One module per stage, each one callable on its own.

Purpose:
    A stage takes validated input, does one thing, and returns a model. It does
    not decide what runs next, does not touch the database, and does not read
    configuration out of the environment -- the pipeline hands it a
    ``PipelineConfig`` and the objects it needs.

WHY THE STAGES DO NOT KNOW ABOUT EACH OTHER
    The pre-rewrite stages imported each other to reuse a helper, so `concat`
    could not be tested without `convert` importing cleanly, and a change to
    one stage's signature broke tests for three others. Shared behaviour lives
    in ``utils`` and shared shapes in ``models``; a stage importing another
    stage is the signal that something belongs in one of those instead.

Key Components:
    - discovery: turn a directory tree into BookDirectory candidates

Pattern/Convention:
    Every stage function takes its inputs explicitly and returns a model::

        books = discover_books(source_dir)

See Also:
    - audiobook_pipeline.models.stage: the stage vocabulary and STAGE_ORDER
"""

from __future__ import annotations
