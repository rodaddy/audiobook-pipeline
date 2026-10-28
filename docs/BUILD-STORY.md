# How audiobook-pipeline was built

This is the build history of audiobook-pipeline, taken from its git log (86
commits, 2026-02-20 to 2026-08-04), `CHANGELOG.md`, `VERSION`, the test
suite, and the project's issue tracker and pull requests. This public
repository is a one-commit snapshot of the v1.0.0 code.

## The problem

Audiobooks arrive as folders of MP3s with inconsistent names, no chapters
and no reliable metadata. Media servers such as Plex and Audiobookshelf then
guess, and guess wrong: wrong edition, wrong author folder, series split in
two. The goal was a pipeline that turns a download into one tagged,
chaptered M4B filed where a media server will match it, without a person
fixing each book by hand.

## Timeline

| Date | Phase | What changed |
|---|---|---|
| 2026-02-20 | Plan, then build in Bash | Requirements, research notes and a four-phase roadmap were written first. Then, in one day: conversion (MP3 merge, chapters, AAC, M4B mux), metadata enrichment (ASIN discovery, Audnexus client with caching, tagging), Plex-style folder organisation with an archive stage behind an M4B validation gate, and automation (a fast-exit download-manager webhook, a cron fallback scanner, a queue, a global lock, disk-space checks, retry tracking, logrotate). Each phase ended with a verification report. |
| 2026-02-20 | Integration test on macOS | Six bugs fixed, among them missing `flock` on macOS, unbound variables under `set -u`, config defaults overriding CLI flags, and ISBN-10 ASINs being rejected. |
| 2026-02-21 | First public release | Four modes (convert, enrich, metadata, organize) with auto-detection, multi-format input, the Audible catalogue API as the primary metadata source, community files and CI with shellcheck. |
| 2026-02-23 | v0.5.0, Python rewrite | The shell pipeline was rewritten in Python: Pydantic settings, Click CLIs, SQLite state, Loguru logging, four pipeline levels (simple, normal, ai, full), CPU-aware parallel conversion. 417 tests, mypy checked. |
| 2026-02-24 | Library diff | `audiobook-audit --diff` compares a source folder with a library: multi-part collapsing, author normalisation, fuzzy title matching. Tested against a real library, it cut false "missing" reports from 505 to 11, and each of the 11 was explained. |
| 2026-08-04 | v1.0.0 | Correctness, portability and first-run fixes found by reviewing and then running the tagged code (details below). 899 tests. |

Work continued after this snapshot in the source repository: in September
2026 a release added embedded ASIN, series and publisher atoms written with
mutagen (ffmpeg drops freeform atoms), a local cover, a `metadata.json`
sidecar and create-only publishing. That release was prompted by a media
server's bulk match rewriting 646 library items with wrong editions, because
no file carried an ASIN.

## Key design decisions

- **Plan before code.** Every phase started with a written research note and
  plan, and ended with a verification report against the requirements.
- **Stages with a manifest.** Each book has a manifest; each stage is
  idempotent and records its result, so a failed run resumes instead of
  starting over. The Python version keeps this as resumable SQLite state.
- **Never destroy the source.** Archiving is gated on validating the M4B,
  and later releases copy and verify instead of moving.
- **AI only where the catalogue is ambiguous.** The `ai` and `full` levels
  ask a model to choose between catalogue candidates; everything else is
  deterministic. Remote chapter data that disagrees with the local audio
  duration is rejected.
- **Rewrite rather than patch.** After three days the Bash version worked
  but was hard to test. The Python rewrite traded it for typed models and a
  real test suite.

## How AI was used to build and check it

Most commits were written with Claude Opus as a pair programmer (58
co-author trailers). Review was done by separate agent reviewers: the
history records fixes "caught by shadow reviewer" and "caught by integration
reviewer" during the Bash build. The v1.0.0 pass was a review followed by
real runs; every fix in it carries a test proven to fail against the
original code. Issues filed in July 2026 tracked the optional AI backend
itself: enforcing a timeout, verifying fallbacks between model CLIs, and
merging two model calls into one to cut latency.

## Testing

- 417 tests at the Python rewrite (2026-02-23), 899 at v1.0.0
  (2026-08-04), with mypy and Ruff clean.
- v1.0.0 was also verified end to end on real audiobooks: eight MP3s joined,
  matched in the Audible catalogue, eight chapters fetched, and a valid
  2h08m M4B produced with the full Plex tag set. Resume, audit and diff exit
  codes were checked on real, messy filenames.
- Secret scanning runs in CI over full history.

## What did not work

- The library diff reported unconverted books as converted, the one
  direction it must never fail in: titles were deduplicated without the
  author, so two authors with the same title collapsed into one entry.
  Fixed in v1.0.0 with author-scoped matching and a franchise fallback.
- A `~` in a config path was never expanded, so the tool created a folder
  literally named `~`.
- A first run crashed because working directories were never created.
- Folder reuse checked `exists()`, which cannot answer the question on a
  case-insensitive volume.
- Known and not fixed: the Audible catalogue returns inconsistent series
  names for at least one series, splitting it across two folders. Choosing
  the canonical name is a judgement call, not a code fix.

## Current state

v1.0.0 is this snapshot. The tool is in personal use against a real
library; development is occasional.
