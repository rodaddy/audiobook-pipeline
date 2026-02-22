# ops

File operations for the audiobook pipeline.

Submodules:
    organize -- Path parsing (patterns A-G), Plex-compatible folder structure,
                library copying/moving, and duplicate folder detection. Handles
                bracket positions ([01]), parenthesized series info, near-match
                dedup, and move_in_library for reorganize mode with empty-dir
                cleanup. Accepts optional LibraryIndex for O(1) batch lookups.
                Debug logging traces every pattern match attempt, author heuristic
                rejection reason, and file operation.
                Normalization strips single trailing 's' only (not all trailing 's'
                characters) to avoid false negatives on names like "Ross", "Mass".

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
