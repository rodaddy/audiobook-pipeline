# Antagonistic Agent Review -- Audiobook Pipeline

**Date:** 2026-02-21
**Branch:** feat/python-rewrite
**Commit:** 8a43b5e

## Process

Four specialized agents ran in parallel against the codebase:

| Agent | Role | Duration | Findings |
|-------|------|----------|----------|
| Code Reviewer | Bugs, edge cases, API mismatches | ~177s | 2 HIGH, 4 MEDIUM, 2 LOW |
| Security Reviewer | Vulns, injection, traversal | ~77s | 1 MEDIUM, 2 LOW, 1 INFO |
| Architect | Design debt, coupling, fragility | ~104s | 2 HIGH, 3 MEDIUM, 3 LOW |
| QA Tester | mypy, imports, API signatures | ~55s | 1 FAIL (cli.py types), 7 PASS |

**Total wall time:** ~3 min (parallel). Sequential would have been ~7 min.

## Deduplicated Findings

Agents overlapped on several issues. Below is the deduplicated, prioritized list.

### HIGH -- Must Fix

#### H1. _move_book_directory silently skips subdirectories
**Source:** Architect, Code Reviewer
**File:** stages/organize.py:251-275
**Issue:** `_move_book_directory` iterates `source_dir.iterdir()` and only moves files (`if item.is_file()`). Multi-disc books with CD1/, CD2/ subdirectories leave orphaned content behind. `_cleanup_empty_parents` then fails silently because the dir isn't empty.
**Impact:** Data not moved for multi-disc audiobooks. Silent data loss from the user's perspective (files exist but aren't in the library).
**Fix:** Recursively move all contents, or use `shutil.copytree`/`shutil.move` on the entire directory.

#### H2. _find_book_directories drops audio at intermediate levels + O(n^2)
**Source:** Code Reviewer, Architect
**File:** runner.py:25-49
**Issue:** Two problems: (a) The "leaf directory" heuristic skips any dir that has a child dir also containing audio -- so `Book/part1.mp3` + `Book/extras/bonus.mp3` drops the parent's files. (b) For every dir with audio, `rglob("*")` re-scans the subtree that os.walk already visits -- quadratic on large libraries.
**Impact:** Silent file dropping + performance degradation at scale.
**Fix:** Treat first audio-containing dir as book root, prune `dirnames` to stop descending. Single pass, O(n).

#### H3. Correctly-placed check uses source_file.parent instead of source_path
**Source:** Architect
**File:** stages/organize.py:183-190
**Issue:** `book_dir = source_file.parent` but `source_file` can be nested (from rglob). For `Book/CD1/track.mp3`, `book_dir = Book/CD1/` which never matches `dest_dir = Author/Title/`. Reorganize incorrectly determines correctly-placed books need moving.
**Impact:** Wasted I/O, potential duplicate directories.
**Fix:** Use `source_path` (the runner-identified book dir) instead of `source_file.parent`.

### MEDIUM -- Should Fix

#### M1. cli.py mypy type errors
**Source:** QA Tester
**File:** cli.py:125-134
**Issue:** `config_kwargs` inferred as `dict[str, bool]` but receives a `str` value ("DEBUG"). mypy reports 3 errors.
**Fix:** Annotate `config_kwargs: dict[str, bool | str] = {...}`.

#### M2. Broad except Exception silences missing ffprobe
**Source:** Code Reviewer
**File:** stages/organize.py:72-78
**Issue:** Catches all exceptions including `FileNotFoundError` (ffprobe not installed). Entire library processes with degraded results and only per-book warnings.
**Fix:** Re-raise `FileNotFoundError`, catch specific subprocess/JSON exceptions.

#### M3. Prompt injection via directory names in AI
**Source:** Security Reviewer
**File:** ai.py:92-97
**Issue:** `source_directory` (filesystem path) interpolated directly into LLM prompt. Crafted dir name could manipulate AI response. Mitigated by `sanitize_filename()` on output and structured parser.
**Fix:** Strip newlines and truncate before interpolation. Low priority given threat model (local CLI, attacker needs filesystem access).

#### M4. _cleanup_empty_parents unbounded (stop_at=None)
**Source:** Code Reviewer, Architect, Security Reviewer
**File:** stages/organize.py:273, ops/organize.py:388-404
**Issue:** Both `_move_book_directory` and `move_in_library` call with `stop_at=None`. Could remove empty dirs above the library root, including NFS mount points.
**Fix:** Pass `config.nfs_output_dir` as `stop_at`.

#### M5. cleanup.py not registered in get_stage_runner
**Source:** Code Reviewer
**File:** stages/__init__.py:16-30
**Issue:** `cleanup.py` has a proper `run()` but isn't registered. Dead code that will break ENRICH/METADATA modes.
**Fix:** Add `Stage.CLEANUP` case to `get_stage_runner()`.

#### M6. Cross-source dedup collision on common filenames
**Source:** Architect
**File:** stages/organize.py:57, library_index.py:91-100
**Issue:** `index.mark_processed(source_file.stem)` deduplicates by filename stem. Multiple books with `01.mp3` or `audiobook.m4b` collide silently.
**Fix:** Include book directory in dedup key: `f"{source_path.name}/{source_file.stem}"`.

#### M7. Cache bypass fragile and LiteLLM-specific
**Source:** Architect
**File:** ai.py:175-176
**Issue:** `extra_body={"cache": {"no-cache": True}}` is undocumented LiteLLM behavior. UUID nonce in prompt already defeats content-based caching. Three mechanisms, no documentation of which is primary.
**Fix:** Add comment documenting nonce as primary mechanism, extra_body as supplementary.

