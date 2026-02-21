# Project Research Summary

**Project:** Audiobook Pipeline
**Domain:** Media processing pipeline (MP3 to M4B conversion, metadata enrichment, Plex organization)
**Researched:** 2026-02-20
**Confidence:** HIGH

## Executive Summary

This is a CLI-driven media processing pipeline that converts downloaded MP3 audiobooks into chaptered, tagged M4B files organized for Plex/Prologue consumption. The domain is well-understood -- tools like m4b-tool, tone, and ffmpeg are mature, and community conventions (seanap's Plex-Audiobook-Guide) provide a clear target format. The recommended approach is a stage-based Bash pipeline with 7 discrete stages (ingest, merge, chapter, convert, tag, organize, archive), each idempotent and resumable via a per-book manifest file. All processing happens on local disk; only the final output goes to NFS.

The stack is straightforward: ffmpeg for audio concat/encoding, tone CLI for metadata tagging (single binary, no dependencies), Audnexus API for metadata lookup (free, no auth), and mp4chaps for chapter embedding. Bash is the right orchestration language -- the pipeline is fundamentally a sequence of CLI tool invocations. The target output is 64kbps mono AAC in M4B containers, which matches Audible's own encoding and produces ~280MB per 10-hour book.

The biggest risks are: (1) chapter markers silently dropped during ffmpeg concat if you don't explicitly generate and inject a chapters metadata file, (2) Audible metadata matching returning the wrong book when searching by title/author (fuzzy matching is unreliable for short titles and series entries), and (3) NFS cross-device moves being non-atomic, which can corrupt output if interrupted. All three have clear mitigations documented in the pitfalls research. The ASIN discovery problem -- how to find the Audible ASIN for a given audiobook -- is the biggest open question and should be addressed in the metadata phase.

## Key Findings

### Recommended Stack

The stack is all CLI tools with zero runtime dependencies beyond what's already on LXC 210. No Python, no PHP, no Docker required for the core pipeline.

**Core technologies:**
- **ffmpeg 6.1.1** -- MP3 concatenation, AAC-LC encoding, M4B muxing. Already installed on LXC 210.
- **tone v0.2.5** -- M4B metadata tagging (title, author, narrator, series, cover, chapters). Single static binary by sandreas (m4b-tool author). Successor to m4b-tool for pure tagging.
- **Audnexus API** -- Audible metadata lookup (book info, chapter timestamps with millisecond precision). Free, no auth for book data. Requires ASIN as input -- no free-text search.
- **mp4chaps (mp4v2-utils)** -- Chapter embedding into M4B without re-muxing. Simpler than ffmpeg's chapter metadata approach for post-hoc insertion.
- **Bash 5.x** -- Pipeline orchestration. Matches existing scripts, no runtime dependencies.

**Critical version/config notes:**
- tone must operate on local block storage, not NFS mounts (causes issues)
- AAC-LC at 64kbps mono is the target -- matches Audible, universal player compatibility, avoid HE-AAC
- `-movflags +faststart` is mandatory on all ffmpeg M4B output (enables seeking)

**Resolved contradiction:** STACK.md suggested 128k bitrate floor, but FEATURES.md correctly identifies 64kbps mono AAC as the audiobook sweet spot (equivalent quality to 128kbps MP3 for speech). Go with 64kbps mono unless source is <= 64kbps MP3 (in which case, don't transcode -- lossy-to-lossy at that bitrate causes audible degradation).

### Expected Features

**Must have (table stakes):**
- MP3-to-M4B conversion (single and multi-file)
- Chapter markers (from file boundaries for multi-MP3, Audnexus for known ASINs)
- Metadata tagging (title, author, narrator, series, cover art)
- Plex-compatible folder structure (`Author/Series/## Title (Year)/Title.m4b`)
- Companion files (cover.jpg, desc.txt, reader.txt alongside M4B)
- Idempotent processing (skip already-processed books)

**Should have (differentiators):**
- Audible metadata matching via Audnexus API
- Bitrate-aware transcoding (don't upscale low-bitrate sources)
- Automatic trigger from Readarr/Bookshelf post-import hook
- Backup of original files before conversion
- Dry-run mode for previewing actions
- Processing status/history log

**Defer (v2+):**
- Web UI (use CLI; Audiobookshelf exists for GUI management)
- Multi-region Audible search (start with .com only)
- MusicBrainz chapter lookup (too niche)
- Silence-based chapter detection for unknown books (complex, unreliable)
- Notification system (add after core pipeline is stable)

### Architecture Approach

Stage-based sequential pipeline with per-book parallelism (MAX_JOBS=2). Each book flows through 7 stages with a manifest tracking progress. Failed books retry up to 3 times, then move to a `failed/` directory for manual investigation. Concurrency controlled by `flock` -- no PID files, no race conditions.

**Major components:**
1. **pipeline.sh** -- Queue manager, job dispatch, flock-based single instance
2. **Stage scripts (01-07)** -- Ingest, merge, chapter, convert, tag, organize, archive. Each takes a job directory, returns 0/non-zero. Idempotent.
3. **readarr-hook.sh** -- Thin shim that drops a trigger file and exits fast (Readarr has 30s timeout)
4. **lib/** -- Shared functions: logging (structured k=v), manifest I/O, Audible API helpers, ffmpeg helpers, filename sanitization
5. **config.env** -- All configurable paths, limits, defaults

**Key architectural decisions already made:**
- Work directory on local disk (`/var/lib/audiobook-pipeline/work/`), NOT on NFS
- Source files never deleted until archive stage confirms valid output
- Cron scan (every 15 min) as catch-all trigger alongside Readarr hook
- No inotifywait -- does not work on NFS (kernel limitation)

**Key decision still needed:** Whether to use ffmpeg chapters (FFMETADATA1 format, requires re-mux) or mp4chaps (Nero format, post-hoc injection without re-mux) or tone's `--auto-import=chapters` for chapter embedding. Recommendation: generate chapters.txt in Nero format, use tone to import during the tagging stage -- one tool handles both tags and chapters.

### Critical Pitfalls

1. **Chapter markers lost during concat** -- ffmpeg silently drops chapters unless you generate a FFMETADATA1 file from individual MP3 durations and inject it via `-map_metadata`. Validate with `ffprobe -show_chapters` post-conversion. This is the #1 cause of unusable audiobooks.

2. **NFS root squash and cross-device moves** -- `mv` from local disk to NFS falls back to copy+delete (non-atomic). Run pipeline as readarr user (UID 2018), never as root. Use `install -o readarr -g media` for final file placement. Verify NFS export permissions.

3. **Wrong Audible metadata match** -- Fuzzy title search returns wrong book for short titles, series entries, omnibus editions. Require ASIN when available, use multi-field matching (title + author + narrator), add confidence scoring, log all match decisions for audit.

4. **Bash word splitting on filenames** -- Audiobook filenames always have spaces. Quote every variable, use arrays for file lists, run ShellCheck on all scripts. This will bite immediately if missed.

5. **Resource exhaustion on large books** -- 40-hour audiobooks need 3x input size in temp space (input + output + faststart rewrite). Pre-flight disk check before conversion. Set MAX_JOBS=2 on 8GB RAM to prevent OOM.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: Core Conversion Pipeline
**Rationale:** Get a working end-to-end pipeline before adding metadata enrichment. Conversion is the foundation -- everything else layers on top.
**Delivers:** CLI tool that takes a directory of MP3s and outputs a chaptered M4B in the correct Plex folder structure.
**Addresses:** MP3-to-M4B conversion, multi-file merge, file-per-chapter detection, basic folder organization, bitrate-aware encoding
**Avoids:** Chapter loss (Pitfall 1) by generating chapters from MP3 file durations. Word splitting (Pitfall 4) by quoting everything and running ShellCheck.
**Stack:** ffmpeg, mp4chaps or tone for chapters, config.env, stage scripts 01-04 + 06
**Research needed:** No -- well-documented patterns from m4b-tool and ffmpeg guides

### Phase 2: Metadata Enrichment
**Rationale:** Once conversion works, add rich metadata from Audnexus. This is what separates "renamed M4A" from "proper audiobook" -- cover art, narrator, series info, descriptions.
**Delivers:** Audnexus API integration, tone-based tagging, cover art download, companion file generation (desc.txt, reader.txt, cover.jpg)
**Addresses:** Audible metadata matching, cover art embedding, series organization, companion files
**Avoids:** Wrong book matching (Pitfall 3) by implementing multi-field matching with confidence scoring and manual ASIN override.
**Stack:** Audnexus API, tone CLI, curl, jq
**Research needed:** YES -- ASIN discovery strategy needs validation. How does Readarr/Bookshelf store ASINs? Can we extract them from the API? This is the biggest open question.

### Phase 3: Automation and Triggers
**Rationale:** Manual CLI works but defeats the purpose. Add automatic triggers after the pipeline is proven reliable.
**Delivers:** Readarr/Bookshelf post-import hook, cron-based folder scan fallback, idempotent processing (skip already-done), flock concurrency control
**Addresses:** Automatic trigger, idempotent processing, queue management, error recovery with retry/failed directory
**Avoids:** Race conditions (Pitfall 12) with flock and file stability checks. NFS issues (Pitfall 2) by running as readarr user with proper permissions.
**Stack:** Bash, flock, cron, systemd (optional for service mode)
**Research needed:** Moderate -- Readarr/Bookshelf hook env vars are documented but `readarr_addedbookpaths` is undocumented and may not always populate. Need to test on actual Bookshelf install.

### Phase 4: Hardening and Operations
**Rationale:** Once the pipeline runs automatically, add operational tooling for monitoring, debugging, and handling edge cases.
**Delivers:** Structured logging, processed.log audit trail, failed book handling (3-strike rule), dry-run mode, pre-flight disk space checks, logrotate config
**Addresses:** Processing status/history, backup originals, notifications, resource exhaustion protection
**Avoids:** Resource exhaustion (Pitfall 8) with pre-flight checks. Signal handling (Pitfall 10) with background ffmpeg + trap pattern.
**Stack:** Bash logging lib, logrotate, optional notification webhook
**Research needed:** No -- standard operational patterns

### Phase Ordering Rationale

- **Phase 1 before Phase 2:** Conversion must work before metadata matters. A chaptered M4B with no Audible metadata is useful; metadata without a valid M4B is worthless.
- **Phase 2 before Phase 3:** Validate metadata enrichment manually before automating it. Bad metadata applied automatically is worse than no metadata.
- **Phase 3 before Phase 4:** Get the automation running, then harden it. You need real failure data to build good error handling.
- **Each phase is independently deployable:** Phase 1 alone is a useful CLI tool. Each subsequent phase adds value without breaking previous functionality.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2:** ASIN discovery is the biggest gap. The Audnexus API requires an ASIN but offers no text search. Need to validate Readarr/Bookshelf API for ASIN extraction, or implement audible-cli fallback.
- **Phase 3:** Bookshelf (Readarr replacement, retired June 2025) hook behavior needs testing. The `readarr_addedbookpaths` env var is undocumented and reportedly unreliable on manual imports.

Phases with standard patterns (skip research-phase):
- **Phase 1:** ffmpeg concat, AAC encoding, and chapter generation are thoroughly documented. The existing scripts provide a starting point.
- **Phase 4:** Logging, error handling, and operational hardening follow standard Bash patterns. No domain-specific research needed.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All tools verified via GitHub releases and live API calls. tone and ffmpeg are mature, well-documented. |
| Features | HIGH | seanap's Plex guide (1.7k stars) provides authoritative feature list. Audnexus API verified with live calls. |
| Architecture | HIGH | Stage-based pipeline is the consensus pattern across m4b-tool, m4b-merge, and auto-m4b. Existing scripts validate the approach. |
| Pitfalls | HIGH | All critical pitfalls verified across multiple sources. NFS and chapter issues are extremely well-documented. |

**Overall confidence:** HIGH

### Gaps to Address

- **ASIN discovery:** Audnexus requires ASIN but offers no search. Readarr/Bookshelf likely stores ASINs but this needs validation against a live Bookshelf instance. Fallback: manual `.asin` file alongside audiobook, or audible-cli Python tool.
- **Bookshelf vs Readarr:** Readarr retired June 2025. Bookshelf fork inherits the API but may have differences. Need to verify hook behavior on the actual installed version.
- **Bitrate handling for edge cases:** The "don't transcode <= 64kbps sources" rule needs a code path that copies MP3 into M4B container without re-encoding (`-c:a copy` only works if source is AAC). Low-bitrate MP3s may need to stay as MP3 or accept quality loss.
- **Chapter format decision:** Three options (FFMETADATA1, Nero/mp4chaps, tone auto-import). Research recommends tone auto-import for simplicity, but this needs testing to confirm it handles all edge cases (long books, many chapters, Unicode titles).

## Sources

### Primary (HIGH confidence)
- [tone CLI - sandreas/tone](https://github.com/sandreas/tone) -- metadata tagging, chapter import, installation
- [Audnexus API - laxamentumtech/audnexus](https://github.com/laxamentumtech/audnexus) -- metadata lookup, chapter data (verified with live API calls)
- [m4b-tool - sandreas/m4b-tool](https://github.com/sandreas/m4b-tool) -- merge/chapter patterns, architecture reference
- [seanap/Plex-Audiobook-Guide](https://github.com/seanap/Plex-Audiobook-Guide) -- folder structure, Plex compatibility (1.7k stars)
- [ffmpeg documentation](https://ffmpeg.org/documentation.html) -- concat, AAC encoding, chapter metadata
- [flock(2) Linux Manual](https://man7.org/linux/man-pages/man2/flock.2.html) -- concurrency control
- [BashPitfalls - wooledge.org](https://mywiki.wooledge.org/BashPitfalls) -- shell scripting pitfalls

### Secondary (MEDIUM confidence)
- [Servarr Wiki - Readarr Custom Scripts](https://wiki.servarr.com/readarr/custom-scripts) -- hook env vars (Readarr retired, Bookshelf inherits)
- [m4b-merge - djdembeck](https://github.com/djdembeck/m4b-merge) -- Audible metadata pipeline patterns
- [Hydrogenaudio forums](https://hydrogenaudio.org/index.php/topic,32153.0.html) -- AAC bitrate recommendations
- [audtag - jeffjose](https://github.com/jeffjose/audtag) -- auto-search and parallel processing patterns

### Tertiary (LOW confidence)
- [Audible external API docs (community)](https://audible.readthedocs.io/en/latest/misc/external_api.html) -- undocumented API, can break without notice
- [Chapterize-Audiobooks](https://github.com/patrickenfuego/Chapterize-Audiobooks) -- ML chapter detection (appears unmaintained, deferred)

---
*Research completed: 2026-02-20*
*Ready for roadmap: yes*
