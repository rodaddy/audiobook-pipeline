# Models

The shapes this pipeline passes between its layers, declared once.

Purpose:
    Every value crossing a boundary is one of these, validated at construction.
    A layer that receives a model knows what it holds; a layer that receives a
    dict knows only what the code that built it happened to put there.

    That is not theoretical here. The pre-rewrite code moved 68
    ``dict[str, Any]`` values between modules, and the failures it produced
    were all the same shape: a missing key surfaced as a ``KeyError`` several
    frames from the code that failed to set it, and a malformed API payload
    surfaced as ``None`` propagating quietly until something tried to divide by
    it.

Key Components:
    - chapter: Chapter, ChapterSet -- the chapter table and its invariants;
      FetchedChapters, which carries WHY a catalogue table came back empty so a
      failed fetch is not mistaken for a wrong edition
    - media: ProbeResult, AudioStream -- what ffprobe reports about a file
    - book: SourceBook, BookDirectory -- audio on disk, before identification
    - metadata: AudibleResult, BookMetadata -- what an API said about a book

Architecture:
    Models hold shape and the invariants of that shape. They do not hold
    behaviour that needs the outside world: no subprocess calls, no HTTP, no
    file reads. That keeps them importable from anywhere without dragging a
    process boundary along, and testable without fixtures.

    A model may REJECT a value for being structurally unusable -- a chapter
    ending before it starts is not a chapter. It may not silently REPAIR one:
    clamping a bad offset produces a plausible book with wrong chapter marks,
    which is worse than a refusal because nothing says it happened.

Pattern/Convention:
    Import from the submodule, not from this package::

        from audiobook_pipeline.models.chapter import Chapter, ChapterSet

    Re-exporting through this ``__init__`` would make every ``models`` import
    pull in every submodule, which is how an import cycle gets built by
    accident.

Example:
    >>> from audiobook_pipeline.models.chapter import Chapter
    >>> Chapter(start_ms=0, end_ms=30467, title="Opening Credits").duration_ms
    30467

See Also:
    - _DOCS/STANDARDS-python.md ## LAW: Pydantic for every model
    - _plans/python-rewrite-sequence.md: the eleven defects these guard

---
*Auto-generated from `__init__.py` by `_githooks/generate_folder_docs.py`.*
