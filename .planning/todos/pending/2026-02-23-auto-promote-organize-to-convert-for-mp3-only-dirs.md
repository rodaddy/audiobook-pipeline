---
created: 2026-02-23T04:46:59.953Z
title: Auto-promote organize to convert for MP3-only dirs
area: general
files:
  - src/audiobook_pipeline/runner.py
  - src/audiobook_pipeline/stages/organize.py
  - src/audiobook_pipeline/stages/metadata.py
---

## Problem

In reorganize batch runs, directories containing only MP3/FLAC files (no .m4b) fail at the metadata stage because there's no m4b to tag. The organize pipeline only runs `asin -> metadata -> organize`, but MP3-only dirs need the full convert pipeline first.

Discovered during Robin Hobb test -- `02 Short Pieces/*` subdirs have unconverted MP3s that the reorganize can't process.

## Solution

In `runner.py` `_run_single()` (or during batch directory scanning), detect when a source dir has convertible audio files but no .m4b. Auto-promote those directories from organize mode to the full convert pipeline (`validate -> concat -> convert -> asin -> metadata -> organize -> cleanup`) instead of just the organize stages. This should be transparent to the user -- just log the promotion.
