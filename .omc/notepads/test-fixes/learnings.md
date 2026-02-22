# Test Fixes Learnings

## 2026-02-21: test_index_early_skip_existing_file

### Problem
Test was using a generic filename "book.m4b" which is in `_GENERIC_BASENAMES` in `parse_path`. This caused:
1. `parse_path` to fall back to parent directory name for title
2. Unpredictable destination path computation
3. Mismatch between where test created the pre-existing file and where organize stage expected it

### Solution
1. Changed source filename to non-generic "Great Book.m4b"
2. Updated pre-existing destination path to match: `_unsorted/Great Book/Great Book.m4b`
3. Added mock for `build_plex_path` to return the dest_dir, isolating the test from path-building logic

### Key Pattern
When testing organize stage with index:
- Mock `build_plex_path` to control destination computation
- Use non-generic filenames (avoid "book", "audiobook", etc.)
- This makes the test focus on index skip logic, not path parsing edge cases

### Files Changed
- `/Volumes/ThunderBolt/Development/audiobook-pipeline/tests/test_stages/test_organize.py`

### Verification
All 24 tests in `test_stages/test_organize.py` pass.
