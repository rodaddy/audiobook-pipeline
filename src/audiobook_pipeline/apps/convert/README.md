# Convert

Convert - turn a source directory into library-ready M4B audiobooks.

The conversion application owns the command-line boundary only: it validates
operator arguments, loads settings once, invokes the pipeline, and translates
the result into an exit status. Conversion and organization remain in services
so they can be exercised without a CLI process.

Key Components:
    - __main__: command options, settings construction, and pipeline invocation
    - pipeline service: ordered, resumable execution of conversion stages

Pattern/Convention:
    Keep domain decisions out of this package. A new conversion capability is
    implemented in a service and wired here through an explicit option.

Example:
    >>> from audiobook_pipeline.apps.convert.__main__ import main
    >>> callable(main)
    True

See Also:
    - audiobook_pipeline.services.pipeline
    - audiobook_pipeline.config

---
*Auto-generated from `__init__.py` by `_githooks/generate_folder_docs.py`.*
