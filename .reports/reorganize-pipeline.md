# Reorganize Pipeline -- Validation Report

## Changes Made

### 1. parse_path() Directory Context Fix (stages/organize.py)
**Problem:** `parse_path()` received the audio file path, losing book directory metadata when files were nested in subdirectories (e.g., `Book Dir/subdir/file.m4b`).

**Fix:** Construct a synthetic path `source_path / source_file.name` so `parse_path()` always sees the book directory name as the parent, regardless of nesting depth.

### 2. ffprobe Graceful Failure (stages/organize.py)
**Problem:** Empty or corrupt audio files would crash the pipeline when `get_tags()` or `extract_author_from_tags()` threw exceptions.

**Fix:** Wrapped both calls in try/except, falling back to empty tags dict and empty author string.

### 3. AI Source Directory Context (ai.py)
**Problem:** The AI resolve prompt had no visibility into the book directory path, only the audio filename.

**Fix:** Added `source_directory` parameter to `resolve()`, included in evidence as "Source directory path" so the LLM can see rich parent directory names like "James Islington - The Licanius Trilogy".

### 4. LiteLLM Cache Bypass (ai.py)
**Problem:** LiteLLM proxy's semantic cache was returning stale responses from previous sessions, causing all books to resolve to the same wrong metadata (e.g., every book resolved to "James Islington / The Licanius Trilogy #3").

**Fix:** Added `extra_body={"cache": {"no-cache": True}}` to both `resolve()` and `disambiguate()` API calls. This is the LiteLLM-specific cache bypass mechanism (the existing `Cache-Control: no-cache` header was not being honored by the proxy).

### 5. AI Response Logging (ai.py)
Added debug logging of raw AI responses for easier troubleshooting.

## Test Results

### Test Sandbox
- Location: `/Volumes/ThunderBolt/AudioBookStuff/test-reorganize/` (cleaned up after testing)
- 5 books, ~1.3GB total
- Mix of organized (correct structure) and messy (flat/wrong naming) books

### Books Tested

| Source Dir | Author Resolved | Destination | Status |
|---|---|---|---|
| Anansi Boys/ (8 mp3 parts) | Neil Gaiman | Neil Gaiman/American Gods/Anansi Boys | Moved |
| Ann Leckie/ (1 m4b) | Ann Leckie | Ann Leckie/The Raven Tower | Moved |
| Ann Leckie - The Raven Tower/ (84 mp3s) | Ann Leckie | Ann Leckie/The Raven Tower | Moved, merged |
| Maureen Corrigan/ (1 m4b) | Maureen Corrigan | Maureen Corrigan/So We Read On | Moved |
| Vincenzo Latronico/Perfection - .../ (1 m4b) | Vincenzo Latronico | Vincenzo Latronico/Perfection | Moved |

### Validation Steps Passed
1. Dry-run showed correct AI resolution for all 5 books
2. Real run moved all misplaced books to correct Plex structure
3. Re-run showed all 4 books as "OK -- already correctly placed"
4. Empty source directories automatically cleaned up
5. `__init__.py` type error removed and verified clean (py_compile passes)

### Known Observations
- Anansi Boys resolved with series="American Gods" -- technically debatable (loose companion novel, not a strict sequel). Acceptable AI judgment for Plex organization.
- Ann Leckie m4b and mp3 chapter files merged into same destination folder. Correct behavior -- different formats of the same book coexist.
