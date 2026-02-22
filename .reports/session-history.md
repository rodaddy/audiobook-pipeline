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
