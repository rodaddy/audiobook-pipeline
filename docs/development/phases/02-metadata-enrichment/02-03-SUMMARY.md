---
phase: 02-metadata-enrichment
plan: 03
status: completed
commits: [43657a7, 94a7e71]
---

# 02-03 Summary: Tone CLI Tagging Stage

## What was built

### lib/metadata.sh (178 lines)
Tone CLI wrapper library with 5 functions:
- `tag_m4b()` -- Build tone tag argument array conditionally, single invocation
- `verify_metadata()` -- tone dump with --include-property to avoid binary data
- `convert_chapters_to_tone()` -- jq conversion of startOffsetMs to HH:MM:SS.mmm
- `generate_companions()` -- Write desc.txt and reader.txt companion files

### stages/06-metadata.sh (154 lines)
Metadata enrichment stage orchestrating:
1. METADATA_SKIP check
2. M4B location from manifest
3. ASIN read from manifest (set by stage 05)
4. Book JSON from cache (with fallback fetch)
5. Cover art download (non-fatal)
6. Chapter duration validation + conversion (non-fatal)
7. tone tag invocation (fatal on failure)
8. Metadata verification (non-fatal)
9. Companion file generation
10. Manifest update with enrichment details

### Pipeline wiring
- bin/audiobook-convert: sources audnexus.sh + metadata.sh, STAGE_MAP[metadata]="06"
- STAGE_ORDER: (validate concat convert asin metadata cleanup)
- lib/manifest.sh: metadata stage in schema + get_next_stage loop
- config.env.example: METADATA_SKIP, FORCE_METADATA vars

## Graceful degradation
- No ASIN: stage completes with enriched=false
- API unavailable: stage completes with enriched=false
- Cover art fails: continues without cover
- Chapter duration mismatch: keeps file-boundary chapters
- Only tone tag failure causes stage failure (return 1)

## Deviations from plan
- download_cover_art and validate_chapter_duration not duplicated in lib/metadata.sh -- correctly reused from lib/audnexus.sh via source
- Used awk (not bc) consistent with lib/audnexus.sh portability pattern
