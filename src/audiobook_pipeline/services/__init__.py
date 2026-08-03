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
    - discovery: turn a directory tree into BookDirectory candidates, deciding
      per folder whether its files are one book or several
    - concat: join a multi-file book into one stream, carrying real chapter
      marks across from the source where they exist
    - convert: encode to M4B at a bitrate CEILINGed by the source, writing
      chapters and moving the moov atom to the front
    - audible: search the catalogue and fetch chapters, guarded by a duration
      match so a wrong hit cannot be adopted
    - identify: pick the best catalogue match for a discovered book, and refuse
      one whose runtime says it describes a different work
    - organize: build the library path -- ``Author/Series/Book N - Title/Book N
      - Title.m4b``, copied from the shape the existing library already uses --
      and place the finished file without overwriting anything
    - pipeline: the spine that runs the stages in order, skipping any the
      database already records as done, and falling back to what the SOURCE
      TREE knows when the catalogue cannot identify a book

Pattern/Convention:
    Every stage function takes its inputs explicitly and returns a model::

        books = discover_books(source_dir)
        destination = build_library_path(library_root, metadata)

See Also:
    - audiobook_pipeline.models.stage: the stage vocabulary and STAGE_ORDER
"""

from __future__ import annotations
