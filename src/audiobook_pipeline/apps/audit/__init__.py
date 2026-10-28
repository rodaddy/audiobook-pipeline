"""Audit - report what an audiobook library and its state database contain.

The audit application is a thin command boundary. It parses requested checks,
loads configuration once, and delegates inspection to the pipeline services.
Keeping library analysis out of the CLI makes the same behavior reusable from
tests and automation without reconstructing command-line state.

Key Components:
    - __main__: argument parsing, result presentation, and process exit status
    - services: reusable library and source comparison behavior

Pattern/Convention:
    Add new audit behavior as a service first, then expose only its arguments
    and result formatting through ``__main__``.

Example:
    >>> from audiobook_pipeline.apps.audit.__main__ import main
    >>> callable(main)
    True

See Also:
    - audiobook_pipeline.apps.convert
    - audiobook_pipeline.config
"""

from __future__ import annotations
