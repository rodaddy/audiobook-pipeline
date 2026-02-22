"""File operations for the audiobook pipeline.

Submodules:
    organize -- Path parsing (patterns A-G), Plex-compatible folder structure,
                library copying, and duplicate folder detection. Handles bracket
                positions ([01]), parenthesized series info, and near-match dedup.
                Normalization strips single trailing 's' only (not all trailing 's'
                characters) to avoid false negatives on names like "Ross", "Mass".
"""
