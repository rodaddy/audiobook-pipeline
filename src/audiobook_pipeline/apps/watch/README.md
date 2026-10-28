# Watch

Watch - process stable inbox candidates through the existing batch spine.

The watch application owns only durable inbox polling and the conversion CLI
boundary. Every claimed candidate is discovered, admitted, and scheduled by
the existing services, so a watch run has the same library index, resource
ownership, and cross-process lease guarantees as an ordinary batch.

Key Components:
    - __main__: settings mapping, durable WatchRunner construction, and Ctrl-C handling
    - batch services: discovery, admission, and scheduled pipeline execution

Pattern/Convention:
    Keep candidate processing as a thin adapter to the batch spine. New
    conversion policy belongs in the existing service contracts, not in a
    second watch-only scheduler or resource lifecycle.

Example:
    >>> from audiobook_pipeline.apps.watch.__main__ import main
    >>> callable(main)
    True

See Also:
    - audiobook_pipeline.services.watch
    - audiobook_pipeline.services.batch
    - audiobook_pipeline.services.admission

---
*Auto-generated from `__init__.py` by `_githooks/generate_folder_docs.py`.*
