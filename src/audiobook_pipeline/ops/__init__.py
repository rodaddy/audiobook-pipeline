"""File operations for the audiobook pipeline.

Submodules:
    organize -- Path parsing (patterns A-G) with optional source_dir parameter for
                author-only directory fallback via _title_from_audio_file helper.
                Plex-compatible folder structure, library copying/moving with
                dest_filename parameter on copy_to_library and move_in_library,
                and duplicate folder detection. Handles bracket positions ([01]),
                parenthesized series info, near-match dedup, and move_in_library
                for reorganize mode with empty-dir cleanup. Accepts optional
                LibraryIndex for O(1) batch lookups. Debug logging traces every
                pattern match attempt, author heuristic rejection reason, and
                file operation. Normalization strips single trailing 's' only
                (not all trailing 's' characters) to avoid false negatives on
                names like "Ross", "Mass".
"""
