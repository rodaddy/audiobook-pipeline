# stages

Stage registry -- maps Stage enum values to run functions.

Stages:
    validate -- Verify source directory of audio files and prepare conversion
                metadata. Discovers audio files (excludes .m4b since we convert
                TO m4b), validates with ffprobe, natural-sorts by filename,
                checks disk space, detects target bitrate from first file,
                sums total duration, creates work_dir, writes audio_files.txt
                with absolute paths. Updates manifest with target_bitrate,
                file_count, total_duration. Supports dry_run.
    concat -- Generate ffmpeg input files from validated audio list. Creates
              files.txt (concat demuxer format with escaped paths) and
              metadata.txt (FFMETADATA1 chapter markers with cumulative
              timestamps). For single-file books, writes metadata header only.
              Updates manifest with chapter_count. Handles path escaping for
              single quotes in filenames. Uses ffprobe to get duration for
              each audio file.
    convert -- Wraps ffmpeg MP3-to-M4B conversion as subprocess. Auto-detects
               aac_at (Apple AudioToolbox) encoder, falls back to aac. Validates
               output codec, format, and chapter count (for multi-file books).
               Supports dry-run mode and thread control via kwargs. Updates
               manifest with output_file, codec, and bitrate.
    organize -- Copy/move audiobooks to Plex-compatible NFS library. Accepts
                optional LibraryIndex for O(1) batch lookups and reorganize flag
                for in-place library cleanup (move instead of copy). Logs audio
                file discovery, Audible search strategies, AI resolution decisions,
                cross-source dedup, correctly-placed detection, and
                progress bar with ETA for batch operations. Supports
                batch and single-file modes.

---
*Auto-generated from `__init__.py` docstring by `scripts/gen-readme.py`.*
