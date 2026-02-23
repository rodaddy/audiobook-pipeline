# ops

File operations for the audiobook pipeline.

Submodules:
    organize -- Path parsing (patterns A-G) with optional source_dir parameter for
                author-only directory fallback via _title_from_audio_file helper.
                Plex-compatible folder structure, library copying/moving with
                dest_filename parameter on copy_to_library and move_in_library,
                and duplicate folder detection. Near-match uses stop-word-aware
                subset checking (extra tokens must be stop words like "the",
                "of" -- not meaningful words like "origins") and Jaccard
                similarity at 0.85 threshold. Handles bracket positions ([01]),
                parenthesized series info, and move_in_library for reorganize
                mode with empty-dir cleanup. Accepts optional LibraryIndex for
                O(1) batch lookups. Debug logging traces every pattern match
                attempt, author heuristic rejection reason, and file operation.
                Normalization strips single trailing 's' only (not all trailing
                's' characters) to avoid false negatives on names like "Ross".

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
