# Session History

Chronological log of all development sessions.

## 2026-02-21 -- Organize Edge Cases and Dedup (35ab4a9a)

**Branch:** feat/python-rewrite
**Type:** Bug fixes, edge case hardening, E2E testing

Continued Python rewrite recovery. Fixed multiple organize edge cases discovered during live testing: hardened author heuristics, added Pattern G for bracket positions, parenthesized series extraction, title cleanup, position normalization, duplicate folder detection. Widened Audible search when AI available. Live test: 65/65 books organized with no duplicates. Pre-commit hooks generating requirements.txt. Known cosmetic issues documented.

**Commits:** a4e878c, 0364edc, 41cac27

**Details:** `.reports/sessions/35ab4a9a-42cc-48ec-8299-b8aeaab23053.md`

---

## 2026-02-21 -- Audiobook Pipeline Python Restructure (session-2026-02-21-restructure)

**Branch:** feat/python-rewrite (parent), main (submodule)
**Type:** Major restructure

Restructured audiobook pipeline from legacy Python scripts to proper app. Added loguru logging, openai SDK for AI, consolidated api/ layer (audible.py, search.py). Completed tasks 1-7 of 13-task plan. Tasks 9-13 remain (cli.py update, runner.py loguru migration, pre-commit hooks, python/ deletion, verification).

**Details:** `.reports/sessions/session-2026-02-21-restructure.md`

---

## 2026-02-21 -- Fix Reorganize Pipeline (f681e9e3)

### Done
- Fixed parse_path() directory context loss via synthetic paths
- Added graceful ffprobe failure handling (empty/corrupt audio files)
- Added source_directory parameter to AI resolve() for better evidence
- Fixed LiteLLM semantic cache staleness via extra_body cache bypass
- Fixed cleanup.py type error (update_stage -> set_stage with enums)
- Fixed type-check hook (snake_case fields, process.exit(2) blocking)
- Created test sandbox, validated 9/9 acceptance criteria
- Wrote validation report to .reports/reorganize-pipeline.md

### Decisions
- parse_path() receives synthetic path `source_path / source_file.name` to preserve hierarchy
- LiteLLM cache bypass uses `extra_body={"cache": {"no-cache": True}}`
- Type-check hook uses process.exit(2) + stderr (matching security-validator)
- Claude Code hook input is snake_case (tool_name/tool_input), not camelCase

### Files Changed
- src/audiobook_pipeline/stages/organize.py -- parse_path fix, ffprobe handling, source_directory
- src/audiobook_pipeline/ai.py -- source_directory param, cache bypass, debug logging
- src/audiobook_pipeline/runner.py -- batch processing refactor
- src/audiobook_pipeline/stages/__init__.py, cleanup.py -- type error fixes
- ~/.claude/hooks/safety/pre-bash-type-check.ts -- snake_case fields, exit blocking
- .reports/reorganize-pipeline.md -- validation report

### Known Issues
None. All 9 acceptance criteria met.

**Commit:** 8a43b5e

---

## 2026-02-22 -- PR Creation (f681e9e3 continuation)

**Branch:** feat/python-rewrite
**Type:** Git workflow -- push and PR creation

Continuation from context compaction. Pushed feat/python-rewrite branch to origin (13 commits ahead) and created PR #5 with comprehensive test plan. All adversarial review findings (H1-H3, M1-M8) fixed in compacted session (78096ea). PR includes 18 commits, 76 files changed, ~10,400 lines added. Ready for review.

**PR:** https://github.com/rodaddy/audiobook-pipeline/pull/5

**Details:** `.reports/sessions/f681e9e3-032e-4521-aa4e-ee15eeb2ea33.md`

---

## 2026-02-22 -- Convert Mode with CPU-Aware Parallelism (ce5c2584)

**Branch:** feat/python-rewrite
**Type:** Feature -- convert mode implementation

Implemented full convert pipeline (validate, concat, convert stages) wrapping ffmpeg/ffprobe, plus ConvertOrchestrator for CPU-aware parallel batch processing. Fixed CPU monitoring (psutil blocking 1s sample) and dry-run flow bugs. Launched batch conversion of 142 books from Original/ at ~60-70% CPU with 4 workers. Created 26 tests covering all new stages. Three commits: initial implementation, psutil monitoring fix, conservative worker tuning.

**Commits:** 99545f5, 4c31442, 5e0a0d4

**Details:** `.reports/sessions/ce5c2584-153c-45d0-b4d5-dd6e798f111b.md`
