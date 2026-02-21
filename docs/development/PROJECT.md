# Audiobook Pipeline

## What This Is

An automated audiobook processing pipeline that converts MP3 audiobooks to M4B format with proper chapters, enriches them with metadata from Audible (title, narrator, series, cover art, description), and organizes them into a Plex-compatible folder structure (`Author/Series/## Title (Year)/`). Runs on LXC 210 (media-automation) alongside Radarr/Sonarr/Readarr, triggered by Readarr post-import hooks, cron scans, or manual runs.

## Core Value

Downloaded audiobooks are automatically converted, tagged, and organized into the correct Plex library structure without manual intervention.

## Requirements

### Validated

(None yet -- ship to validate)

### Active

- [ ] Convert MP3 audiobooks to single M4B files with chapter markers
- [ ] Support both single-MP3 and multi-MP3 (chapter-per-file) audiobooks
- [ ] Match source audio bitrate for AAC encoding, with 128k floor
- [ ] Auto-detect chapters via silence detection for single-file audiobooks
- [ ] Tag M4B files with metadata from Audible (title, author, narrator, series, series position, year, cover art, description, genre)
- [ ] Organize output into Plex-compatible structure: Author/Series/## Title (Year)/file.m4b
- [ ] Handle books without series (Author/Title (Year)/)
- [ ] Readarr post-import webhook trigger
- [ ] Cron-based periodic scan of staging directory
- [ ] Manual CLI trigger for ad-hoc processing
- [ ] Logging of all operations with success/failure tracking
- [ ] Move processed originals to archive directory (don't delete)

### Out of Scope

- GUI or web interface -- CLI/automated only
- Audible account integration or DRM removal -- only metadata scraping
- Re-processing existing library -- pipeline is for new acquisitions
- Multi-format output (only M4B)
- GPU acceleration -- CPU-only tools are sufficient for audio
- Docker/container packaging -- direct install on LXC 210

## Context

- **Existing partial work:** `/Volumes/ThunderBolt/AudioBookStuff/AudioBooks_to_fix/` contains conversion scripts (process_audiobooks.sh, convert_to_m4b.sh), a Python chapterizer (Chapterize-Audiobooks with silence detection model), and test data. The conversion mostly worked but metadata tagging was manual (Mp3tag + Audible web lookup) which killed the workflow.
- **Readarr staging:** Readarr is configured with root folder `/mnt/media/AudioBooks/_incoming` on LXC 210. Downloads land there via qBittorrent (category: readarr).
- **Plex library:** `/mnt/media/AudioBooks/` on TrueNAS NFS, mounted on LXC 210. ~60 authors, structure: `Author/Series/## Title (Year)/file.m4b`. Served via Plex with Prologue client for playback.
- **Available tools on LXC 210:** ffmpeg 6.1.1, ffprobe, mediainfo. Need to install: tone CLI (metadata tagger), possibly update Chapterize-Audiobooks.
- **Audio quality policy:** Match source bitrate (e.g., 128k MP3 -> 128k AAC, 320k MP3 -> 256k AAC). Never encode below 128k AAC floor regardless of source quality.

## Constraints

- **Runtime:** LXC 210 (media-automation), Ubuntu 24.04, 8GB RAM, no GPU
- **Stack:** Bash scripts + ffmpeg + tone CLI. No heavy frameworks.
- **File permissions:** readarr user (UID 2018, GID 2000 media group). All file operations must run as this user.
- **NFS:** Media files on TrueNAS NFS mount at /mnt/media/. Root squash active -- must use readarr user, not root.
- **Shebang:** `#!/usr/bin/env bash` (LAW 7)
- **Existing layout:** Must match `Author/Series/## Title (Year)/` structure exactly. Plex + Prologue depend on this.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| tone CLI for metadata | Can scrape Audible, write directly to M4B, no GUI needed | -- Pending |
| Bash over Python for orchestration | Consistency with existing scripts, simpler deployment on LXC | -- Pending |
| Silence detection for chapters | Existing Chapterize-Audiobooks tool uses this approach | -- Pending |
| Match source bitrate with 128k floor | Best quality without bloat, never degrade below listenable | -- Pending |
| Readarr staging folder (_incoming) | Already configured, keeps pipeline decoupled from Readarr internals | -- Pending |

---
*Last updated: 2026-02-20 after initialization*
