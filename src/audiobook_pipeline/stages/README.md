# stages

Stage registry -- maps Stage enum values to run functions.

Stages:
    organize -- Copy/move audiobooks to Plex-compatible NFS library. Accepts
                optional LibraryIndex for O(1) batch lookups and reorganize flag
                for in-place library cleanup (move instead of copy). Logs audio
                file discovery, Audible search strategies, AI resolution decisions,
                cross-source dedup, and correctly-placed detection.

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