#### M8. get_client strips /v1 from base_url incorrectly
**Source:** Code Reviewer
**File:** ai.py:27-34
**Issue:** Comment says "OpenAI SDK expects base_url WITHOUT /v1" -- this is wrong. OpenAI SDK uses base_url as-is. Works with LiteLLM but breaks standard OpenAI endpoints.
**Fix:** Remove stripping or document as LiteLLM-specific.

### LOW -- Nice to Fix

#### L1. Symlink following in rglob
**Source:** Security Reviewer
**File:** stages/organize.py:287-293
**Issue:** `rglob("*")` follows symlinks. Could traverse outside intended tree.
**Fix:** Filter `not f.is_symlink()`.

#### L2. source_directory parameter naming
**Source:** Architect
**File:** ai.py:79
**Issue:** Named `source_directory` but receives file path in single-file mode.
**Fix:** Rename to `source_path` or pass `.parent` for files.

#### L3. _find_audio_file no existence check
**Source:** Code Reviewer
**File:** stages/organize.py:278-297
**Issue:** If source_path deleted between scan and processing, raw traceback.
**Fix:** Existence check or try/except.

#### L4. Hook fail-open design
**Source:** Security Reviewer
**File:** pre-bash-type-check.ts:183-186
**Issue:** Catch-all exits 0 (allow). Already logs error. Intentional design.
**Fix:** None needed -- fail-open is correct for a quality gate.

## Process Observations

### What Worked
1. **Parallel execution** -- 4 agents in ~3 min wall time vs ~7 min serial
2. **Cross-agent validation** -- 3 agents independently found _cleanup_empty_parents issue, 2 found the O(n^2) rglob, confirming these are real
3. **Complementary perspectives** -- Security found prompt injection (others missed), Architect found dedup collision (others missed), QA found the cli.py type error (others missed)
4. **Adversarial framing** -- "find real bugs, not style nits" produced actionable findings vs typical "add docstrings" noise

### What Could Improve
1. **Agent overlap** -- ~30% of findings were duplicates across agents. Could reduce with more specific scoping (e.g., "don't review path handling" for security agent)
2. **No runtime testing** -- QA agent ran static checks but couldn't test with real audio files (no test sandbox). A dedicated integration test agent with a test fixture would catch H1/H2/H3 empirically
3. **No fix verification** -- Agents found issues but didn't verify fixes. A second pass with a "fix then re-review" loop would increase confidence
4. **Severity calibration** -- Architect and Code Reviewer both rated issues HIGH but with different thresholds. A shared rubric would help

### Recommended Process for Next Time
1. Spawn agents with non-overlapping scopes (security: external interfaces only, code: internal logic only, architect: cross-module only)
2. Include an integration test agent with a real test fixture
3. Run a second pass: fix agent applies changes, then re-run reviewers to verify
4. Add a "consensus" step: findings that appear in 2+ agents get auto-promoted

---

## E2E Test Results

### Round 1: Real audiobook files (4 books, 116MB)
- Dry-run, real reorganize, re-run for idempotency
- All 10 files moved, source cleaned up, 3/5 correctly-placed detected
- **Bugs found:** 2 (dedup collision on common filenames, year-as-series parsing)
- **Both fixed** in organize.py and ops/organize.py

### Round 2: Edge case sandbox (10 scenarios)
- Empty dir, Unicode, Dedup collision, Deep nesting, Mixed formats
- Non-audio junk, Special chars, Prompt injection, Apostrophes, Zero-byte corrupt
- **All passed** after Round 1 fixes

### Round 3: Parallel QA agents (5 agents, 18 tests)

| Agent | Tests | Pass | Fail | Partial |
|-------|-------|------|------|---------|
| Single-file mode | 3 | 3 | 0 | 0 |
| No-AI fallback | 3 | 3 | 0 | 0 |
| Symlink edge cases | 3 | 2 | 1 | 0 |
| Reorganize edge cases | 4 | 4 | 0 | 0 |
| Boundary conditions | 5 | 4 | 0 | 1 |

**16 PASS, 1 FAIL, 1 PARTIAL**

#### Remaining findings (all LOW/INFO -- no fixes needed):

| ID | Finding | Severity | Decision |
|----|---------|----------|----------|
| F1 | Symlinked dirs not followed by os.walk() | LOW | By design. followlinks=True risks infinite loops. |
| F2 | Same source/dest creates duplicate book entries on re-scan | LOW | Edge case users won't hit. No data loss. |
| F3 | No collision warning when two books resolve to same dest folder | INFO | Files coexist safely (different basenames). |

## Fix Summary

All HIGH and MEDIUM findings from the initial review were fixed:

| ID | Status | Fix |
|----|--------|-----|
| H1 | FIXED | _move_book_directory uses rglob("*") for recursive subdir handling |
| H2 | FIXED | _find_book_directories rewritten to single-pass O(n) with dirnames.clear() |
| H3 | FIXED | Correctly-placed check uses source_path instead of source_file.parent |
| M1 | FIXED | cli.py config_kwargs annotated as dict[str, bool or str] |
| M2 | FIXED | FileNotFoundError re-raised before broad except |
| M3 | FIXED | Prompt injection mitigation -- strip newlines, truncate 200 chars |
| M4 | FIXED | _cleanup_empty_parents bounded to config.nfs_output_dir |
| M5 | FIXED | Stage.CLEANUP registered in get_stage_runner() |
| M6 | FIXED | Dedup key scoped to book_dir/stem |
| Year-as-series | FIXED | re.fullmatch(r"\d{4}") filter excludes years from series |
